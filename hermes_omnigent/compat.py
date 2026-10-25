"""Hermes model/profile support for this plugin's private Omnigent 0.16."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sqlite3
import time


def retained_hermes_state(bridge_dir):
    from omnigent.harnesses.hermes_native.bridge import read_hermes_home
    from omnigent.harnesses.hermes_native.forwarder import _read_state
    state = _read_state(bridge_dir)
    if not state.hermes_session_id:
        return None
    home = read_hermes_home(bridge_dir)
    if home is None:
        raise RuntimeError('HERMES_RESUME_STATE_INVALID')
    try:
        connection = sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True, timeout=1)
        try:
            exists = connection.execute('SELECT 1 FROM sessions WHERE id = ?', (state.hermes_session_id,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise RuntimeError('HERMES_RESUME_STATE_INVALID') from None
    if not exists:
        raise RuntimeError('HERMES_RESUME_STATE_INVALID')
    return state


def resume_hermes_args(args, session_id):
    # Hermes 0.22 resume retains the session's policy/model and rejects creation
    # overrides. Drop only the model launch selector; later model changes use
    # the normal /model command through the session event API.
    result = []
    iterator = iter(args)
    for arg in iterator:
        if arg == '--model':
            next(iterator, None)
        elif not arg.startswith('--model='):
            result.append(arg)
    if '--resume' not in result and not any(x.startswith('--resume=') for x in result):
        result.extend(['--resume', session_id])
    return result


def await_hermes_message(db_path, baseline_id, content, timeout):
    """Confirm the submitted user text, never an unrelated assistant/control row."""
    expected = content.replace('\r\n', '\n').replace('\r', '\n').strip()
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1)
            try:
                rows = connection.execute("SELECT content FROM messages WHERE id > ? AND role = 'user' ORDER BY id LIMIT 1000", (baseline_id,))
                if any(isinstance(row[0], str) and row[0].replace('\r\n', '\n').strip() == expected for row in rows):
                    return True
            finally:
                connection.close()
        except sqlite3.Error:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(.2)


def stop_hermes_terminal(info):
    if info is None:
        return
    args = ['tmux', '-S', info['socket_path']]
    def absent(result):
        return result.returncode == 1 and (not Path(info['socket_path']).exists()
            or "can't find session:" in result.stderr or 'no server running on' in result.stderr)
    try:
        probe = subprocess.run([*args, 'has-session', '-t', info['tmux_target']],
                               capture_output=True, text=True, timeout=5)
        if absent(probe):
            return
        if probe.returncode:
            raise RuntimeError('HERMES_TERMINAL_STOP_FAILED')
        result = subprocess.run([*args, 'kill-session', '-t', info['tmux_target']],
                                capture_output=True, text=True, timeout=5)
        if result.returncode and not absent(result):
            raise RuntimeError('HERMES_TERMINAL_STOP_FAILED')
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('HERMES_TERMINAL_STOP_FAILED') from None


def native_gateway(action, home):
    if home is None:
        return
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    if env.get('HERMES_OMNIGENT_PYTHONPATH'):
        env['PYTHONPATH'] = env['HERMES_OMNIGENT_PYTHONPATH']
    result = subprocess.run([env['HERMES_OMNIGENT_PYTHON'],
                             str(Path(__file__).with_name('native_gateway.py')), action, str(home)],
                            env=env, capture_output=True, text=True, timeout=150)
    if result.returncode or len(result.stdout) > 4096 or json.loads(result.stdout).get('ok') is not True:
        raise RuntimeError('HERMES_GATEWAY_FAILED')


def hermes_models():
    python = os.environ['HERMES_OMNIGENT_PYTHON']
    script = Path(__file__).with_name('models.py')
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    if env.get('HERMES_OMNIGENT_PYTHONPATH'):
        env['PYTHONPATH'] = env['HERMES_OMNIGENT_PYTHONPATH']
    process = subprocess.run([python, str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=45, env=env)
    if process.returncode or len(process.stdout) > 262144:
        raise RuntimeError('HERMES_MODELS_UNAVAILABLE')
    rows = json.loads(process.stdout)
    if not isinstance(rows, list):
        raise RuntimeError('HERMES_MODELS_UNAVAILABLE')
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_.:\-]{0,200}', row['id'])]


async def change_model(session_id, model):
    from starlette.responses import JSONResponse, Response
    if model is None:
        return Response(status_code=204)
    allowed = {row['id'] for row in await asyncio.to_thread(hermes_models)}
    if model not in allowed:
        return JSONResponse({'error': 'invalid_model'}, status_code=400)
    from omnigent.harnesses.hermes_native.bridge import bridge_dir_for_session_id, inject_user_message
    try:
        await asyncio.to_thread(inject_user_message, bridge_dir_for_session_id(session_id), content='/model ' + model)
    except Exception:
        return JSONResponse({'error': 'hermes_model_change_failed'}, status_code=409)
    return Response(status_code=204)


def install_private(root, plugin_root, python):
    """Patch only the managed SDK; exact anchors fail closed on other versions."""
    from .runtime import Failure, write
    import hashlib
    import importlib.metadata
    import shutil
    if importlib.metadata.version('omnigent') != '0.16.0':
        raise Failure('OMNIGENT_COMPAT_VERSION_UNSUPPORTED')
    import omnigent
    sdk = Path(omnigent.__file__).resolve().parent
    root = Path(root).resolve()
    if not sdk.is_relative_to(root / 'uv/tools'):
        raise Failure('COMPAT_REQUIRES_PRIVATE_RUNTIME')
    from . import patches
    from .runtime import read
    previous = read(root / 'compatibility.json')
    backup = root / 'compat-originals'
    originals = {}
    for row in previous.get('files', []):
        file = sdk / row['file']
        if not file.resolve().is_relative_to(sdk):
            raise Failure('COMPAT_SOURCE_CHANGED')
        actual = file.read_bytes()
        if hashlib.sha256(actual).hexdigest() != row.get('sha256'):
            raise Failure('COMPAT_SOURCE_CHANGED')
        if b'hermes-omnigent-plugin compatibility' in actual and patches.MARKER.encode() not in actual:
            original = backup / row['file'].replace('/', '__')
            if not original.is_file():
                raise Failure('COMPAT_BACKUP_MISSING')
            originals[file] = original.read_text()
    # Validate and compile the complete proposed upgrade before changing any
    # SDK file. A failed later anchor leaves the previous generation intact.
    edits = patches.make_edits(sdk, source_overrides=originals)
    backup.mkdir(mode=0o700, exist_ok=True)
    receipts = []
    for file, original, patched in edits:
        target = backup / (str(file.relative_to(sdk)).replace('/', '__'))
        if not target.exists():
            target.write_text(original)
            target.chmod(0o600)
        if file.read_text() != patched:
            file.write_text(patched)
        receipts.append({'file': str(file.relative_to(sdk)), 'sha256': hashlib.sha256(patched.encode()).hexdigest()})
    # Normal and isolated Python subprocesses both process the private venv's
    # site .pth files. No global site-packages or caller workspace is changed.
    site = sdk.parent
    (site / 'hermes_omnigent_plugin.pth').write_text('import sys; sys.path.insert(0, ' + repr(str(plugin_root)) + ')\n')
    write(root / 'compatibility.json', {'version': '0.16.0', 'generation': patches.MARKER, 'privateOnly': True, 'files': receipts})


if __name__ == '__main__':
    install_private(Path(sys.argv[1]), Path(sys.argv[2]), sys.executable)

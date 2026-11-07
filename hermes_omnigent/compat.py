"""Hermes model/profile support for this plugin's private Omnigent (the release pinned in runtime.OMNIGENT_VERSION)."""
import asyncio
import concurrent.futures
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import sqlite3
import threading
import time

_logger = logging.getLogger(__name__)


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


SLASH_COMMAND_GRACE_S = 3


def await_hermes_message(db_path, baseline_id, content, timeout):
    """Confirm the submitted user text, never an unrelated assistant/control row."""
    expected = content.replace('\r\n', '\n').replace('\r', '\n').strip()
    # Hermes expands a slash skill into other text and runs /model without any row, so the exact text never appears.
    # Reporting that as a failed delivery would paste the command a second time.
    is_command = expected.startswith('/')
    deadline = time.monotonic() + (min(timeout, SLASH_COMMAND_GRACE_S) if is_command else timeout)
    while True:
        try:
            connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=1)
            try:
                rows = connection.execute("SELECT content FROM messages WHERE id > ? AND role = 'user' ORDER BY id LIMIT 1000", (baseline_id,))
                if is_command and rows:
                    return True
                if any(isinstance(row[0], str) and row[0].replace('\r\n', '\n').strip() == expected for row in rows):
                    return True
            finally:
                connection.close()
        except sqlite3.Error:
            pass
        if time.monotonic() >= deadline:
            return is_command
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


# One inventory run costs a Python start, Hermes' imports and a probe of the active endpoint: 2-4 s on a quick host and far more on a
# slow one, while Omnigent waits 5 s (before a session exists) or 15 s (the picker) for the answer. A complete answer is therefore
# kept for a minute (and dropped as soon as Hermes' own configuration files change), and callers that arrive while a run is in
# progress share it instead of each starting another Python process. Only an answer that models.py itself reports complete is kept:
# an incomplete one (the endpoint did not list its models), a failure, and an answer from a models.py that reports nothing (an older
# release) are returned to the callers that waited for them but never kept, so the next call asks again. The status travels inside
# the list, so an older compat, which keeps only elements that have an id, reads a newer models.py without noticing it.
MODELS_TTL_S = 60.0
MODELS_WAIT_S = 60.0  # how long a caller waits for a run it shares; a run itself is bounded by the 45 s process timeout
MODELS_STATUS_KEY = 'inventory'  # the last element of models.py's list: {'inventory': 'complete' | 'degraded'}, not a model
MODEL_OPTIONS_FAILED = 'hermes_native_model_options_failed'  # the code of the 503 the SDK's own catalog routes use too
_models_clock = time.monotonic
_models_lock = threading.Lock()
_models_kept = None  # (key, expires_at, rows) of the last complete answer
_models_flight = None  # (key, future) of the run in progress


def _forget_models_after_fork():
    # A runner forked while another thread held the lock, or was running an inventory, must not wait for a thread that does not
    # exist in the child.
    global _models_lock, _models_kept, _models_flight
    _models_lock = threading.Lock()
    _models_kept = _models_flight = None


reset_hermes_models = _forget_models_after_fork
if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_forget_models_after_fork)


def _models_key():
    profile = os.environ.get('HERMES_OMNIGENT_PROFILE')
    if not profile:
        return None  # nothing to tell a changed configuration by, so nothing is kept
    stamps = []
    for name in ('config.yaml', '.env', 'auth.json'):
        try:
            info = (Path(profile) / name).stat()
            stamps.append((info.st_mtime_ns, info.st_size))
        except OSError:
            stamps.append(None)
    return (profile, os.environ.get('HERMES_OMNIGENT_PYTHON'), os.environ.get('HERMES_OMNIGENT_PYTHONPATH'), tuple(stamps))


def _run_hermes_models():
    """Start the inventory script once. Returns (rows, complete); complete is False when the active endpoint could not list its models."""
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
    status = next((row[MODELS_STATUS_KEY] for row in rows if isinstance(row, dict) and 'id' not in row and MODELS_STATUS_KEY in row), None)
    rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_.:\-]{0,200}', row['id'])]
    complete = status == 'complete'
    if status == 'degraded':
        _logger.warning('The Hermes model inventory is incomplete: the active endpoint did not list its models. '
                        'The list is not kept; the next call asks again.')
    elif not complete:
        _logger.info('The Hermes model inventory script did not say whether its list is complete; the list is not kept.')
    return rows, complete


def hermes_models():
    """Hermes' model inventory, for the picker and for checking a model change (see the notes above on when a run is shared)."""
    global _models_kept, _models_flight
    key = _models_key()
    with _models_lock:
        kept = _models_kept
        if key is not None and kept is not None and kept[0] == key and kept[1] > _models_clock():
            return [dict(row) for row in kept[2]]
        flight = _models_flight
        if flight is not None and flight[0] == key:
            future, leader = flight[1], False
        else:
            future, leader = concurrent.futures.Future(), True
            _models_flight = (key, future)
    if not leader:
        try:
            rows = future.result(timeout=MODELS_WAIT_S)  # only a vanished leader gets here
        except concurrent.futures.TimeoutError:
            with _models_lock:
                if _models_flight is not None and _models_flight[1] is future:
                    _models_flight = None  # or every later caller would wait for it too
            raise RuntimeError('HERMES_MODELS_UNAVAILABLE') from None
        return [dict(row) for row in rows]
    try:
        rows, complete = _run_hermes_models()
    except BaseException as error:
        with _models_lock:
            if _models_flight is not None and _models_flight[1] is future:
                _models_flight = None
        future.set_exception(error)
        raise
    with _models_lock:
        current = _models_flight is not None and _models_flight[1] is future
        if current:
            _models_flight = None
        # A run that a configuration change or a timed-out wait has replaced must not overwrite what a newer run kept.
        if current and complete and key is not None:
            _models_kept = (key, _models_clock() + MODELS_TTL_S, rows)
    future.set_result(rows)
    return [dict(row) for row in rows]


def shared_local_model_config(config):
    model = config.get('model')
    if not isinstance(model, dict) or model.get('provider') not in ('llamacpp', 'llama.cpp', 'llama-cpp'):
        return config
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    if env.get('HERMES_OMNIGENT_PYTHONPATH'):
        env['PYTHONPATH'] = env['HERMES_OMNIGENT_PYTHONPATH']
    env['HERMES_HOME'] = env['HERMES_OMNIGENT_PROFILE']
    try:
        result = subprocess.run([env['HERMES_OMNIGENT_PYTHON'],
            str(Path(__file__).with_name('native_local_model.py'))], env=env,
            capture_output=True, text=True, timeout=30)
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError()
        connection = json.loads(result.stdout)
        if not isinstance(connection, dict) or set(connection) != {'provider', 'base_url', 'api_key', 'api_mode'}:
            raise ValueError()
        return {**config, 'model': {**model, **connection}}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise RuntimeError('HERMES_LOCAL_MODEL_UNAVAILABLE') from None


def shared_user_skills(config):
    """Let a per-session home see the profile's skills; the session home only seeds the bundled ones."""
    profile = os.environ.get('HERMES_OMNIGENT_PROFILE')
    if not profile or not (Path(profile) / 'skills').is_dir():
        return config
    return {**config, 'skills': {'external_dirs': [str(Path(profile) / 'skills')]}}


def selected_hermes_model(home, session_id):
    """Read the selected model, without exporting private native policy fields."""
    connection = sqlite3.connect(f'file:{Path(home) / "state.db"}?mode=ro', uri=True, timeout=1)
    try:
        row = connection.execute('SELECT model, chat_id FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise RuntimeError('HERMES_MODEL_STATE_UNAVAILABLE')
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'state_meta' in tables:
            saved = connection.execute('SELECT value FROM state_meta WHERE key=?',
                ('gateway.local_policy.v1:' + (row[1] or session_id),)).fetchone()
            if saved:
                return json.loads(saved[0])['policy']['model']
        return row[0]
    finally:
        connection.close()


def select_native_model(home, session_id, model):
    if os.environ.get('HERMES_OMNIGENT_DIRECT_HERMES') != '1':
        return False
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    if env.get('HERMES_OMNIGENT_PYTHONPATH'):
        env['PYTHONPATH'] = env['HERMES_OMNIGENT_PYTHONPATH']
    env['HERMES_HOME'] = str(home)
    result = subprocess.run([env['HERMES_OMNIGENT_PYTHON'],
        str(Path(__file__).with_name('native_model.py')), str(home), session_id, model],
        env=env, capture_output=True, text=True, timeout=12)
    if result.returncode == 3:
        return False
    if result.returncode or result.stdout.strip() != 'HERMES_MODEL_SELECTED':
        raise RuntimeError('HERMES_MODEL_CHANGE_UNCONFIRMED')
    if selected_hermes_model(home, session_id) != model:
        raise RuntimeError('HERMES_MODEL_CHANGE_UNCONFIRMED')
    return True


def inject_hermes_model(bridge_dir, model):
    from omnigent.harnesses.hermes_native import bridge
    from omnigent.harnesses.hermes_native.forwarder import _read_state
    state = _read_state(bridge_dir)
    home = bridge.read_hermes_home(bridge_dir)
    if not home or not state.hermes_session_id:
        raise RuntimeError('HERMES_MODEL_STATE_UNAVAILABLE')
    if selected_hermes_model(home, state.hermes_session_id) == model:
        return
    if select_native_model(home, state.hermes_session_id, model):
        return
    info = bridge._wait_for_tmux_info(bridge_dir, timeout_s=10)
    if not bridge._session_alive(info['socket_path'], info['tmux_target']):
        raise RuntimeError('HERMES_TERMINAL_UNAVAILABLE')
    command = '/model ' + model
    bridge._deliver_once(info['socket_path'], info['tmux_target'], command, bridge_dir,
        bridge._submit_needle(command), settle_timeout_s=5)
    # /model changes native policy; it deliberately creates no user transcript
    # row. Confirm durable selection instead of retyping the command as a chat.
    deadline = time.monotonic() + 15
    while True:
        if selected_hermes_model(home, state.hermes_session_id) == model:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('HERMES_MODEL_CHANGE_UNCONFIRMED')
        time.sleep(.2)


async def change_model(session_id, model):
    from starlette.responses import JSONResponse, Response
    try:
        rows = await asyncio.to_thread(hermes_models)
    except Exception:
        # The inventory is what a model is checked against before it reaches a terminal command: without it nothing changes.
        _logger.warning('The Hermes model inventory is unavailable; the model change was not applied', exc_info=True)
        return JSONResponse({'error': MODEL_OPTIONS_FAILED, 'detail': 'The Hermes model inventory is unavailable.'}, status_code=503)
    if model is None:
        model = next((row['id'] for row in rows if row.get('isDefault')), None)
    allowed = {row['id'] for row in rows}
    if model not in allowed:
        return JSONResponse({'error': 'invalid_model'}, status_code=400)
    from omnigent.harnesses.hermes_native.bridge import bridge_dir_for_session_id
    try:
        await asyncio.to_thread(inject_hermes_model, bridge_dir_for_session_id(session_id), model)
    except Exception:
        return JSONResponse({'error': 'hermes_model_change_failed'}, status_code=409)
    return Response(status_code=204)


def install_private(root, plugin_root, python):
    """Patch only the managed SDK; exact anchors fail closed on other versions."""
    from .runtime import Failure, OMNIGENT_VERSION, write
    import hashlib
    import importlib.metadata
    import shutil
    if importlib.metadata.version('omnigent') != OMNIGENT_VERSION:
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
    if previous.get('version') != OMNIGENT_VERSION:
        # The receipts and the saved originals describe another Omnigent release. The files now on disk are that release's
        # replacement, so nothing may be compared with them; keep the old originals aside for the record.
        label = re.sub(r'[^0-9A-Za-z._-]', '_', str(previous.get('version') or 'unknown'))
        previous = {}
        if backup.is_dir():
            aside = root / f'compat-originals.{label}'
            shutil.rmtree(aside, ignore_errors=True)
            backup.rename(aside)
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
    write(root / 'compatibility.json', {'version': OMNIGENT_VERSION, 'generation': patches.MARKER, 'privateOnly': True, 'files': receipts})


if __name__ == '__main__':
    install_private(Path(sys.argv[1]), Path(sys.argv[2]), sys.executable)

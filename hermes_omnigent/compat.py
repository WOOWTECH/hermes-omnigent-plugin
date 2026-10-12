"""Hermes model/profile support for this plugin's private Omnigent 0.16."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def hermes_models():
    python = os.environ['HERMES_OMNIGENT_PYTHON']
    script = Path(__file__).with_name('models.py')
    process = subprocess.run([python, str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=45)
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

"""Native Hermes 0.22 Gateway lifecycle for a single Omnigent session.

Executed by Hermes's Python, never by the private Omnigent SDK interpreter.
Only explicit direct execution uses this helper. The original profile's
supervised Gateway is never started, stopped, or reconfigured here.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

if __package__:
    from .session_store import bridge_root
else:
    from session_store import bridge_root


def validate_home(value):
    home = Path(value)
    legacy = Path(tempfile.gettempdir()) / f'omnigent-{os.getuid()}' / 'hermes-native'
    base = home.parent.parent
    if (not home.is_absolute() or home.name != 'hermes_home'
            or base not in (legacy, bridge_root(legacy)) or not re.fullmatch(r'[a-f0-9]{32}', home.parent.name)):
        raise RuntimeError('HERMES_GATEWAY_HOME_INVALID')
    for path in (base.parent, base, home.parent, home):
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
            raise RuntimeError('HERMES_GATEWAY_HOME_INVALID')
    return home


def identity(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        return {'start': raw[raw.rfind(')') + 2:].split()[19],
                'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, ValueError, IndexError):
        return {}


def owned_process(home, receipt):
    pid = receipt.get('pid')
    if not isinstance(pid, int) or pid <= 1 or not receipt.get('identity'):
        return False
    try:
        process = Path(f'/proc/{pid}')
        return (process.stat().st_uid == os.getuid() and identity(pid) == receipt['identity']
                and f'HERMES_HOME={home}'.encode() in (process / 'environ').read_bytes().split(b'\0'))
    except OSError:
        return False


@contextmanager
def locked(home):
    descriptor = os.open(home / '.omnigent-gateway.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        deadline = time.monotonic() + 65
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('HERMES_GATEWAY_BUSY') from None
                time.sleep(.1)
        yield


def read_receipt(home):
    file = home / '.omnigent-gateway.json'
    if not file.exists():
        return {}
    if file.is_symlink() or file.stat().st_uid != os.getuid():
        raise RuntimeError('HERMES_GATEWAY_RECEIPT_INVALID')
    receipt = json.loads(file.read_text())
    if not isinstance(receipt, dict):
        raise RuntimeError('HERMES_GATEWAY_RECEIPT_INVALID')
    return receipt


def save_receipt(home, receipt):
    fd, name = tempfile.mkstemp(dir=home)
    try:
        with os.fdopen(fd, 'w') as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump(receipt, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, home / '.omnigent-gateway.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(home):
    home = validate_home(home)
    if os.environ.get('HERMES_OMNIGENT_DIRECT_HERMES') != '1':
        raise RuntimeError('HERMES_GATEWAY_DIRECT_APPROVAL_REQUIRED')
    try:
        from hermes_cli.gateway_runtime import discover_gateway_endpoint
        from hermes_cli.gateway_runtime_start import spawn_unmanaged_gateway
    except ModuleNotFoundError as error:
        if error.name in ('hermes_cli', 'hermes_cli.gateway_runtime'):
            return 'legacy'
        raise
    with locked(home):
        state = discover_gateway_endpoint(home, timeout=2)
        if state.state == 'ready':
            if not owned_process(home, read_receipt(home)):
                raise RuntimeError('HERMES_GATEWAY_OWNER_UNKNOWN')
            return 'ready'
        if state.state != 'absent':
            raise RuntimeError('HERMES_GATEWAY_NOT_READY')
        # Official per-process opt-out: do not redirect this private profile
        # to the container's s6 service, which belongs to the original profile.
        os.environ['HERMES_GATEWAY_NO_SUPERVISE'] = '1'
        deadline = time.monotonic() + 60
        child = spawn_unmanaged_gateway(home, deadline=deadline, idle_exit=True)
        try:
            record = {'pid': child.pid, 'identity': identity(child.pid)}
            if not record['identity']:
                raise RuntimeError('HERMES_GATEWAY_START_FAILED')
            save_receipt(home, record)
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError('HERMES_GATEWAY_START_FAILED')
                state = discover_gateway_endpoint(home, timeout=2)
                if state.state == 'ready' and owned_process(home, record):
                    return 'ready'
                time.sleep(.25)
            raise RuntimeError('HERMES_GATEWAY_START_TIMEOUT')
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            raise


def stop(home):
    home = validate_home(home)
    with locked(home):
        receipt = read_receipt(home)
        if not owned_process(home, receipt):
            return 'stopped'
        os.kill(receipt['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 12
        while owned_process(home, receipt) and time.monotonic() < deadline:
            time.sleep(.1)
        if owned_process(home, receipt):
            raise RuntimeError('HERMES_GATEWAY_STOP_TIMEOUT')
        return 'stopped'


if __name__ == '__main__':
    try:
        action, home = sys.argv[1:]
        result = {'prepare': prepare, 'stop': stop}[action](home)
        print(json.dumps({'ok': True, 'state': result}))
    except Exception:
        # Do not expose native startup exceptions, which can contain credentials.
        print(json.dumps({'ok': False, 'error': 'HERMES_GATEWAY_FAILED'}))
        raise SystemExit(1)

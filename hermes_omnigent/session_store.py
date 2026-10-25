"""Persistent, profile-scoped native conversation state on the Hermes volume."""
from contextlib import closing
import errno
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time


def bridge_root(legacy):
    profile = os.environ.get('HERMES_OMNIGENT_PROFILE')
    if not profile:
        return Path(legacy)
    home = Path(profile)
    if not home.is_absolute():
        raise RuntimeError('HERMES_PROFILE_INVALID')
    # Keep the pathname short enough for Hermes's Unix-domain gateway socket.
    return home / 'omnigent-plugin' / 'sessions'


def _private(path):
    stat = path.lstat()
    if path.is_symlink() or not path.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')


def prepare_private_directory(path):
    """Restore owner-only access after a PVC fsGroup permission adjustment.

    Only plugin-owned directories are callers. Open without following a final
    symlink and check ownership on the descriptor before changing permissions.
    Legacy sources retain their strict validation; they are never repaired.
    """
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
                raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
            if stat.S_IMODE(info.st_mode) != 0o700:
                os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    except OSError:
        raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID') from None


def migrate_legacy(target):
    """Copy durable state from a stopped pre-rc.9 conversation, without deleting it.

    Runtime sockets, PID records, cached auth relays and scratch files stay out.
    SQLite backup includes committed WAL rows. Copy publication is atomic, so
    an interruption leaves either the previous temp state or a complete copy.
    """
    target = Path(target)
    legacy_root = Path(tempfile.gettempdir()) / f'omnigent-{os.getuid()}' / 'hermes-native'
    if target.parent == legacy_root:
        return
    if target.parent != bridge_root(legacy_root) or not re.fullmatch(r'[a-f0-9]{32}', target.name):
        raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
    if target.is_symlink():
        raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
    for path in (target.parent.parent, target.parent):
        prepare_private_directory(path)
    if target.exists():
        prepare_private_directory(target)
        home = target / 'hermes_home'
        if home.exists() or home.is_symlink():
            prepare_private_directory(home)
        return
    source = legacy_root / target.name
    if not source.exists():
        return
    for path in (legacy_root.parent, legacy_root, source, source / 'hermes_home'):
        _private(path)
    from .native_gateway import owned_process
    record_file = source / 'hermes_home/.omnigent-gateway.json'
    if record_file.is_file() and owned_process(source / 'hermes_home', json.loads(record_file.read_text())):
        raise RuntimeError('HERMES_SESSION_MIGRATION_BUSY')
    tmux_file = source / 'tmux.json'
    if tmux_file.is_file():
        info = json.loads(tmux_file.read_text())
        probe = subprocess.run(['tmux', '-S', info['socket_path'], 'has-session', '-t', info['tmux_target']],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if probe.returncode == 0:
            raise RuntimeError('HERMES_SESSION_MIGRATION_BUSY')
        if probe.returncode != 1:
            raise RuntimeError('HERMES_SESSION_MIGRATION_CHECK_FAILED')
    # Migration runs only as part of native terminal recreation. A stopped
    # Gateway is required above; the current runner has cancelled its forwarder.
    staging = Path(tempfile.mkdtemp(prefix='.migrate-', dir=target.parent))
    try:
        home = staging / 'hermes_home'
        home.mkdir(mode=0o700)
        db = source / 'hermes_home/state.db'
        if db.is_file():
            if db.is_symlink() or db.stat().st_uid != os.getuid():
                raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
            original = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
            copied = sqlite3.connect(home / 'state.db')
            try:
                deadline = time.monotonic() + 30
                def progress(*_):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('HERMES_SESSION_MIGRATION_TIMEOUT')
                original.backup(copied, pages=128, progress=progress)
            finally:
                copied.close()
                original.close()
            (home / 'state.db').chmod(0o600)
        for name in ('hermes_forwarder.json', 'hermes_status_forwarder.json'):
            file = source / name
            if file.is_file():
                if file.is_symlink() or file.stat().st_uid != os.getuid():
                    raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
                shutil.copyfile(file, staging / name)
                (staging / name).chmod(0o600)
        if db.is_file():
            import_native_history(staging)
        try:
            staging.rename(target)
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            _private(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def import_native_history(staging):
    """Native policies bind their profile path; use Hermes's portability API.

    The helper receives a stopped SQLite snapshot, never the live source. Its
    stdout is deliberately only a fixed success receipt, never transcript data.
    """
    db = staging / 'hermes_home/state.db'
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        canonical = ('state_meta' in tables and connection.execute(
            "SELECT 1 FROM state_meta WHERE key LIKE 'gateway.local_policy.v1:%' LIMIT 1").fetchone())
    if not canonical:
        return
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    if env.get('HERMES_OMNIGENT_PYTHONPATH'):
        env['PYTHONPATH'] = env['HERMES_OMNIGENT_PYTHONPATH']
    env['HERMES_HOME'] = str(staging / 'hermes_home')
    try:
        result = subprocess.run([env['HERMES_OMNIGENT_PYTHON'],
            str(Path(__file__).with_name('session_import.py')), str(staging)],
            env=env, capture_output=True, text=True, timeout=90)
        if result.returncode or result.stdout.strip() != 'HERMES_HISTORY_IMPORTED':
            raise RuntimeError('HERMES_SESSION_IMPORT_FAILED')
    except (KeyError, OSError, subprocess.TimeoutExpired):
        raise RuntimeError('HERMES_SESSION_IMPORT_FAILED') from None

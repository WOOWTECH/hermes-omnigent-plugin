"""Profile-scoped pairing, managed dependencies, and verifiable chat receipts."""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

NAME = 'hermes-omnigent'
ENGINES = ('hermes', 'claude', 'codex', 'pi')
SENTINEL = 'OMNIGENT_CONNECTION_OK'
# The Omnigent release this plugin installs for itself and patches (hermes_omnigent/patches.py). The compatibility installer,
# the CI workflows and the weekly canary all read it from here; a plugin update that moves it makes the next activation
# reinstall the private runtime (see Runtime.install).
OMNIGENT_VERSION = '0.19.1'
_cache = {}
_cache_lock = threading.Lock()

def process_identity(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        return {'start': text[text.rfind(')') + 2:].split()[19],
                'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, ValueError, IndexError):
        return {}



class Failure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def failure_code(value, fallback):
    """Receipts contain bounded codes, never provider response bodies."""
    return value.upper() if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,63}', value) else fallback


def read(path, default=None):
    try:
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict):
            raise Failure('INVALID_STATE')
        return value
    except FileNotFoundError:
        return {} if default is None else default
    except (ValueError, OSError):
        raise Failure('INVALID_STATE') from None


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Runtime:
    def __init__(self, home=None, *, call=None, find=None, installer=None, active=None):
        self.home = Path(home or os.environ.get('HERMES_HOME') or Path.home() / '.hermes').resolve()
        self.root = self.home / 'omnigent-plugin'
        self.omni = self.root / 'omnigent'
        self.adapter = Path(__file__).with_name('adapter.py')
        self._call = call
        self._find = find
        self._installer = installer
        self._active = active
        self._thread = None
        self._mutex = threading.RLock()
        self._watcher = None

    def active(self):
        if self._active:
            return self._active()
        # Hermes 0.22 bundles its safe YAML reader instead of PyYAML.
        try:
            import hermes_yaml as yaml
        except ModuleNotFoundError as error:
            if error.name != 'hermes_yaml':
                raise
            import yaml
        try:
            config = yaml.safe_load((self.home / 'config.yaml').read_text()) or {}
            plugins = config.get('plugins', {})
            return NAME in (plugins.get('enabled') or []) and NAME not in (plugins.get('disabled') or [])
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            return False

    def state(self):
        return read(self.root / 'state.json', {'phase': 'needs-login', 'loginConfirmed': False})

    def require_profile_user(self):
        # Administrative kubectl/SSH shells can be root while Hermes itself
        # runs as a service user. Never create 0600 state that user cannot read.
        # A non-root service may legitimately use a root-owned writable mount.
        if os.geteuid() != 0:
            return
        for path in (self.home, self.root):
            try:
                owner = path.stat().st_uid
            except FileNotFoundError:
                continue
            except OSError:
                raise Failure('PROFILE_UNAVAILABLE') from None
            if owner != 0:
                raise Failure('PROFILE_USER_MISMATCH')

    def save(self, **changes):
        self.require_profile_user()
        state = {**self.state(), **changes, 'updatedAt': time.time()}
        write(self.root / 'state.json', state)
        return state

    @contextlib.contextmanager
    def lock(self):
        self.require_profile_user()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.root / 'operation.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure('OPERATION_BUSY') from None
            yield
        finally:
            os.close(descriptor)

    def environment(self):
        env = dict(os.environ)
        env.update(HERMES_HOME=str(self.home), OMNIGENT_CONFIG_HOME=str(self.omni),
                   OMNIGENT_DATA_DIR=str(self.omni), OMNIGENT_HOST_NO_OPEN='1')
        env['HERMES_OMNIGENT_PYTHONPATH'] = env.get('HERMES_OMNIGENT_PYTHONPATH') or env.get('PYTHONPATH', '')
        # Hermes bundle dependencies must never override the private SDK's MCP,
        # FastAPI or other packages. Restore this path only in native inventory.
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['HERMES_OMNIGENT_PROFILE'] = str(self.home)
        env['HERMES_OMNIGENT_PYTHON'] = env.get('HERMES_OMNIGENT_PYTHON') or sys.executable
        env['HERMES_OMNIGENT_DIRECT_HERMES'] = '1' if self.state().get('allowDirectHermes') is True else '0'
        # The SDK filters host->runner environments, including zygote forks.
        # Forward these selectors explicitly; never opt in all HERMES_* vars.
        passthrough = {name.strip() for name in env.get('OMNIGENT_RUNNER_ENV_PASSTHROUGH', '').split(',') if name.strip()}
        passthrough.update(('HERMES_OMNIGENT_PYTHONPATH', 'HERMES_OMNIGENT_PROFILE', 'HERMES_OMNIGENT_PYTHON', 'HERMES_OMNIGENT_DIRECT_HERMES'))
        env['OMNIGENT_RUNNER_ENV_PASSTHROUGH'] = ','.join(sorted(passthrough))
        env['PATH'] = os.pathsep.join([str(self.root / 'bin'), str(self.root / 'bootstrap/bin'), env.get('PATH', '')])
        # An Omnigent-managed child must not donate its isolated agent home or
        # session identity to a new resident host.
        for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
            if '/.omnigent/' in env.get(key, ''):
                env.pop(key, None)
        for key in list(env):
            if key.startswith('OMNIGENT_') and any(word in key for word in ('TOKEN', 'BRIDGE', 'SESSION')):
                env.pop(key, None)
        env.pop('HARNESS_HERMES_NATIVE_BRIDGE_DIR', None)
        return env

    @staticmethod
    def bundled_uv():
        # Hermes bundle tools are not necessarily exposed in its service PATH.
        # Locate only the installed Hermes package, never the current workspace.
        try:
            spec = importlib.util.find_spec('hermes_cli')
            if not spec or not spec.origin:
                return None
            root = Path(spec.origin).resolve().parent.parent
            candidates = sorted((root / 'tools').glob('uv-*/uv'))
            for binary in reversed(candidates):
                if binary.is_file() and os.access(binary, os.X_OK):
                    return str(binary)
        except (ImportError, ValueError, OSError):
            pass
        return None

    def discover(self):
        if self._find:
            return self._find()
        env = self.environment()
        result = {}
        for name in (*ENGINES, 'omnigent', 'uv', 'tmux', 'bwrap'):
            binary = shutil.which(name, path=env['PATH'])
            if not binary and name == 'uv':
                binary = self.bundled_uv()
            if not binary:
                result[name] = {'available': False, 'path': None}
                continue
            try:
                info = Path(binary).stat()
                key = (binary, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, str(self.home))
                with _cache_lock:
                    cached = _cache.get(key)
                if not cached or cached[0] < time.monotonic():
                    probe = subprocess.run([binary, '-V' if name == 'tmux' else '--version'],
                                           capture_output=True, text=True, timeout=30 if name == 'hermes' else 8, env=env)
                    match = re.search(r'(?<!\d)(\d+\.\d+(?:\.\d+)?[a-z]?)', probe.stdout + probe.stderr)
                    entry = {'available': probe.returncode == 0, 'path': binary,
                             'version': match.group(1) if match else None}
                    with _cache_lock:
                        if len(_cache) > 128:
                            _cache.clear()
                        _cache[key] = (time.monotonic() + 15, entry)
                else:
                    entry = dict(cached[1])
                result[name] = entry
            except (OSError, subprocess.TimeoutExpired):
                result[name] = {'available': False, 'path': binary}
        return result

    def call(self, action, **inputs):
        dependencies = self.discover()
        request = {'action': action, 'configHome': str(self.omni), 'dataDir': str(self.omni),
                   'stateDir': str(self.root), 'workspace': str(self.home),
                   'omnigentBin': dependencies.get('omnigent', {}).get('path'),
                   'hostName': 'Hermes · ' + (os.environ.get('HOSTNAME') or self.home.name)[:100],
                   'engineInventory': {name: {'installed': dependencies.get(name, {}).get('available') is True,
                                            'version': dependencies.get(name, {}).get('version')} for name in ENGINES}, **inputs}
        if self._call:
            return self._call(request)
        binary = request['omnigentBin']
        python = Path(binary).resolve().parent / 'python' if binary else None
        if not python or not python.is_file():
            return {'ok': False, 'error': {'code': 'DEPENDENCIES_MISSING'}}
        try:
            process = subprocess.run([str(python), str(self.adapter)], input=json.dumps(request), text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.environment(),
                                     timeout=300 if action == 'verify' else 150)
            if process.returncode or len(process.stdout) > 1_048_576:
                raise Failure('ADAPTER_FAILED')
            return json.loads(process.stdout)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': {'code': 'ADAPTER_TIMEOUT'}}
        except (OSError, ValueError, Failure):
            return {'ok': False, 'error': {'code': 'ADAPTER_FAILED'}}

    def reset_pairing(self):
        with self.lock():
            if not self.active():
                raise Failure('PLUGIN_DISABLED')
            write(self.root / 'verification.json', {})
            self.save(phase='needs-login', loginConfirmed=False, issue=None, pairingId=uuid.uuid4().hex)
            return self.state()

    def installation_identity(self):
        # Hermes 0.22 carries untracked state across force reinstall. A fresh
        # git clone replaces .git/config, while a normal update copies it with
        # its original mtime before pulling. Directory inodes are not reliable
        # because publication can move/copy trees and reuse old inodes.
        checkout = Path(__file__).resolve().parents[1]
        marker = checkout / '.omnigent-installation.json'
        git_config = checkout / '.git/config'
        clone = str(git_config.stat().st_mtime_ns) if git_config.is_file() else None
        try:
            saved = read(marker)
            identity = saved.get('id')
            if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}', identity):
                if clone is not None and saved.get('clone') != clone:
                    self.require_profile_user()
                    # Legacy rc.8 markers have no clone fingerprint. A fresh
                    # clone's config is newer than the carried marker (Hermes
                    # preserves its mtime); a normal pull keeps the older config.
                    if saved.get('clone') is not None or git_config.stat().st_mtime_ns > marker.stat().st_mtime_ns:
                        identity = uuid.uuid4().hex
                    write(marker, {'id': identity, 'clone': clone})
                return identity
            if marker.exists():
                return None
        except Failure:
            return None
        self.require_profile_user()
        identity = uuid.uuid4().hex
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except (FileExistsError, FileNotFoundError):
            return None  # Another initializer or an installer is in progress.
        except OSError:
            raise Failure('INSTALLATION_ID_UNAVAILABLE') from None
        with os.fdopen(descriptor, 'w') as file:
            json.dump({'id': identity, 'clone': clone}, file)
            file.flush()
            os.fsync(file.fileno())
        return identity

    def lifecycle(self):
        active = self.active()
        state = self.state()
        installation = self.installation_identity()
        activation = read(self.root / 'activation.json').get('id')
        changed = ((installation is not None and state.get('installationId') != installation)
                   or state.get('activationId') != activation)
        if state.get('nativeActive') != active or changed:
            with self.lock():
                previous = self.state()
                changed = ((installation is not None and previous.get('installationId') != installation)
                           or previous.get('activationId') != activation)
                if not active or previous.get('nativeActive') is False or changed:
                    write(self.root / 'verification.json', {})
                    self.save(phase='needs-login', loginConfirmed=False, issue=None)
                    self.save(allowDirectHermes=False)
                self.save(nativeActive=active, activationId=activation,
                          **({'installationId': installation} if installation is not None else {}))
        return active

    def on_unload(self):
        # Native unload is synchronous with disable, unlike the polling fallback.
        # Persist revocation even when an operation holds the state lock, so a
        # rapid disable/enable cannot reuse the previous approval.
        if not self.active():
            self.require_profile_user()
            write(self.root / 'activation.json', {'id': uuid.uuid4().hex})
            try:
                self.lifecycle()
            except Failure:
                pass

    def approved(self, state):
        return (state.get('loginConfirmed') is True
                and state.get('activationId') == read(self.root / 'activation.json').get('id'))

    def receipt_ok(self, receipt, state, host_id, dependency):
        return (receipt.get('ok') is True and receipt.get('reply') == SENTINEL
                and receipt.get('cleanup') == 'stopped' and receipt.get('server') == state.get('server')
                and receipt.get('hostId') == host_id and receipt.get('cliVersion') == dependency.get('version'))

    def status(self):
        active = self.active()
        state = self.state()
        deps = self.discover()
        installed = [name for name in ENGINES if deps.get(name, {}).get('available')]
        response = self.call('status', **({'server': state['server']} if state.get('server') else {})) if active and deps.get('omnigent', {}).get('available') else {}
        # Host/model lookups can take seconds. A verification may complete
        # during that request; use its final local state and receipt together.
        state = self.state()
        receipts = read(self.root / 'verification.json')
        data = response.get('data') or {}
        host = data.get('host') or {'connected': False}
        approved = self.approved(state)
        ready = (active and approved and response.get('ok') is True
                 and host.get('connected') is True and bool(installed)
                 and all(data.get('engines', {}).get(name, {}).get('ready') is True
                         and self.receipt_ok(receipts.get(name, {}), state, host.get('id'), deps[name]) for name in installed))
        phase = state.get('phase', 'needs-login')
        if not active:
            phase = 'disabled'
        elif not approved and phase not in ('installing', 'connecting', 'verifying', 'disconnecting', 'error'):
            phase = 'needs-login'
        elif phase not in ('installing', 'connecting', 'verifying', 'disconnecting', 'error', 'needs-login'):
            phase = 'ready' if ready else 'needs-verification'
        operation = read(self.root / 'operation.json', {'running': False})
        if operation.get('running') and not self._thread_running(operation):
            operation = {**operation, 'running': False, 'error': 'OPERATION_INTERRUPTED'}
            if phase in ('installing', 'connecting', 'verifying', 'disconnecting'):
                phase = 'error'
        return {'phase': phase, 'enabled': active, 'loginConfirmed': approved,
                'pairingRequired': active and not approved,
                'server': state.get('server'), 'host': host, 'installedEngines': installed,
                'engines': {name: {**deps.get(name, {}), **data.get('engines', {}).get(name, {})} for name in ENGINES},
                'verification': receipts, 'operation': operation, 'issue': state.get('issue'),
                'sandbox': read(self.root / 'sandbox.json'),
                'allowDirectHermes': approved and state.get('allowDirectHermes') is True}

    @staticmethod
    def _thread_running(operation):
        try:
            os.kill(operation.get('pid', 0), 0)
            return operation.get('pid', 0) > 1 and operation.get('identity') == process_identity(operation['pid'])
        except (OSError, TypeError):
            return False

    def install(self):
        self.require_profile_user()
        if self._installer:
            return self._installer()
        deps = self.discover()
        if not deps.get('tmux', {}).get('available'):
            from .dependencies import private_tmux
            private_tmux(self.root)
            deps = self.discover()
        if not deps.get('bwrap', {}).get('available'):
            from .dependencies import private_tmux
            private_tmux(self.root, tool='bwrap')
            deps = self.discover()
        self.check_sandbox(deps)
        binary = deps.get('omnigent', {}).get('path')
        private = bool(deps.get('omnigent', {}).get('available') and binary and Path(binary).resolve().is_relative_to(self.root / 'uv/tools'))
        if private and deps['omnigent'].get('version') == OMNIGENT_VERSION:
            self.install_compatibility(binary)
            return deps
        # Missing, or a private runtime of another release (a plugin update moved the pin): install the pinned release over it.
        env = self.environment()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        uv = deps.get('uv', {}).get('path')
        if not deps.get('uv', {}).get('available'):
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(self.root / 'bootstrap'),
                                     '--disable-pip-version-check', '--no-input', 'uv==0.12.10'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, env=env)
            if result.returncode:
                raise Failure('UV_INSTALL_FAILED')
            uv = str(self.root / 'bootstrap/bin/uv')
        env.update(UV_TOOL_DIR=str(self.root / 'uv/tools'), UV_TOOL_BIN_DIR=str(self.root / 'bin'),
                   UV_CACHE_DIR=str(self.root / 'uv/cache'), UV_PYTHON_INSTALL_DIR=str(self.root / 'python'))
        python = sys.executable if sys.version_info >= (3, 12) else '3.12'
        result = subprocess.run([uv, 'tool', 'install', '--python', python, *(['--reinstall'] if private else []), f'omnigent=={OMNIGENT_VERSION}'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, env=env)
        if result.returncode:
            raise Failure('OMNIGENT_INSTALL_FAILED')
        deps = self.discover()
        if not deps.get('omnigent', {}).get('available'):
            raise Failure('OMNIGENT_MISSING')
        self.install_compatibility(deps['omnigent']['path'])
        return deps

    def check_sandbox(self, dependencies):
        binary = dependencies.get('bwrap', {}).get('path')
        if not binary:
            raise Failure('BUBBLEWRAP_REQUIRED')
        try:
            probe = subprocess.run([binary, '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev',
                                    '--unshare-user', '--uid', '0', '--gid', '0', '/bin/true'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, env=self.environment())
            supported = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            supported = False
        write(self.root / 'sandbox.json', {'namespaceSupported': supported, 'checkedAt': time.time()})
        if not supported and self.state().get('allowDirectHermes') is not True:
            raise Failure('SANDBOX_NAMESPACE_UNAVAILABLE')

    def install_compatibility(self, binary):
        python = Path(binary).resolve().parent / 'python'
        plugin = Path(__file__).resolve().parents[1]
        env = self.environment()
        env['PYTHONPATH'] = str(plugin)
        result = subprocess.run([str(python), '-m', 'hermes_omnigent.compat', str(self.root), str(plugin)],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if result.returncode:
            raise Failure('HERMES_COMPATIBILITY_INSTALL_FAILED')

    def require_active(self):
        if not self.active():
            raise Failure('PLUGIN_DISABLED')
        self.require_profile_user()

    def check_response(self, response):
        if not response.get('ok'):
            code = response.get('error', {}).get('code', 'OPERATION_FAILED')
            raise Failure(code if isinstance(code, str) and re.fullmatch(r'[A-Z_0-9]+', code) else 'OPERATION_FAILED')
        return response.get('data') or {}

    def initialize(self, server, username, password, allowDirectHermes=False):
        self.require_active()
        activation = read(self.root / 'activation.json').get('id')
        previous_server = self.state().get('server')
        # Invalidate approval even when malformed URL/new credentials fail.
        write(self.root / 'verification.json', {})
        if not isinstance(allowDirectHermes, bool):
            raise Failure('INVALID_EXECUTION_MODE')
        self.save(loginConfirmed=False, phase='installing', issue=None, allowDirectHermes=allowDirectHermes)
        from .adapter import normalize_server
        server = normalize_server(server)
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise Failure('FRESH_LOGIN_REQUIRED')
        if previous_server and previous_server != server:
            # Keep the original identity recoverable; do not rewrite a resident
            # host's config while it still points at a different server.
            raise Failure('DISCONNECT_BEFORE_SERVER_CHANGE')
        self.save(server=server)
        self.install()
        self.require_active()
        self.save(phase='connecting')
        self.ensure_host_generation(server)
        self.check_response(self.call('connect', server=server, username=username, password=password, allowManagedHost=True))
        self.save_host_generation()
        password = None
        self.require_active()
        if activation != read(self.root / 'activation.json').get('id'):
            raise Failure('FRESH_LOGIN_REQUIRED')
        self.save(loginConfirmed=True, activationId=activation, phase='needs-verification')
        return self.verify()

    def verify(self, engine=None, model=None):
        self.require_active()
        state = self.state()
        if not self.approved(state):
            raise Failure('FRESH_LOGIN_REQUIRED')
        deps = self.discover()
        installed = [name for name in ENGINES if deps.get(name, {}).get('available')]
        if not installed or (engine and engine not in installed):
            raise Failure('ENGINE_UNAVAILABLE')
        self.save(phase='verifying')
        catalog = self.check_response(self.call('catalog', server=state['server']))
        host_id = catalog.get('host', {}).get('id')
        receipts = read(self.root / 'verification.json')
        for name in [engine] if engine else installed:
            self.require_active()
            if not self.approved(state):
                raise Failure('FRESH_LOGIN_REQUIRED')
            response = self.call('verify', server=state['server'], engine=name, timeoutSeconds=120,
                                 **({'model': model} if model else {}))
            data = response.get('data') or {}
            ok = response.get('ok') is True and data.get('ok') is True and data.get('reply') == SENTINEL and data.get('cleanup') == 'stopped'
            reason = data.get('reason') or response.get('error', {}).get('code')
            if not reason and data.get('cleanup') == 'stop_failed':
                reason = 'SESSION_STOP_FAILED'
            receipts[name] = {'engine': name, 'server': state['server'], 'hostId': host_id,
                              'cliVersion': deps[name].get('version'), 'checkedAt': time.time(), 'ok': ok,
                              **{key: data[key] for key in ('model', 'cleanup', 'sessionId', 'url', 'nativeSubmitRecovered') if key in data},
                              **({'cleanupReason': failure_code(data.get('cleanupReason'), 'SESSION_STOP_FAILED')}
                                 if data.get('cleanup') == 'stop_failed' else {}),
                              **({'reply': SENTINEL} if ok else {'reason': failure_code(reason, 'NO_REPLY')})}
            write(self.root / 'verification.json', receipts)
        self.require_active()
        if not self.approved(state):
            raise Failure('FRESH_LOGIN_REQUIRED')
        ok = bool(host_id) and all(self.receipt_ok(receipts.get(name, {}), state, host_id, deps[name]) for name in installed)
        self.save(phase='ready' if ok else 'needs-verification', issue=None if ok else 'INCOMPLETE_CHAT_VERIFICATION')
        return {'ok': ok, 'verification': receipts}

    def catalog(self):
        self.require_active()
        state = self.state()
        if not self.approved(state):
            raise Failure('FRESH_LOGIN_REQUIRED')
        return self.call('catalog', server=state['server'])

    def disconnect(self):
        self.require_active()
        state = self.state()
        write(self.root / 'verification.json', {})
        self.save(phase='disconnecting', loginConfirmed=False)
        result = self.check_response(self.call('disconnect', server=state.get('server')))
        self.save(phase='needs-login', issue=None)
        self.save(server=None)
        return result

    def restore(self):
        self.require_active()
        state = self.state()
        if not self.approved(state) or not state.get('server'):
            raise Failure('FRESH_LOGIN_REQUIRED')
        self.install()
        self.require_active()
        self.save(phase='connecting')
        self.ensure_host_generation(state['server'])
        self.check_response(self.call('connect', server=state['server'], allowManagedHost=True))
        self.save_host_generation()
        self.require_active()
        # Existing evidence still binds to this host and executable version;
        # restoration never sends another model prompt automatically.
        self.save(phase='needs-verification')
        current = self.status()
        self.save(phase='ready' if current['phase'] == 'ready' else 'needs-verification')
        return {'ok': True}

    def host_generation(self):
        from .patches import MARKER
        return {'generation': MARKER, 'allowDirectHermes': self.state().get('allowDirectHermes') is True,
                'runnerEnvironmentVersion': 2}

    def ensure_host_generation(self, server):
        previous = read(self.root / 'host-runtime.json')
        process = read(self.root / 'host-process.json')
        if process.get('pid') and previous != self.host_generation():
            self.check_response(self.call('restart_host', server=server))

    def save_host_generation(self):
        write(self.root / 'host-runtime.json', self.host_generation())

    def start(self, action, **inputs):
        self.require_active()
        allowed = {'pair': self.initialize, 'verify': self.verify, 'disconnect': self.disconnect,
                   'prepare': self.install, 'restore': self.restore}
        if action not in allowed:
            raise Failure('UNKNOWN_ACTION')
        with self._mutex:
            if self._thread and self._thread.is_alive():
                raise Failure('OPERATION_BUSY')
            lease = self.lock()
            lease.__enter__()
            identifier = uuid.uuid4().hex
            try:
                write(self.root / 'operation.json', {'id': identifier, 'running': True, 'action': action, 'pid': os.getpid(), 'identity': process_identity(os.getpid())})
                def work():
                    try:
                        if action == 'prepare':
                            self.save(phase='installing')
                        result = allowed[action](**inputs)
                        if action == 'prepare':
                            self.save(phase='needs-verification' if self.state().get('loginConfirmed') else 'needs-login')
                        write(self.root / 'operation.json', {'id': identifier, 'running': False, 'ok': result.get('ok', True)})
                    except Exception as error:
                        code = getattr(error, 'code', 'OPERATION_FAILED')
                        code = code if isinstance(code, str) and re.fullmatch(r'[A-Z_0-9]+', code) else 'OPERATION_FAILED'
                        login_required = code in ('NEEDS_LOGIN', 'NEEDS_BROWSER_AUTH', 'FRESH_LOGIN_REQUIRED')
                        self.save(phase='needs-login' if login_required else 'error', issue=code,
                                  **({'loginConfirmed': False} if login_required else {}))
                        write(self.root / 'operation.json', {'id': identifier, 'running': False, 'ok': False, 'error': code})
                    finally:
                        inputs.clear()
                        lease.__exit__(None, None, None)
                self._thread = threading.Thread(target=work, name='omnigent-operation', daemon=True)
                self._thread.start()
            except Exception:
                lease.__exit__(None, None, None)
                raise
        return {'ok': True, 'id': identifier, 'running': True}

    def activate(self, prepare=False):
        if (os.environ.get('HARNESS_HERMES_NATIVE_BRIDGE_DIR') or os.environ.get('OMNIGENT_HERMES_NATIVE_BRIDGE_DIR')):
            return
        try:
            self.lifecycle()
        except Failure:
            pass
        if prepare and self.active():
            try:
                deps = self.discover()
                from .patches import MARKER
                compatibility = read(self.root / 'compatibility.json')
                state = self.state()
                approved = self.approved(state) and bool(state.get('server'))
                if (not deps.get('omnigent', {}).get('available') or not deps.get('tmux', {}).get('available')
                        or compatibility.get('generation') != MARKER or compatibility.get('version') != OMNIGENT_VERSION):
                    # An authenticated upgrade must also refresh its resident
                    # host after installing the new SDK generation. A release that moves
                    # the Omnigent pin without touching the patches keeps the generation,
                    # so the runtime's own release is compared as well.
                    self.start('restore' if approved else 'prepare')
                elif approved and (read(self.root / 'host-runtime.json') != self.host_generation()
                                   or not self.status().get('host', {}).get('connected')):
                    self.start('restore')
            except Failure:
                pass
        if self._watcher:
            return
        def watch():
            while True:
                time.sleep(0.5)
                try:
                    self.lifecycle()
                except Failure:
                    pass
        self._watcher = threading.Thread(target=watch, name='omnigent-lifecycle', daemon=True)
        self._watcher.start()


_runtime = None
def get_runtime():
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime

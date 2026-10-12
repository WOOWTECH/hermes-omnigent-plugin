"""Profile-scoped pairing, managed dependencies, and verifiable chat receipts."""
from __future__ import annotations

import contextlib
import fcntl
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
_cache = {}
_cache_lock = threading.Lock()


class Failure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
        import yaml
        try:
            config = yaml.safe_load((self.home / 'config.yaml').read_text()) or {}
            plugins = config.get('plugins', {})
            return NAME in (plugins.get('enabled') or []) and NAME not in (plugins.get('disabled') or [])
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            return False

    def state(self):
        return read(self.root / 'state.json', {'phase': 'needs-login', 'loginConfirmed': False})

    def save(self, **changes):
        state = {**self.state(), **changes, 'updatedAt': time.time()}
        write(self.root / 'state.json', state)
        return state

    @contextlib.contextmanager
    def lock(self):
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
        env['PATH'] = os.pathsep.join([str(self.root / 'bin'), str(self.root / 'bootstrap/bin'), env.get('PATH', '')])
        # An Omnigent-managed child must not donate its isolated agent home or
        # session identity to a new resident host.
        for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
            if '/.omnigent/' in env.get(key, ''):
                env.pop(key, None)
        for key in list(env):
            if key.startswith('OMNIGENT_') and any(word in key for word in ('TOKEN', 'BRIDGE', 'SESSION')):
                env.pop(key, None)
        return env

    def discover(self):
        if self._find:
            return self._find()
        env = self.environment()
        result = {}
        for name in (*ENGINES, 'omnigent', 'uv', 'tmux'):
            binary = shutil.which(name, path=env['PATH'])
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
                                           capture_output=True, text=True, timeout=8, env=env)
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

    def lifecycle(self):
        active = self.active()
        state = self.state()
        if state.get('nativeActive') != active:
            with self.lock():
                previous = self.state()
                if not active or previous.get('nativeActive') is False:
                    write(self.root / 'verification.json', {})
                    self.save(phase='needs-login', loginConfirmed=False, issue=None)
                self.save(nativeActive=active)
        return active

    def receipt_ok(self, receipt, state, host_id, dependency):
        return (receipt.get('ok') is True and receipt.get('reply') == SENTINEL
                and receipt.get('cleanup') == 'stopped' and receipt.get('server') == state.get('server')
                and receipt.get('hostId') == host_id and receipt.get('cliVersion') == dependency.get('version'))

    def status(self):
        active = self.active()
        state = self.state()
        deps = self.discover()
        receipts = read(self.root / 'verification.json')
        installed = [name for name in ENGINES if deps.get(name, {}).get('available')]
        response = self.call('status', **({'server': state['server']} if state.get('server') else {})) if active and deps.get('omnigent', {}).get('available') else {}
        data = response.get('data') or {}
        host = data.get('host') or {'connected': False}
        ready = (active and state.get('loginConfirmed') is True and response.get('ok') is True
                 and host.get('connected') is True and bool(installed)
                 and all(data.get('engines', {}).get(name, {}).get('ready') is True
                         and self.receipt_ok(receipts.get(name, {}), state, host.get('id'), deps[name]) for name in installed))
        phase = state.get('phase', 'needs-login')
        if not active:
            phase = 'disabled'
        elif phase not in ('installing', 'connecting', 'verifying', 'disconnecting', 'error', 'needs-login'):
            phase = 'ready' if ready else 'needs-verification'
        operation = read(self.root / 'operation.json', {'running': False})
        if operation.get('running') and not self._thread_running(operation):
            operation = {**operation, 'running': False, 'error': 'OPERATION_INTERRUPTED'}
            if phase in ('installing', 'connecting', 'verifying', 'disconnecting'):
                phase = 'error'
        return {'phase': phase, 'enabled': active, 'loginConfirmed': state.get('loginConfirmed') is True,
                'pairingRequired': active and state.get('loginConfirmed') is not True,
                'server': state.get('server'), 'host': host, 'installedEngines': installed,
                'engines': {name: {**deps.get(name, {}), **data.get('engines', {}).get(name, {})} for name in ENGINES},
                'verification': receipts, 'operation': operation, 'issue': state.get('issue')}

    @staticmethod
    def _thread_running(operation):
        try:
            os.kill(operation.get('pid', 0), 0)
            return operation.get('pid', 0) > 1
        except (OSError, TypeError):
            return False

    def install(self):
        if self._installer:
            return self._installer()
        deps = self.discover()
        if not deps.get('tmux', {}).get('available'):
            from .dependencies import private_tmux
            private_tmux(self.root)
            deps = self.discover()
        if deps.get('omnigent', {}).get('available'):
            return deps
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
        result = subprocess.run([uv, 'tool', 'install', '--python', '3.12', 'omnigent==0.16.0'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, env=env)
        if result.returncode:
            raise Failure('OMNIGENT_INSTALL_FAILED')
        deps = self.discover()
        if not deps.get('omnigent', {}).get('available'):
            raise Failure('OMNIGENT_MISSING')
        return deps

    def require_active(self):
        if not self.active():
            raise Failure('PLUGIN_DISABLED')

    def check_response(self, response):
        if not response.get('ok'):
            code = response.get('error', {}).get('code', 'OPERATION_FAILED')
            raise Failure(code if isinstance(code, str) and re.fullmatch(r'[A-Z_0-9]+', code) else 'OPERATION_FAILED')
        return response.get('data') or {}

    def initialize(self, server, username, password):
        self.require_active()
        # Invalidate approval even when malformed URL/new credentials fail.
        write(self.root / 'verification.json', {})
        self.save(loginConfirmed=False, phase='installing', issue=None)
        from .adapter import normalize_server
        server = normalize_server(server)
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise Failure('FRESH_LOGIN_REQUIRED')
        self.save(server=server)
        self.install()
        self.require_active()
        self.save(phase='connecting')
        self.check_response(self.call('connect', server=server, username=username, password=password, allowManagedHost=True))
        password = None
        self.require_active()
        self.save(loginConfirmed=True, phase='needs-verification')
        return self.verify()

    def verify(self, engine=None, model=None):
        self.require_active()
        state = self.state()
        if not state.get('loginConfirmed'):
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
            response = self.call('verify', server=state['server'], engine=name, timeoutSeconds=120,
                                 **({'model': model} if model else {}))
            data = response.get('data') or {}
            ok = response.get('ok') is True and data.get('ok') is True and data.get('reply') == SENTINEL and data.get('cleanup') == 'stopped'
            receipts[name] = {'engine': name, 'server': state['server'], 'hostId': host_id,
                              'cliVersion': deps[name].get('version'), 'checkedAt': time.time(), 'ok': ok,
                              **{key: data[key] for key in ('model', 'cleanup', 'sessionId', 'url', 'nativeSubmitRecovered') if key in data},
                              **({'reply': SENTINEL} if ok else {'reason': 'SESSION_STOP_FAILED' if data.get('cleanup') == 'stop_failed'
                                  else data.get('reason') or response.get('error', {}).get('code') or 'NO_REPLY'})}
            write(self.root / 'verification.json', receipts)
        self.require_active()
        ok = bool(host_id) and all(self.receipt_ok(receipts.get(name, {}), state, host_id, deps[name]) for name in installed)
        self.save(phase='ready' if ok else 'needs-verification', issue=None if ok else 'INCOMPLETE_CHAT_VERIFICATION')
        return {'ok': ok, 'verification': receipts}

    def catalog(self):
        self.require_active()
        state = self.state()
        if not state.get('loginConfirmed'):
            raise Failure('FRESH_LOGIN_REQUIRED')
        return self.call('catalog', server=state['server'])

    def disconnect(self):
        self.require_active()
        state = self.state()
        write(self.root / 'verification.json', {})
        self.save(phase='disconnecting', loginConfirmed=False)
        result = self.check_response(self.call('disconnect', server=state.get('server')))
        self.save(phase='needs-login', issue=None)
        return result

    def start(self, action, **inputs):
        self.require_active()
        allowed = {'pair': self.initialize, 'verify': self.verify, 'disconnect': self.disconnect, 'prepare': self.install}
        if action not in allowed:
            raise Failure('UNKNOWN_ACTION')
        with self._mutex:
            if self._thread and self._thread.is_alive():
                raise Failure('OPERATION_BUSY')
            lease = self.lock()
            lease.__enter__()
            identifier = uuid.uuid4().hex
            try:
                write(self.root / 'operation.json', {'id': identifier, 'running': True, 'action': action, 'pid': os.getpid()})
                def work():
                    try:
                        result = allowed[action](**inputs)
                        write(self.root / 'operation.json', {'id': identifier, 'running': False, 'ok': result.get('ok', True)})
                    except Exception as error:
                        code = getattr(error, 'code', 'OPERATION_FAILED')
                        code = code if isinstance(code, str) and re.fullmatch(r'[A-Z_0-9]+', code) else 'OPERATION_FAILED'
                        self.save(phase='needs-login' if code in ('NEEDS_LOGIN', 'NEEDS_BROWSER_AUTH', 'FRESH_LOGIN_REQUIRED') else 'error', issue=code)
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

    def activate(self):
        if os.environ.get('OMNIGENT_HERMES_NATIVE_BRIDGE_DIR'):
            return
        try:
            self.lifecycle()
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

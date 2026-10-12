import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hermes_omnigent.runtime import Runtime, Failure, write, read, SENTINEL


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.enabled = True
        self.installed = ['hermes']
        self.online = True
        self.ready = True
        self.fail_login = False
        self.calls = []
        def find():
            return {name: {'available': name in self.installed or name in ['omnigent', 'tmux'],
                           'path': '/image/bin/' + name, 'version': '1.0.0'}
                    for name in ['hermes', 'claude', 'codex', 'pi', 'omnigent', 'tmux']}
        def call(request):
            self.calls.append(request)
            if request['action'] == 'connect':
                return {'ok': False, 'error': {'code': 'NEEDS_LOGIN'}} if self.fail_login else {'ok': True, 'data': {}}
            if request['action'] in ['status', 'catalog']:
                return {'ok': True, 'data': {'host': {'id': 'own-host', 'connected': self.online},
                        'engines': {name: {'ready': self.ready, 'agentId': 'own-module', 'models': [{'id': 'real-model'}]} for name in self.installed}}}
            if request['action'] == 'verify':
                return {'ok': True, 'data': {'ok': True, 'reply': SENTINEL, 'sessionId': 'own-session', 'cleanup': 'stopped'}}
            return {'ok': True, 'data': {'credentialsCleared': True}}
        self.runtime = Runtime(self.home, find=find, call=call, installer=lambda: {'ok': True}, active=lambda: self.enabled)

    def pair(self):
        return self.runtime.initialize('https://server.example', 'account', 'do-not-persist-this-password')

    def test_first_activation_cannot_reuse_ambient_authentication(self):
        self.assertTrue(self.runtime.status()['pairingRequired'])
        with self.assertRaises(Failure) as error:
            self.runtime.verify()
        self.assertEqual(error.exception.code, 'FRESH_LOGIN_REQUIRED')
        self.assertFalse(any(x['action'] == 'verify' for x in self.calls))

    def test_real_hermes_reply_and_stopped_receipt_required_without_installing_other_engines(self):
        self.assertTrue(self.pair()['ok'])
        status = self.runtime.status()
        self.assertEqual(status['phase'], 'ready')
        self.assertEqual(status['installedEngines'], ['hermes'])
        self.assertEqual([x['engine'] for x in self.calls if x['action'] == 'verify'], ['hermes'])
        for path in self.runtime.root.rglob('*.json'):
            self.assertNotIn('do-not-persist-this-password', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_new_rejected_login_invalidates_old_ready(self):
        self.pair()
        self.fail_login = True
        with self.assertRaises(Failure):
            self.pair()
        self.assertFalse(self.runtime.state()['loginConfirmed'])
        self.assertEqual(read(self.runtime.root / 'verification.json'), {})
        self.assertNotEqual(self.runtime.status()['phase'], 'ready')

    def test_enable_disable_resets_approval_without_logging_out_host(self):
        self.runtime.lifecycle()
        self.pair()
        self.enabled = False
        self.runtime.lifecycle()
        self.enabled = True
        self.runtime.lifecycle()
        self.assertFalse(self.runtime.state()['loginConfirmed'])
        self.assertEqual(read(self.runtime.root / 'verification.json'), {})
        self.assertFalse(any(x['action'] == 'disconnect' for x in self.calls))

    def test_disabled_rejects_every_mutation(self):
        self.enabled = False
        for function in [self.runtime.verify, self.runtime.disconnect, self.runtime.reset_pairing,
                         lambda: self.runtime.start('pair', server='https://server.example', username='a', password='p')]:
            with self.assertRaises(Failure) as error:
                function()
            self.assertEqual(error.exception.code, 'PLUGIN_DISABLED')

    def test_updated_cli_or_wrong_host_cannot_inherit_success(self):
        self.pair()
        receipt = read(self.runtime.root / 'verification.json')
        receipt['hermes']['cliVersion'] = '0.9.0'
        write(self.runtime.root / 'verification.json', receipt)
        self.assertEqual(self.runtime.status()['phase'], 'needs-verification')
        receipt['hermes']['cliVersion'] = '1.0.0'
        receipt['hermes']['hostId'] = 'other-machine'
        write(self.runtime.root / 'verification.json', receipt)
        self.assertNotEqual(self.runtime.status()['phase'], 'ready')

    def test_live_offline_or_unconfigured_engine_never_uses_stale_ready(self):
        self.pair()
        self.online = False
        self.assertNotEqual(self.runtime.status()['phase'], 'ready')
        self.online = True
        self.ready = False
        self.assertNotEqual(self.runtime.status()['phase'], 'ready')

    def test_lock_is_held_until_background_job_finishes_and_secrets_are_not_in_receipts(self):
        entered, finish = threading.Event(), threading.Event()
        def install():
            entered.set()
            finish.wait(5)
            return {'ok': True}
        self.runtime._installer = install
        job = self.runtime.start('prepare')
        self.assertTrue(entered.wait(2))
        self.assertTrue(job['running'])
        with self.assertRaises(Failure) as error:
            Runtime(self.home, active=lambda: True).reset_pairing()
        self.assertEqual(error.exception.code, 'OPERATION_BUSY')
        finish.set()
        self.runtime._thread.join(2)
        self.assertFalse(read(self.runtime.root / 'operation.json')['running'])

    def test_cleanup_failure_never_passes(self):
        original = self.runtime._call
        def fail(request):
            result = original(request)
            if request['action'] == 'verify':
                result['data']['cleanup'] = 'stop_failed'
            return result
        self.runtime._call = fail
        self.assertFalse(self.pair()['ok'])
        self.assertNotEqual(self.runtime.status()['phase'], 'ready')

    def test_async_failure_sanitizes_raw_exception_and_returns_safe_error(self):
        def fail():
            raise RuntimeError('password=do-not-show-this')
        self.runtime._installer = fail
        self.runtime.start('prepare')
        self.runtime._thread.join(2)
        result = read(self.runtime.root / 'operation.json')
        self.assertEqual(result['error'], 'OPERATION_FAILED')
        self.assertNotIn('do-not-show-this', json.dumps(result))

    def test_corrupt_state_fails_closed(self):
        self.runtime.root.mkdir()
        (self.runtime.root / 'state.json').write_text('not-json')
        with self.assertRaises(Failure):
            self.runtime.status()

    def test_root_cannot_create_private_state_for_another_service_user(self):
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            if path == self.home:
                return SimpleNamespace(st_uid=10000)
            return original_stat(path, *args, **kwargs)
        with patch('hermes_omnigent.runtime.os.geteuid', return_value=0), patch.object(Path, 'stat', stat):
            with self.assertRaises(Failure) as error:
                self.pair()
            self.assertEqual(error.exception.code, 'PROFILE_USER_MISMATCH')
            with self.assertRaises(Failure):
                self.runtime.start('prepare')
            with self.assertRaises(Failure):
                self.runtime.save(phase='needs-login')
            with self.assertRaises(Failure):
                self.runtime.install()
        self.assertFalse(self.runtime.root.exists())
        self.assertFalse(self.calls)

    def test_non_root_service_can_use_a_root_owned_writable_mount(self):
        with patch('hermes_omnigent.runtime.os.geteuid', return_value=10000):
            self.assertTrue(self.pair()['ok'])
        self.assertEqual(self.runtime.status()['phase'], 'ready')

    def test_old_running_operation_with_reused_pid_is_reported_interrupted(self):
        write(self.runtime.root / 'operation.json', {'running': True, 'pid': os.getpid(),
                                                     'identity': {'start': 'old', 'boot': 'old'}})
        self.runtime.save(phase='connecting')
        state = self.runtime.status()
        self.assertFalse(state['operation']['running'])
        self.assertEqual(state['operation']['error'], 'OPERATION_INTERRUPTED')
        self.assertEqual(state['phase'], 'error')

    def test_changing_server_keeps_old_identity_recoverable_until_disconnect(self):
        self.pair()
        with self.assertRaises(Failure) as error:
            self.runtime.initialize('https://another.example', 'account', 'password')
        self.assertEqual(error.exception.code, 'DISCONNECT_BEFORE_SERVER_CHANGE')
        self.assertEqual(self.runtime.state()['server'], 'https://server.example')
        self.assertFalse(self.runtime.state()['loginConfirmed'])
        self.runtime.disconnect()
        self.assertIsNone(self.runtime.state()['server'])

    def test_restore_restarts_approved_host_without_sending_model_prompt(self):
        self.pair()
        self.calls.clear()
        self.runtime.restore()
        self.assertFalse(any(x['action'] == 'verify' for x in self.calls))
        self.assertEqual(self.runtime.status()['phase'], 'ready')

    def test_namespace_failure_cannot_enable_direct_execution_implicitly(self):
        failed = type('Probe', (), {'returncode': 1})()
        with patch('hermes_omnigent.runtime.subprocess.run', return_value=failed):
            with self.assertRaises(Failure) as error:
                self.runtime.check_sandbox({'bwrap': {'path': '/private/bwrap'}})
        self.assertEqual(error.exception.code, 'SANDBOX_NAMESPACE_UNAVAILABLE')
        self.assertNotEqual(self.runtime.environment()['HERMES_OMNIGENT_DIRECT_HERMES'], '1')

    def test_direct_execution_requires_boolean_user_input_and_resets_on_new_login(self):
        self.runtime.initialize('https://server.example', 'account', 'password', allowDirectHermes=True)
        self.assertEqual(self.runtime.environment()['HERMES_OMNIGENT_DIRECT_HERMES'], '1')
        self.pair()
        self.assertEqual(self.runtime.environment()['HERMES_OMNIGENT_DIRECT_HERMES'], '0')
        with self.assertRaises(Failure):
            self.runtime.initialize('https://server.example', 'account', 'password', allowDirectHermes='true')

    def test_real_sdk_runner_receives_profile_and_explicit_execution_mode(self):
        from omnigent.host.connect import _build_runner_env
        model = {'default': 'configured-model', 'provider': 'custom'}
        (self.home / 'config.yaml').write_text(json.dumps({'model': model}))
        for approved in (False, True):
            with self.subTest(approved=approved):
                self.runtime.save(allowDirectHermes=approved)
                with patch.dict(os.environ, {'OMNIGENT_RUNNER_ENV_PASSTHROUGH': 'EXISTING_SELECTOR',
                                             'EXISTING_SELECTOR': 'keep-me',
                                             'UNRELATED_SECRET': 'never-forward'}):
                    base = self.runtime.environment()
                with patch('omnigent.onboarding.provider_config.load_config', return_value={}):
                    child = _build_runner_env(base, server_url='https://server.example',
                        runner_id='test-runner', binding_token='test-binding', workspace=str(self.home),
                        parent_pid=os.getpid(), harness='hermes-native')
                self.assertEqual(child.get('EXISTING_SELECTOR'), 'keep-me')
                self.assertNotIn('UNRELATED_SECRET', child)
                self.assertEqual(child.get('HERMES_OMNIGENT_PROFILE'), str(self.home))
                self.assertEqual(child.get('HERMES_OMNIGENT_DIRECT_HERMES'), '1' if approved else '0')
                self.assertEqual(child.get('HERMES_OMNIGENT_PYTHON'), base['HERMES_OMNIGENT_PYTHON'])

    def test_old_host_environment_is_restarted_before_restore(self):
        self.pair()
        write(self.runtime.root / 'host-process.json', {'pid': 123})
        previous = self.runtime.host_generation()
        previous.pop('runnerEnvironmentVersion', None)
        write(self.runtime.root / 'host-runtime.json', previous)
        self.calls.clear()
        self.runtime.restore()
        self.assertEqual([x['action'] for x in self.calls].count('restart_host'), 1)
        self.assertFalse(any(x['action'] == 'verify' for x in self.calls))

    def test_status_refreshes_receipts_after_slow_host_lookup(self):
        self.pair()
        completed = read(self.runtime.root / 'verification.json')
        self.runtime.save(phase='verifying')
        write(self.runtime.root / 'verification.json', {})
        original = self.runtime._call
        def finish_during_lookup(request):
            if request['action'] == 'status':
                write(self.runtime.root / 'verification.json', completed)
                self.runtime.save(phase='ready')
            return original(request)
        self.runtime._call = finish_during_lookup
        self.assertEqual(self.runtime.status()['phase'], 'ready')

    def test_force_reinstall_requires_fresh_login_and_execution_consent(self):
        with patch.object(self.runtime, 'installation_identity', return_value='first-install', create=True):
            self.runtime.lifecycle()
            self.runtime.initialize('https://server.example', 'account', 'password', allowDirectHermes=True)
            self.runtime.lifecycle()
            self.assertEqual(self.runtime.status()['phase'], 'ready')
        with patch.object(self.runtime, 'installation_identity', return_value='replacement-install', create=True):
            self.runtime.lifecycle()
        self.assertTrue(self.runtime.status()['pairingRequired'])
        self.assertFalse(self.runtime.state()['allowDirectHermes'])
        self.assertEqual(read(self.runtime.root / 'verification.json'), {})

    def test_reinstall_cannot_reuse_approval_when_filesystem_reuses_directory_inode(self):
        checkout = self.home / 'checkout'
        checkout.mkdir()
        original_stat = Path.stat
        def reused_inode(path, *args, **kwargs):
            if path == checkout:
                return SimpleNamespace(st_ino=42)
            return original_stat(path, *args, **kwargs)
        with patch('hermes_omnigent.runtime.__file__', str(checkout / 'hermes_omnigent/runtime.py')), \
                patch.object(Path, 'stat', reused_inode):
            self.runtime.lifecycle()
            self.runtime.initialize('https://server.example', 'account', 'password', allowDirectHermes=True)
            first = self.runtime.installation_identity()
            import shutil
            shutil.rmtree(checkout)
            checkout.mkdir()
            self.runtime.lifecycle()
            self.assertNotEqual(self.runtime.installation_identity(), first)
            self.assertFalse(self.runtime.state()['loginConfirmed'])
            self.assertFalse(self.runtime.state()['allowDirectHermes'])

    def test_expired_login_during_background_verification_requires_fresh_pairing(self):
        self.pair()
        self.runtime._call = lambda request: {'ok': False, 'error': {'code': 'NEEDS_LOGIN'}}
        self.runtime.start('verify')
        self.runtime._thread.join(2)
        self.assertFalse(self.runtime.state()['loginConfirmed'])
        self.assertTrue(self.runtime.status()['pairingRequired'])


if __name__ == '__main__':
    unittest.main()

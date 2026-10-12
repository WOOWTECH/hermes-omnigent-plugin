import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()

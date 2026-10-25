import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hermes_omnigent import native_gateway as gateway, compat


class NativeGatewayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / f'omnigent-{os.getuid()}' / 'hermes-native' / ('a' * 32) / 'hermes_home'
        self.home.mkdir(parents=True)
        self.patcher = patch.object(gateway.tempfile, 'gettempdir', return_value=temp.name)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_scope_rejects_original_profile_and_symlink(self):
        self.assertEqual(gateway.validate_home(self.home), self.home)
        with self.assertRaisesRegex(RuntimeError, 'HOME_INVALID'):
            gateway.validate_home('/opt/data')
        self.home.rmdir()
        self.home.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'HOME_INVALID'):
            gateway.validate_home(self.home)

    def test_direct_approval_is_required_before_spawning_anything(self):
        with patch.dict(os.environ, {'HERMES_OMNIGENT_DIRECT_HERMES': '0'}):
            with self.assertRaisesRegex(RuntimeError, 'APPROVAL_REQUIRED'):
                gateway.prepare(self.home)

    def test_owned_process_only_stops_recorded_home_uid_and_identity(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 env={**os.environ, 'HERMES_HOME': str(self.home)})
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        receipt = {'pid': child.pid, 'identity': gateway.identity(child.pid)}
        self.assertTrue(gateway.owned_process(self.home, receipt))
        self.assertFalse(gateway.owned_process(self.root, receipt))
        self.assertFalse(gateway.owned_process(self.home, {**receipt, 'identity': {'start': 'other'}}))
        gateway.save_receipt(self.home, receipt)
        self.assertEqual((self.home / '.omnigent-gateway.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(gateway.stop(self.home), 'stopped')
        child.wait(timeout=3)
        self.assertEqual(gateway.stop(self.home), 'stopped')

    def test_unknown_process_is_never_signalled(self):
        gateway.save_receipt(self.home, {'pid': os.getpid(), 'identity': gateway.identity(os.getpid())})
        with patch.object(gateway.os, 'kill') as kill:
            self.assertEqual(gateway.stop(self.home), 'stopped')
        kill.assert_not_called()

    def test_native_boundary_restores_native_dependencies(self):
        with patch.dict(os.environ, {'HERMES_OMNIGENT_PYTHON': '/native/python',
                                     'HERMES_OMNIGENT_PYTHONPATH': '/native/packages', 'PYTHONHOME': '/bad'}), \
             patch.object(compat.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"ok":true}')) as run:
            compat.native_gateway('prepare', self.home)
        self.assertEqual(run.call_args.args[0][0], '/native/python')
        self.assertEqual(run.call_args.kwargs['env']['PYTHONPATH'], '/native/packages')
        self.assertNotIn('PYTHONHOME', run.call_args.kwargs['env'])

    def test_native_errors_do_not_include_subprocess_output(self):
        with patch.dict(os.environ, {'HERMES_OMNIGENT_PYTHON': '/native/python'}), \
             patch.object(compat.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='private credentials')):
            with self.assertRaisesRegex(RuntimeError, '^HERMES_GATEWAY_FAILED$'):
                compat.native_gateway('prepare', self.home)

    def test_prepares_official_private_gateway_and_records_ownership(self):
        discover = Mock(side_effect=[SimpleNamespace(state='absent'), SimpleNamespace(state='ready')])
        child = Mock(pid=43210)
        child.poll.return_value = None
        spawn = Mock(return_value=child)
        modules = {'hermes_cli': SimpleNamespace(),
                   'hermes_cli.gateway_runtime': SimpleNamespace(discover_gateway_endpoint=discover),
                   'hermes_cli.gateway_runtime_start': SimpleNamespace(spawn_unmanaged_gateway=spawn)}
        with patch.dict(os.environ, {'HERMES_OMNIGENT_DIRECT_HERMES': '1'}), patch.dict(sys.modules, modules), \
             patch.object(gateway, 'identity', return_value={'start': 'test', 'boot': 'test'}), \
             patch.object(gateway, 'owned_process', return_value=True):
            self.assertEqual(gateway.prepare(self.home), 'ready')
            self.assertEqual(os.environ['HERMES_GATEWAY_NO_SUPERVISE'], '1')
        self.assertTrue(spawn.call_args.kwargs['idle_exit'])
        self.assertEqual(spawn.call_args.args[0], self.home)
        self.assertEqual(gateway.read_receipt(self.home)['pid'], child.pid)
        child.terminate.assert_not_called()

    def test_ready_gateway_with_unknown_owner_is_not_adopted(self):
        discover = Mock(return_value=SimpleNamespace(state='ready'))
        spawn = Mock()
        modules = {'hermes_cli': SimpleNamespace(),
                   'hermes_cli.gateway_runtime': SimpleNamespace(discover_gateway_endpoint=discover),
                   'hermes_cli.gateway_runtime_start': SimpleNamespace(spawn_unmanaged_gateway=spawn)}
        with patch.dict(os.environ, {'HERMES_OMNIGENT_DIRECT_HERMES': '1'}), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, 'OWNER_UNKNOWN'):
                gateway.prepare(self.home)
        spawn.assert_not_called()

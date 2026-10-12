import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch
import omnigent

from hermes_omnigent import compat, patches
from hermes_omnigent.runtime import Failure


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sdk = Path(self.temp.name)
        source = Path(omnigent.__file__).resolve().parent
        for relative in ['host/connect.py', 'harnesses/hermes_native/bridge.py', 'runner/app.py', 'runner/native/orchestration.py']:
            target = self.sdk / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)

    def test_sdk_patch_is_idempotent_and_compiles_all_private_files(self):
        for file, before, after in patches.make_edits(self.sdk):
            self.assertNotEqual(before, after)
            compile(after, str(file), 'exec')
            file.write_text(after)
        for file, before, after in patches.make_edits(self.sdk):
            self.assertEqual(before, after)

    def test_unknown_upstream_shape_fails_before_changing_any_file(self):
        file = self.sdk / 'runner/app.py'
        file.write_text(file.read_text().replace('if body_type == "model_change":', 'if body_type == "renamed":'))
        before = {p: p.read_bytes() for p in self.sdk.rglob('*.py')}
        with self.assertRaises(Failure):
            patches.make_edits(self.sdk)
        self.assertEqual(before, {p: p.read_bytes() for p in self.sdk.rglob('*.py')})

    def test_installer_refuses_modifying_a_global_sdk(self):
        with self.assertRaises(Failure) as error:
            compat.install_private(self.sdk, self.sdk, '/fake/python')
        self.assertEqual(error.exception.code, 'COMPAT_REQUIRES_PRIVATE_RUNTIME')

    def test_actual_host_handler_serves_hermes_inventory_and_sanitizes_failure(self):
        from omnigent.host.frames import HostModelOptionsFrame, HostModelOptionsResultFrame
        import functools
        edited = next(after for file, before, after in patches.make_edits(self.sdk) if str(file).endswith('host/connect.py'))
        tree = ast.parse(edited)
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == '_handle_model_options')
        namespace = {'functools': functools, 'asyncio': asyncio, 'canonicalize_harness': lambda value: value,
                     '_with_model_configuration_source': lambda rows, **kwargs: rows,
                     'HostModelOptionsFrame': HostModelOptionsFrame, 'HostModelOptionsResultFrame': HostModelOptionsResultFrame}
        code = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
        ast.fix_missing_locations(code)
        exec(compile(code, '<private-host-handler>', 'exec'), namespace)
        frame = HostModelOptionsFrame(request_id='own-request', harness='hermes-native')
        with patch.object(compat, 'hermes_models', return_value=[{'id': 'real-model', 'displayName': 'Real model'}]):
            result = asyncio.run(namespace['_handle_model_options'](None, frame))
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.models[0]['id'], 'real-model')
        with patch.object(compat, 'hermes_models', side_effect=RuntimeError('secret raw provider error')):
            result = asyncio.run(namespace['_handle_model_options'](None, frame))
        self.assertEqual(result.status, 'failed')
        self.assertNotIn('secret', result.error)

    def test_model_identifiers_are_whitelisted_before_any_terminal_command(self):
        fake = types.SimpleNamespace(returncode=0, stdout=json.dumps([{'id': 'good/model'}, {'id': 'bad\ncommand'}, {'id': '--flag'}, {'id': 'space command'}]))
        with patch.dict('os.environ', {'HERMES_OMNIGENT_PYTHON': '/sdk/python'}), patch.object(compat.subprocess, 'run', return_value=fake):
            self.assertEqual([row['id'] for row in compat.hermes_models()], ['good/model'])


if __name__ == '__main__':
    unittest.main()

import importlib.util
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    def test_credential_fields_never_appear_in_model_tools(self):
        spec = importlib.util.spec_from_file_location('hermes_entry_test', ROOT / '__init__.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tools = []
        context = types.SimpleNamespace(register_tool=lambda **kwargs: tools.append(kwargs),
                                        register_hook=lambda *args: None, register_command=lambda *args: None)
        runtime = types.SimpleNamespace(activate=lambda: None)
        with patch.object(module, 'get_runtime', return_value=runtime), patch.dict(os.environ, {}, clear=True):
            module.register(context)
        self.assertEqual(len(tools), 4)
        for tool in tools:
            schema = tool['schema']['parameters']
            self.assertNotIn('password', json.dumps(schema))
            self.assertNotIn('username', json.dumps(schema))
            self.assertFalse(schema['additionalProperties'])

    def test_native_hermes_child_does_not_run_pairing_lifecycle(self):
        spec = importlib.util.spec_from_file_location('hermes_child_test', ROOT / '__init__.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(os.environ, {'OMNIGENT_HERMES_NATIVE_BRIDGE_DIR': '/owned/native/bridge'}), \
                patch.object(module, 'get_runtime', side_effect=AssertionError('recursive lifecycle')):
            module.register(None)


if __name__ == '__main__':
    unittest.main()

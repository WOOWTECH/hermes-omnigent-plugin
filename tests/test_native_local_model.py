import json
import types
import unittest
from unittest.mock import patch

from hermes_omnigent import compat, native_local_model


class SharedLocalModelTests(unittest.TestCase):
    def resolve(self, result):
        modules = {
            'hermes_cli': types.ModuleType('hermes_cli'),
            'hermes_cli.local_runtime': types.ModuleType('hermes_cli.local_runtime'),
            'hermes_cli.local_runtime.endpoint': types.SimpleNamespace(managed_root=lambda: result),
        }
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/owned/profile'}):
            return native_local_model.connection()

    def test_owned_loopback_server_is_reused_without_copying_runtime_state(self):
        result = self.resolve(('http://127.0.0.1:18434', 'private-local-key'))
        self.assertEqual(result, {'provider': 'custom', 'base_url': 'http://127.0.0.1:18434/v1',
                                 'api_key': 'private-local-key', 'api_mode': 'chat_completions'})

    def test_no_owned_server_or_external_address_fails_without_revealing_key(self):
        for result in [None, ('https://other.example', 'private-local-key'),
                       ('http://name:secret@localhost', ''), ('http://localhost?token=secret', '')]:
            with self.assertRaisesRegex(RuntimeError, '^HERMES_LOCAL_MODEL_UNAVAILABLE$'):
                self.resolve(result)

    def test_cloud_config_does_not_invoke_local_provider_helper(self):
        config = {'model': {'provider': 'custom', 'default': 'cloud/model'}}
        with patch.object(compat.subprocess, 'run') as run:
            self.assertIs(compat.shared_local_model_config(config), config)
            run.assert_not_called()

    def test_native_interpreter_uses_original_profile_and_keeps_selected_model(self):
        original = {'model': {'provider': 'llamacpp', 'default': 'local/model', 'base_url': 'http://old-port/v1'}}
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(self.resolve(('http://localhost:18434', 'private-local-key'))))
        env = {'HERMES_OMNIGENT_PROFILE': '/original/profile', 'HERMES_HOME': '/conversation/home',
               'HERMES_OMNIGENT_PYTHON': '/native/python', 'HERMES_OMNIGENT_PYTHONPATH': '/native/modules'}
        with patch.dict('os.environ', env), patch.object(compat.subprocess, 'run', return_value=result) as run:
            config = compat.shared_local_model_config(original)
            self.assertEqual(config['model']['default'], 'local/model')
            self.assertEqual(config['model']['provider'], 'custom')
            self.assertEqual(original['model']['provider'], 'llamacpp')
            self.assertEqual(run.call_args.kwargs['env']['HERMES_HOME'], '/original/profile')
            self.assertEqual(run.call_args.kwargs['env']['PYTHONPATH'], '/native/modules')
            result.returncode = 1
            result.stdout = 'private-local-key'
            with self.assertRaisesRegex(RuntimeError, '^HERMES_LOCAL_MODEL_UNAVAILABLE$'):
                compat.shared_local_model_config(original)

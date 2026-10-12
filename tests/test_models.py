import contextlib
import io
import json
import runpy
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


class ModelInventoryTests(unittest.TestCase):
    def inventory(self, *, unavailable=False):
        context = types.SimpleNamespace(current_provider='custom', current_base_url='https://provider.example/v1')
        build = Mock(return_value={'model': 'real/default', 'providers': [
            {'slug': 'custom', 'is_current': True, 'models': ['real/default']},
            {'slug': 'other', 'is_current': False, 'models': ['unrelated/model']},
        ]})
        resolve = Mock(return_value={'api_key': 'provider-key-stay-private',
                                     'base_url': context.current_base_url, 'api_mode': 'chat_completions'})
        def endpoint(key, base, **kwargs):
            if unavailable:
                raise RuntimeError('sensitive upstream diagnostic')
            return ['real/default', 'real/fallback'] if key == 'provider-key-stay-private' and base == context.current_base_url else None
        fetch = Mock(side_effect=endpoint)
        modules = {
            'hermes_cli': types.ModuleType('hermes_cli'),
            'hermes_cli.config': types.SimpleNamespace(load_env=lambda: {}),
            'hermes_cli.inventory': types.SimpleNamespace(load_picker_context=lambda: context, build_models_payload=build),
            'hermes_cli.runtime_provider': types.SimpleNamespace(resolve_runtime_provider=resolve),
            'hermes_cli.models': types.SimpleNamespace(fetch_api_models=fetch),
        }
        output = io.StringIO()
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/private/test-profile'}), contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hermes_omnigent/models.py'), run_name='__main__')
        self.assertNotIn('provider-key-stay-private', output.getvalue())
        self.assertNotIn('sensitive', output.getvalue())
        return json.loads(output.getvalue()), build, resolve, fetch

    def test_authenticated_custom_endpoint_exposes_alternatives_without_other_providers(self):
        rows, build, resolve, fetch = self.inventory()
        self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])
        self.assertTrue(rows[0]['isDefault'])
        self.assertFalse(rows[1]['isDefault'])
        resolve.assert_called_once_with(requested='custom')
        self.assertTrue(build.call_args.kwargs['for_picker'])

    def test_unavailable_catalog_keeps_configured_model_without_leaking_diagnostics(self):
        rows, _, _, _ = self.inventory(unavailable=True)
        self.assertEqual([row['id'] for row in rows], ['real/default'])


if __name__ == '__main__':
    unittest.main()

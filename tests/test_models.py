import contextlib
import io
import json
import runpy
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


class ModelInventoryTests(unittest.TestCase):
    def inventory(self, *, unavailable=False, automatic=False):
        context = types.SimpleNamespace(current_provider='auto' if automatic else 'custom', current_base_url='https://provider.example/v1')
        build = Mock(return_value={'model': 'real/default', 'providers': [
            {'slug': 'custom', 'is_current': True, 'models': ['real/default']},
            {'slug': 'other', 'is_current': False, 'models': ['unrelated/model']},
        ]})
        resolve = Mock(return_value={'api_key': 'provider-key-stay-private',
                                     'base_url': context.current_base_url, 'api_mode': 'chat_completions'})
        if automatic:
            build.return_value['providers'] = []
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
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/private/test-profile'}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hermes_omnigent/models.py'), run_name='__main__')
        self.assertNotIn('provider-key-stay-private', output.getvalue())
        self.assertNotIn('sensitive', output.getvalue())
        return [row for row in json.loads(output.getvalue()) if 'id' in row], build, resolve, fetch

    def test_authenticated_custom_endpoint_exposes_alternatives_without_other_providers(self):
        rows, build, resolve, fetch = self.inventory()
        self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])
        self.assertTrue(rows[0]['isDefault'])
        self.assertFalse(rows[1]['isDefault'])
        resolve.assert_called_once_with(requested='custom')
        self.assertTrue(build.call_args.kwargs['for_picker'])

    def test_auto_provider_with_configured_endpoint_uses_native_runtime_models(self):
        rows, _, resolve, _ = self.inventory(automatic=True)
        self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])
        resolve.assert_called_once_with(requested='auto')

    def test_unavailable_auto_catalog_keeps_the_explicitly_configured_model(self):
        rows, _, _, _ = self.inventory(automatic=True, unavailable=True)
        self.assertEqual([row['id'] for row in rows], ['real/default'])

    def test_unavailable_catalog_keeps_configured_model_without_leaking_diagnostics(self):
        rows, _, _, _ = self.inventory(unavailable=True)
        self.assertEqual([row['id'] for row in rows], ['real/default'])


class ModelStatusRecordTests(unittest.TestCase):
    """The caller keeps an answer only when this script says in the list itself that the endpoint's own list is in it. The probe stays
    one try of 5 s: Hermes' fetch_api_models asks two URLs per call, each with that timeout, and remembers an all-timeout result for a
    minute inside the process, so a longer timeout would overrun Omnigent's 15 s picker wait and a second try could not help."""

    def run_inventory(self, answers=(), *, provider='custom', base_url='https://provider.example/v1', listed=None, resolve_error=None):
        context = types.SimpleNamespace(current_provider=provider, current_base_url=base_url)
        providers = [{'slug': provider, 'is_current': True, 'models': listed if listed is not None else ['real/default']}]
        build = Mock(return_value={'model': 'real/default', 'providers': providers})
        resolve = Mock(return_value={'api_key': 'provider-key-stay-private', 'base_url': base_url, 'api_mode': 'chat_completions'},
                       side_effect=resolve_error)
        fetch = Mock(side_effect=list(answers))
        modules = {
            'hermes_cli': types.ModuleType('hermes_cli'),
            'hermes_cli.config': types.SimpleNamespace(load_env=lambda: {}),
            'hermes_cli.inventory': types.SimpleNamespace(load_picker_context=lambda: context, build_models_payload=build),
            'hermes_cli.runtime_provider': types.SimpleNamespace(resolve_runtime_provider=resolve),
            'hermes_cli.models': types.SimpleNamespace(fetch_api_models=fetch),
        }
        out, err = io.StringIO(), io.StringIO()
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/private/test-profile'}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hermes_omnigent/models.py'), run_name='__main__')
        self.assertNotIn('provider-key-stay-private', out.getvalue() + err.getvalue())
        self.assertNotIn('sensitive', out.getvalue() + err.getvalue())
        self.assertEqual(err.getvalue(), '')  # nothing is said on stderr: the caller does not read it
        listing = json.loads(out.getvalue())
        self.assertEqual(listing[-1].keys(), {'inventory'})  # the status record is always the last element, and the only one without an id
        self.assertTrue(all('id' in row for row in listing[:-1]))
        return listing[:-1], listing[-1]['inventory'], fetch

    def test_the_endpoint_is_asked_once_with_the_five_second_timeout(self):
        rows, status, fetch = self.run_inventory([['real/default', 'real/fallback']])
        self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs['timeout'], 5.0)
        self.assertEqual(status, 'complete')

    def test_an_endpoint_that_lists_nothing_is_reported_as_degraded(self):
        for answer in (None, [], RuntimeError('sensitive upstream diagnostic')):
            with self.subTest(answer=repr(answer)):
                rows, status, fetch = self.run_inventory([answer])
                self.assertEqual([row['id'] for row in rows], ['real/default'])  # the configured model, as before
                fetch.assert_called_once()
                self.assertEqual(status, 'degraded')

    def test_an_endpoint_that_cannot_even_be_resolved_is_reported_as_degraded(self):
        rows, status, fetch = self.run_inventory(resolve_error=RuntimeError('sensitive'))
        self.assertEqual([row['id'] for row in rows], ['real/default'])
        fetch.assert_not_called()
        self.assertEqual(status, 'degraded')

    def test_a_provider_with_its_own_list_never_probes_an_endpoint_and_is_complete(self):
        rows, status, fetch = self.run_inventory([], provider='openai-codex', base_url='', listed=['m1', 'm2'])
        self.assertEqual([row['id'] for row in rows], ['m1', 'm2'])
        fetch.assert_not_called()
        self.assertEqual(status, 'complete')

    def test_an_older_caller_reads_the_new_list_without_noticing_the_status_record(self):
        """During an in-place update the compat.py loaded in memory is the old one: it keeps only elements that have an id."""
        rows, _, _ = self.run_inventory([['real/default', 'real/fallback']])
        out = io.StringIO()
        modules = {
            'hermes_cli': types.ModuleType('hermes_cli'),
            'hermes_cli.config': types.SimpleNamespace(load_env=lambda: {}),
            'hermes_cli.inventory': types.SimpleNamespace(
                load_picker_context=lambda: types.SimpleNamespace(current_provider='custom', current_base_url='https://provider.example/v1'),
                build_models_payload=Mock(return_value={'model': 'real/default', 'providers': [
                    {'slug': 'custom', 'is_current': True, 'models': ['real/default']}]})),
            'hermes_cli.runtime_provider': types.SimpleNamespace(resolve_runtime_provider=Mock(return_value={'api_key': 'k', 'base_url': 'u', 'api_mode': 'm'})),
            'hermes_cli.models': types.SimpleNamespace(fetch_api_models=Mock(return_value=['real/default', 'real/fallback'])),
        }
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/p'}), contextlib.redirect_stdout(out):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hermes_omnigent/models.py'), run_name='__main__')
        import re
        parsed = json.loads(out.getvalue())
        # the filter of the released compat.hermes_models(), verbatim
        old_filter = [row for row in parsed if isinstance(row, dict) and isinstance(row.get('id'), str)
                      and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_.:\-]{0,200}', row['id'])]
        self.assertEqual([row['id'] for row in old_filter], ['real/default', 'real/fallback'])
        self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])


if __name__ == '__main__':
    unittest.main()

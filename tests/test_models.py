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
                patch('time.sleep'), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
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


class ModelProbeBudgetTests(unittest.TestCase):
    """The active endpoint's /models is the only source of a custom provider's list: a failed probe must be retried inside a
    budget that fits Omnigent's 15 s picker timeout, and an answer built without it must say so instead of looking complete."""

    def run_inventory(self, answers=(), *, behavior=None, clock=None, provider='custom', base_url='https://provider.example/v1', listed=None):
        context = types.SimpleNamespace(current_provider=provider, current_base_url=base_url)
        providers = [{'slug': provider, 'is_current': True, 'models': listed if listed is not None else ['real/default']}]
        build = Mock(return_value={'model': 'real/default', 'providers': providers})
        resolve = Mock(return_value={'api_key': 'provider-key-stay-private', 'base_url': base_url, 'api_mode': 'chat_completions'})
        fetch = Mock(side_effect=behavior or list(answers))
        modules = {
            'hermes_cli': types.ModuleType('hermes_cli'),
            'hermes_cli.config': types.SimpleNamespace(load_env=lambda: {}),
            'hermes_cli.inventory': types.SimpleNamespace(load_picker_context=lambda: context, build_models_payload=build),
            'hermes_cli.runtime_provider': types.SimpleNamespace(resolve_runtime_provider=resolve),
            'hermes_cli.models': types.SimpleNamespace(fetch_api_models=fetch),
        }
        out, err = io.StringIO(), io.StringIO()
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {'HERMES_OMNIGENT_PROFILE': '/private/test-profile'}), \
                patch('time.sleep', side_effect=(lambda seconds: setattr(clock, 'now', clock.now + seconds)) if clock else None) as sleep, \
                patch('time.monotonic', side_effect=(lambda: clock.now)) if clock else contextlib.nullcontext(), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hermes_omnigent/models.py'), run_name='__main__')
        self.assertNotIn('provider-key-stay-private', out.getvalue() + err.getvalue())
        self.assertNotIn('sensitive', out.getvalue() + err.getvalue())
        return json.loads(out.getvalue()), err.getvalue(), fetch, sleep

    def test_a_transient_failure_is_retried_and_the_list_stays_complete(self):
        for first in (None, RuntimeError('sensitive upstream diagnostic')):
            with self.subTest(first=repr(first)):
                rows, err, fetch, sleep = self.run_inventory([first, ['real/default', 'real/fallback']])
                self.assertEqual([row['id'] for row in rows], ['real/default', 'real/fallback'])
                self.assertEqual(fetch.call_count, 2)
                self.assertNotIn('HERMES_MODELS_DEGRADED', err)
                sleep.assert_called_once()

    def test_an_endpoint_that_never_lists_models_is_reported_as_degraded(self):
        rows, err, fetch, _ = self.run_inventory([None, RuntimeError('sensitive upstream diagnostic')])
        self.assertEqual([row['id'] for row in rows], ['real/default'])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(err.splitlines().count('HERMES_MODELS_DEGRADED'), 1)

    def test_an_answer_that_needed_no_retry_is_not_reported_as_degraded(self):
        rows, err, fetch, sleep = self.run_inventory([['real/default', 'real/fallback']])
        self.assertEqual(len(rows), 2)
        fetch.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(err, '')

    def test_the_two_attempts_fit_inside_the_picker_budget_and_the_first_is_longer_than_the_old_five_seconds(self):
        clock = types.SimpleNamespace(now=1000.0)

        def endpoint(key, base, timeout, api_mode):  # never answers: each attempt uses the whole timeout it was given
            clock.now += timeout
            return None
        _, _, fetch, _ = self.run_inventory(behavior=endpoint, clock=clock)
        timeouts = [call.kwargs['timeout'] for call in fetch.call_args_list]
        self.assertGreater(timeouts[0], 5.0)
        self.assertEqual(len(timeouts), 2)
        # Omnigent waits 15 s for the whole inventory and the imports and the picker inventory take 2-4 s of that.
        self.assertLessEqual(clock.now - 1000.0, 9.0 + 1e-6)

    def test_a_provider_with_its_own_list_never_probes_an_endpoint(self):
        rows, err, fetch, _ = self.run_inventory([], provider='openai-codex', base_url='', listed=['m1', 'm2'])
        self.assertEqual([row['id'] for row in rows], ['m1', 'm2'])
        fetch.assert_not_called()
        self.assertEqual(err, '')


if __name__ == '__main__':
    unittest.main()

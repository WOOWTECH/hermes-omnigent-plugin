import ast
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
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
        for relative in ['host/connect.py', 'harnesses/hermes_native/bridge.py', 'harnesses/hermes_native/forwarder.py', 'runner/app.py', 'runner/model_option_routes.py', 'runner/native/orchestration.py', 'onboarding/harness_readiness.py']:
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

    def test_native_home_keeps_the_users_config_schema_version(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('hermes_native/bridge.py'))
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '_USER_CONFIG_KEYS' for t in n.targets))
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<config-keys>', 'exec'), namespace)
        self.assertIn('_config_version', namespace['_USER_CONFIG_KEYS'])

    def test_native_home_inherits_the_users_mcp_servers(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('hermes_native/bridge.py'))
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '_USER_CONFIG_KEYS' for t in n.targets))
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<config-keys>', 'exec'), namespace)
        self.assertIn('mcp_servers', namespace['_USER_CONFIG_KEYS'])

    def test_native_home_sees_the_profiles_skills(self):
        from hermes_omnigent.compat import shared_user_skills
        with tempfile.TemporaryDirectory() as profile:
            with patch.dict(os.environ, {'HERMES_OMNIGENT_PROFILE': profile}):
                self.assertEqual(shared_user_skills({'model': 'm'}), {'model': 'm'})
                (Path(profile) / 'skills').mkdir()
                self.assertEqual(shared_user_skills({'model': 'm'}),
                                 {'model': 'm', 'skills': {'external_dirs': [str(Path(profile) / 'skills')]}})

    def test_native_home_turns_background_review_off(self):
        from hermes_omnigent.compat import no_background_review
        self.assertEqual(no_background_review({'model': 'm'}),
                         {'model': 'm', 'auxiliary': {'background_review': {'enabled': False}}})
        self.assertEqual(no_background_review({'auxiliary': {'vision': {'model': 'v'}, 'background_review': {'enabled': True}}}),
                         {'auxiliary': {'vision': {'model': 'v'}, 'background_review': {'enabled': False}}})
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('hermes_native/bridge.py'))
        self.assertIn('no_background_review(shared_user_skills(', source)

    def test_readiness_probes_hermes_aliases_once_and_allows_cold_start(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('onboarding/harness_readiness.py'))
        namespace = {}
        exec(compile(source, '<private-readiness>', 'exec'), namespace)
        seen = []
        namespace['_harness_availability'] = lambda name: seen.append(name) or True
        result = namespace['configured_harness_map']()
        self.assertTrue(result['hermes-native'])
        hermes = namespace['_HERMES_NATIVE_HARNESSES'] | {namespace['HERMES_KEY']}
        self.assertEqual(len([name for name in seen if name in hermes]), 1)
        timeouts = {}
        namespace['harness_cli_installed'] = lambda key, timeout: timeouts.update({key: timeout}) or True
        namespace['_binary_availability_reason']('hermes')
        namespace['_binary_availability_reason']('pi')
        self.assertEqual(timeouts['hermes'], 30)
        self.assertEqual(timeouts['pi'], namespace['READINESS_CLI_PROBE_TIMEOUT_S'])

    def test_mcp_keeps_runner_temp_root_for_secure_bridge_validation(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('hermes_native/bridge.py'))
        self.assertIn('"TMPDIR": tempfile.gettempdir()', source)
        self.assertIn('"HERMES_OMNIGENT_PROFILE": os.environ.get("HERMES_OMNIGENT_PROFILE", "")', source)

    def test_legacy_migration_precedes_every_initial_bridge_directory_creation(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('hermes_native/bridge.py'))
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                    and n.name == 'bridge_dir_for_session_id')
        self.assertIn('migrate_legacy(target)', ast.unparse(node))

    def test_forwarder_probe_never_creates_an_empty_native_database(self):
        edits = patches.make_edits(self.sdk)
        source = next((after for file, before, after in edits if str(file).endswith('hermes_native/forwarder.py')),
                      (self.sdk / 'harnesses/hermes_native/forwarder.py').read_text())
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_connect_ro')
        namespace = {'sqlite3': sqlite3, 'Path': Path}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<readonly-probe>', 'exec'), namespace)
        db = self.sdk / 'not-yet-created.db'
        result = namespace['_connect_ro'](db)
        if result is not None:
            result.close()
        self.assertIsNone(result)
        self.assertFalse(db.exists())
        with sqlite3.connect(db) as writer:
            writer.execute('CREATE TABLE messages (id INTEGER)')
        writer.close()
        reader = namespace['_connect_ro'](db)
        self.assertEqual(reader.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        reader.close()

    def patched(self, relative):
        return next(after for file, before, after in patches.make_edits(self.sdk) if str(file).endswith(relative))

    def test_only_the_users_direct_run_consent_turns_the_terminal_sandbox_off(self):
        """The patched Hermes terminal launch: sandbox none if and only if HERMES_OMNIGENT_DIRECT_HERMES is 1."""
        from omnigent.inner.datamodel import OSEnvSandboxSpec
        tree = ast.parse(self.patched('runner/native/orchestration.py'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_auto_create_hermes_terminal')
        spec = next(n for n in ast.walk(function) if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == 'OSEnvSpec')
        keyword = next(k for k in spec.keywords if k.arg == 'sandbox')
        expression = compile(ast.Expression(keyword.value), '<patched-sandbox>', 'eval')
        inherited = types.SimpleNamespace(sandbox='the-agents-own-sandbox')

        def sandbox(env, agent_os_env):
            with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
                if env.get('HERMES_OMNIGENT_DIRECT_HERMES') is None:
                    os.environ.pop('HERMES_OMNIGENT_DIRECT_HERMES', None)
                return eval(expression, {'os': os, 'OSEnvSandboxSpec': OSEnvSandboxSpec, 'agent_os_env': agent_os_env})

        for agent in (None, inherited):
            consent = sandbox({'HERMES_OMNIGENT_DIRECT_HERMES': '1'}, agent)
            self.assertIsInstance(consent, OSEnvSandboxSpec)
            self.assertEqual(consent.type, 'none')
        for env in ({'HERMES_OMNIGENT_DIRECT_HERMES': '0'}, {'HERMES_OMNIGENT_DIRECT_HERMES': ''}, {'HERMES_OMNIGENT_DIRECT_HERMES': 'true'}, {}):
            self.assertIsNone(sandbox(env, None))
            self.assertEqual(sandbox(env, inherited), 'the-agents-own-sandbox')

    def route_namespace(self):
        tree = ast.parse(self.patched('runner/model_option_routes.py'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_session_model_options')
        class App:  # the route decorators of the real FastAPI app; here they return the function unchanged
            def __getattr__(self, name):
                return lambda *args, **kwargs: (lambda function: function)

        namespace = {'asyncio': asyncio, 'app': App(), 'JSONResponse': lambda content, status_code=200: ('json', status_code, content),
                     '_logger': logging.getLogger('test-model-route')}
        harness = {'name': 'hermes-native'}
        namespace['_session_harness_name'] = lambda session_id: harness['name']

        async def claude(session_id):
            return ('claude', session_id)

        namespace['get_session_claude_model_options'] = claude
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<patched-model-route>', 'exec'), namespace)
        return namespace, harness

    def test_the_session_model_route_lists_hermes_models_and_leaves_other_harnesses_alone(self):
        namespace, harness = self.route_namespace()
        with patch('hermes_omnigent.compat.hermes_models', return_value=[{'id': 'sol'}]):
            self.assertEqual(asyncio.run(namespace['get_session_model_options']('s1')), ('json', 200, {'models': [{'id': 'sol'}]}))
            harness['name'] = 'claude-native'
            self.assertEqual(asyncio.run(namespace['get_session_model_options']('s2')), ('claude', 's2'))

    def test_the_patched_route_only_needs_module_names_the_sdk_defines(self):
        """route_namespace() supplies its own globals; the real module must define the ones the patched text uses."""
        import omnigent.runner.model_option_routes as routes
        for name in ('asyncio', 'JSONResponse', '_logger'):
            self.assertTrue(hasattr(routes, name), name)

    def test_the_session_model_route_reports_an_unavailable_inventory_as_a_retryable_503(self):
        """Like the SDK's own catalog routes: 503 and a stable code, never an unhandled exception (a 500) or a raw diagnostic."""
        namespace, _ = self.route_namespace()
        for failure in (RuntimeError('HERMES_MODELS_UNAVAILABLE secret provider diagnostic'), ValueError('bad json'),
                        __import__('subprocess').TimeoutExpired('python', 45)):
            with self.subTest(failure=type(failure).__name__), patch('hermes_omnigent.compat.hermes_models', side_effect=failure), \
                    self.assertLogs('test-model-route', 'WARNING'):
                result = asyncio.run(namespace['get_session_model_options']('s1'))
            self.assertEqual(result[:2], ('json', 503))
            self.assertEqual(result[2]['error'], 'hermes_native_model_options_failed')
            self.assertNotIn('secret', json.dumps(result[2]))

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

    def test_model_subprocess_uses_hermes_import_path_without_contaminating_sdk(self):
        fake = types.SimpleNamespace(returncode=0, stdout='[]')
        with patch.dict('os.environ', {'HERMES_OMNIGENT_PYTHON': '/sdk/python',
                                      'HERMES_OMNIGENT_PYTHONPATH': '/native/hermes/site'}), \
             patch.object(compat.subprocess, 'run', return_value=fake) as run:
            compat.hermes_models()
        self.assertEqual(run.call_args.kwargs['env']['PYTHONPATH'], '/native/hermes/site')

    def test_model_identifiers_are_whitelisted_before_any_terminal_command(self):
        fake = types.SimpleNamespace(returncode=0, stdout=json.dumps([{'id': 'good/model'}, {'id': 'bad\ncommand'}, {'id': '--flag'}, {'id': 'space command'}]))
        with patch.dict('os.environ', {'HERMES_OMNIGENT_PYTHON': '/sdk/python'}), patch.object(compat.subprocess, 'run', return_value=fake):
            self.assertEqual([row['id'] for row in compat.hermes_models()], ['good/model'])

    def test_stop_of_already_exited_terminal_is_idempotent(self):
        with patch.object(compat.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stderr='no server running on socket')) as run:
            compat.stop_hermes_terminal({'socket_path': '/missing/socket', 'tmux_target': 'own'})
        self.assertEqual(run.call_count, 1)

    def test_delivery_requires_exact_user_message_not_a_terminal_response(self):
        db = self.sdk / 'delivery.db'
        writer = sqlite3.connect(db)
        try:
            writer.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)')
            writer.executemany('INSERT INTO messages VALUES (?,?,?)', [
                (1, 'user', 'expected message'), (2, 'assistant', 'expected message'),
                (3, 'user', ']11;rgb:ffff/ffff/ffff[?1;2c')])
            writer.commit()
            self.assertFalse(compat.await_hermes_message(db, 1, 'expected message', 0))
            writer.execute('INSERT INTO messages VALUES (4,?,?)', ('user', 'expected message\n'))
            writer.commit()
            self.assertTrue(compat.await_hermes_message(db, 1, 'expected message', 0))
        finally:
            writer.close()
        missing = self.sdk / 'missing.db'
        self.assertFalse(compat.await_hermes_message(missing, 0, 'message', 0))
        self.assertFalse(missing.exists())

    def test_a_slash_command_is_delivered_once_with_or_without_a_transcript_row(self):
        from hermes_omnigent import compat
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'state.db'
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)')
            connection.commit()
            with patch.object(compat, 'SLASH_COMMAND_GRACE_S', 0):
                self.assertTrue(compat.await_hermes_message(db, 0, '/model glm', 5))
            self.assertFalse(compat.await_hermes_message(db, 0, 'plain text', 0))
            connection.execute("INSERT INTO messages (role, content) VALUES ('user', 'expanded skill prompt')")
            connection.commit()
            connection.close()
            self.assertTrue(compat.await_hermes_message(db, 0, '/some-skill go', 5))
            self.assertFalse(compat.await_hermes_message(db, 0, 'plain text', 0))

    def test_terminal_uses_explicit_palette_to_avoid_late_osc11_input(self):
        source = next(after for file, before, after in patches.make_edits(self.sdk)
                      if str(file).endswith('native/orchestration.py'))
        self.assertIn('_hermes_terminal_env: dict[str, str] = {"HERMES_LIGHT": "0"}', source)

    def test_restart_preserves_verified_native_conversation_and_forward_cursor(self):
        from omnigent.harnesses.hermes_native import bridge, forwarder
        home = self.sdk / 'hermes_home'
        home.mkdir()
        db = sqlite3.connect(home / 'state.db')
        db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO sessions VALUES (?)', ('own-session',))
        db.commit()
        db.close()
        forwarder._write_state(self.sdk, forwarder._ForwardState(hermes_session_id='own-session', last_id=42))
        with patch.object(bridge, 'read_hermes_home', return_value=home):
            state = compat.retained_hermes_state(self.sdk)
            self.assertEqual(state.hermes_session_id, 'own-session')
            self.assertEqual(state.last_id, 42)
            (home / 'state.db').unlink()
            with self.assertRaisesRegex(RuntimeError, 'RESUME_STATE_INVALID'):
                compat.retained_hermes_state(self.sdk)
        self.assertFalse((home / 'state.db').exists())

    def test_resume_keeps_native_policy_instead_of_reapplying_creation_model(self):
        self.assertEqual(compat.resume_hermes_args(['--model', 'old', '--cli'], 'own'), ['--cli', '--resume', 'own'])
        self.assertEqual(compat.resume_hermes_args(['--model=old', '--resume', 'explicit'], 'own'), ['--resume', 'explicit'])

    def test_stop_does_not_mask_missing_tmux_or_transport_errors(self):
        with patch.object(compat.subprocess, 'run', side_effect=FileNotFoundError('tmux')):
            with self.assertRaisesRegex(RuntimeError, 'HERMES_TERMINAL_STOP_FAILED'):
                compat.stop_hermes_terminal({'socket_path': '/missing/socket', 'tmux_target': 'own'})
        with patch.object(compat.subprocess, 'run', return_value=types.SimpleNamespace(returncode=2, stderr='transport error')):
            with self.assertRaisesRegex(RuntimeError, 'HERMES_TERMINAL_STOP_FAILED'):
                compat.stop_hermes_terminal({'socket_path': '/missing/socket', 'tmux_target': 'own'})


class ModelInventoryCacheTests(unittest.TestCase):
    """One inventory run costs a Python start, Hermes' imports and an endpoint probe, and Omnigent waits only 5 s (before a session
    exists) or 15 s (the picker) for it. A complete answer is kept for a minute, callers that arrive during a run share it, and an
    incomplete answer or a failure is never kept."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        (self.profile / 'config.yaml').write_text('model: {default: a}\n')
        self.clock = types.SimpleNamespace(now=1000.0)
        self.runs = []  # one entry per Python process the inventory started
        self.answers = [self.process(['real/default'])]
        for manager in (patch.dict('os.environ', {'HERMES_OMNIGENT_PYTHON': '/sdk/python', 'HERMES_OMNIGENT_PROFILE': str(self.profile)}),
                        patch.object(compat, '_models_clock', lambda: self.clock.now),
                        patch.object(compat.subprocess, 'run', side_effect=self.fake_run)):
            manager.start()
            self.addCleanup(manager.stop)
        compat.reset_hermes_models()
        self.addCleanup(compat.reset_hermes_models)

    @staticmethod
    def process(ids, *, status='complete', returncode=0):
        """What models.py prints: the models, then (a newer script) one record saying whether the list is complete."""
        rows = [{'id': name, 'displayName': name, 'model': name, 'isDefault': index == 0} for index, name in enumerate(ids)]
        if status is not None:
            rows.append({'inventory': status})
        return types.SimpleNamespace(returncode=returncode, stdout=json.dumps(rows))

    def fake_run(self, *args, **kwargs):
        self.runs.append(args)
        answer = self.answers[min(len(self.runs), len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def ids(self):
        return [row['id'] for row in compat.hermes_models()]

    def test_a_complete_answer_is_served_from_memory_until_it_expires(self):
        self.assertEqual(self.ids(), ['real/default'])
        self.clock.now += 59
        self.assertEqual(self.ids(), ['real/default'])
        self.assertEqual(len(self.runs), 1)
        self.clock.now += 2
        self.ids()
        self.assertEqual(len(self.runs), 2)

    def test_a_change_to_hermes_configuration_is_seen_at_once(self):
        self.ids()
        (self.profile / 'config.yaml').write_text('model: {default: another-model-with-a-longer-name}\n')
        self.ids()
        self.assertEqual(len(self.runs), 2)
        (self.profile / '.env').write_text('KEY=value\n')  # a file appearing counts as a change too
        self.ids()
        self.assertEqual(len(self.runs), 3)
        self.ids()
        self.assertEqual(len(self.runs), 3)

    def test_an_incomplete_answer_is_returned_but_never_kept(self):
        self.answers = [self.process(['only/configured'], status='degraded'), self.process(['a', 'b'])]
        with self.assertLogs('hermes_omnigent.compat', 'WARNING') as logged:
            self.assertEqual(self.ids(), ['only/configured'])
        self.assertNotIn('only/configured', ' '.join(logged.output))
        self.assertEqual(self.ids(), ['a', 'b'])  # asked again at once
        self.assertEqual(self.ids(), ['a', 'b'])  # complete now, so kept
        self.assertEqual(len(self.runs), 2)

    def test_the_status_record_is_never_handed_to_callers_as_a_model(self):
        rows = compat.hermes_models()
        self.assertEqual([row['id'] for row in rows], ['real/default'])
        self.assertTrue(all('inventory' not in row for row in rows))

    def test_only_a_record_without_an_id_counts_as_the_status(self):
        # a model row that happens to carry the key, or a status that is not exactly 'complete', proves nothing
        for stdout in (json.dumps([{'id': 'm', 'inventory': 'complete'}]),
                       json.dumps([{'id': 'm'}, {'inventory': 'Complete'}]),
                       json.dumps([{'id': 'm'}, {'inventory': True}]),
                       json.dumps([{'id': 'm'}, 'complete'])):
            with self.subTest(stdout=stdout):
                compat.reset_hermes_models()
                self.runs.clear()
                self.answers = [types.SimpleNamespace(returncode=0, stdout=stdout)]
                self.ids()
                self.ids()
                self.assertEqual(len(self.runs), 2)

    def test_an_answer_from_a_script_without_a_status_record_is_never_kept(self):
        """models.py of an older plugin release (a rollback while this compat is still loaded) reports nothing."""
        self.answers = [self.process(['m'], status=None)]
        self.assertEqual(self.ids(), ['m'])
        self.assertEqual(self.ids(), ['m'])
        self.assertEqual(len(self.runs), 2)

    def test_a_failure_is_not_kept_and_the_next_call_asks_again(self):
        self.answers = [self.process([], returncode=1), compat.subprocess.TimeoutExpired('python', 45), self.process(['m'])]
        with self.assertRaisesRegex(RuntimeError, 'HERMES_MODELS_UNAVAILABLE'):
            compat.hermes_models()
        with self.assertRaises(compat.subprocess.TimeoutExpired):
            compat.hermes_models()
        self.assertEqual(self.ids(), ['m'])
        self.assertEqual(len(self.runs), 3)

    def hold_the_run(self, outcome):
        """Four callers; the first one's run does not finish until the other three are certainly waiting for it."""
        import concurrent.futures
        entered, arrived = threading.Event(), threading.Semaphore(0)
        waiting_result = concurrent.futures.Future.result

        def counted_result(future, timeout=None):
            arrived.release()
            return waiting_result(future, timeout)

        def slow(*args, **kwargs):
            self.runs.append(args)
            entered.set()
            for _ in range(3):
                self.assertTrue(arrived.acquire(timeout=10))
            return outcome
        results = []

        def call():
            try:
                results.append(self.ids())
            except BaseException as error:  # noqa: BLE001 - the test inspects what each caller saw
                results.append(error)
        with patch.object(compat.subprocess, 'run', side_effect=slow), \
                patch.object(concurrent.futures.Future, 'result', counted_result):
            threads = [threading.Thread(target=call) for _ in range(4)]
            threads[0].start()
            self.assertTrue(entered.wait(10))
            for thread in threads[1:]:
                thread.start()
            for thread in threads:
                thread.join(15)
        return results

    def test_callers_that_arrive_during_a_run_share_it(self):
        results = self.hold_the_run(self.process(['shared']))
        self.assertEqual(results, [['shared']] * 4)
        self.assertEqual(len(self.runs), 1)

    def test_a_failure_reaches_every_caller_that_shared_the_run(self):
        results = self.hold_the_run(self.process([], returncode=1))
        self.assertEqual(len(results), 4)
        self.assertTrue(all(isinstance(r, RuntimeError) and 'HERMES_MODELS_UNAVAILABLE' in str(r) for r in results), results)
        self.assertEqual(len(self.runs), 1)

    def test_a_returned_list_can_be_changed_without_changing_what_is_kept(self):
        first = compat.hermes_models()
        first[0]['id'] = 'mutated'
        first.clear()
        self.assertEqual(self.ids(), ['real/default'])

    def test_without_a_profile_there_is_nothing_to_key_an_answer_on_so_nothing_is_kept(self):
        with patch.dict('os.environ'):
            os.environ.pop('HERMES_OMNIGENT_PROFILE')
            self.ids()
            self.ids()
        self.assertEqual(len(self.runs), 2)

    @unittest.skipUnless(hasattr(os, 'fork'), 'needs fork')
    def test_a_real_fork_does_not_inherit_a_lock_that_another_thread_held(self):
        import warnings
        holding, release = threading.Event(), threading.Event()

        def hold():
            with compat._models_lock:
                holding.set()
                release.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(holding.wait(10))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)  # forking next to a running thread is the point of this test
            pid = os.fork()
        if pid == 0:  # the child: the lock must not still belong to a thread that does not exist here
            os._exit(0 if compat._models_lock.acquire(timeout=2) else 1)
        release.set()
        thread.join(10)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_a_run_that_a_configuration_change_replaced_cannot_overwrite_the_newer_answer(self):
        gates, started = [threading.Event(), threading.Event()], [threading.Event(), threading.Event()]

        def gated(*args, **kwargs):
            index = len(self.runs)
            self.runs.append(args)
            started[index].set()
            self.assertTrue(gates[index].wait(10))
            return self.process([f'run-{index}'])
        results = {}

        def call(name):
            results[name] = self.ids()
        with patch.object(compat.subprocess, 'run', side_effect=gated):
            first = threading.Thread(target=call, args=('old-config',))
            first.start()
            self.assertTrue(started[0].wait(10))
            (self.profile / 'config.yaml').write_text('model: {default: a-different-configuration}\n')  # the first run is now stale
            second = threading.Thread(target=call, args=('new-config',))
            second.start()
            self.assertTrue(started[1].wait(10))
            gates[1].set()  # the newer run finishes first and keeps its answer
            second.join(10)
            gates[0].set()  # the older one finishes later and must not replace it
            first.join(10)
            self.assertEqual(self.ids(), ['run-1'])  # still served from memory
        self.assertEqual(results, {'old-config': ['run-0'], 'new-config': ['run-1']})
        self.assertEqual(len(self.runs), 2)

    def test_a_vanished_leader_does_not_block_the_callers_after_it(self):
        stuck, entered = threading.Event(), threading.Event()

        def fake(*args, **kwargs):
            index = len(self.runs)
            self.runs.append(args)
            if index == 0:  # the first run does not finish within the callers' patience
                entered.set()
                self.assertTrue(stuck.wait(10))
                return self.process(['late'])
            return self.process(['fresh'])
        leader = threading.Thread(target=lambda: self.ids())
        with patch.object(compat, 'MODELS_WAIT_S', 0.3), patch.object(compat.subprocess, 'run', side_effect=fake):
            leader.start()
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(RuntimeError, 'HERMES_MODELS_UNAVAILABLE'):
                compat.hermes_models()  # a follower gives up after MODELS_WAIT_S ...
            self.assertEqual(self.ids(), ['fresh'])  # ... and the next caller runs its own inventory instead of waiting for the dead one
            stuck.set()
            leader.join(10)
            self.assertEqual(self.ids(), ['fresh'])  # the late leader did not replace what the newer run kept
        self.assertEqual(len(self.runs), 2)

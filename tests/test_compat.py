import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
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
        for relative in ['host/connect.py', 'harnesses/hermes_native/bridge.py', 'harnesses/hermes_native/forwarder.py', 'runner/app.py', 'runner/native/orchestration.py', 'onboarding/harness_readiness.py']:
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


if __name__ == '__main__':
    unittest.main()

class NativeModelSelectionTests(unittest.TestCase):
    def test_committed_policy_wins_over_stale_session_model(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            db = sqlite3.connect(home / 'state.db')
            db.execute('CREATE TABLE sessions (id TEXT, model TEXT, chat_id TEXT)')
            db.execute('CREATE TABLE state_meta (key TEXT, value TEXT)')
            db.execute('INSERT INTO sessions VALUES (?,?,?)', ('tip', 'old', 'logical'))
            db.execute('INSERT INTO state_meta VALUES (?,?)', ('gateway.local_policy.v1:logical',
                json.dumps({'policy': {'model': 'new', 'private': 'never-export'}})))
            db.commit();db.close()
            self.assertEqual(compat.selected_hermes_model(home, 'tip'), 'new')
            with self.assertRaisesRegex(RuntimeError, 'STATE_UNAVAILABLE'):
                compat.selected_hermes_model(home, 'missing')

    def test_slash_command_is_sent_once_and_confirmed_by_policy_not_chat_rows(self):
        from omnigent.harnesses.hermes_native import bridge, forwarder
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with patch.object(forwarder, '_read_state', return_value=types.SimpleNamespace(hermes_session_id='sid')), \
                    patch.object(bridge, 'read_hermes_home', return_value=home), \
                    patch.object(compat, 'selected_hermes_model', side_effect=['old', 'old', 'new']), \
                    patch.object(bridge, '_wait_for_tmux_info', return_value={'socket_path': '/own', 'tmux_target': 'own'}), \
                    patch.object(bridge, '_session_alive', return_value=True), \
                    patch.object(bridge, '_deliver_once') as deliver, patch.object(compat.time, 'sleep'):
                compat.inject_hermes_model(home, 'new')
                self.assertEqual(deliver.call_count, 1)
                self.assertEqual(deliver.call_args.args[2], '/model new')

    def test_same_committed_model_is_idempotent_without_touching_terminal(self):
        from omnigent.harnesses.hermes_native import bridge, forwarder
        with patch.object(forwarder, '_read_state', return_value=types.SimpleNamespace(hermes_session_id='sid')), \
                patch.object(bridge, 'read_hermes_home', return_value=Path('/owned')), \
                patch.object(compat, 'selected_hermes_model', return_value='current'), \
                patch.object(bridge, '_wait_for_tmux_info') as wait:
            compat.inject_hermes_model(Path('/owned'), 'current')
            wait.assert_not_called()

class NativeModelRPCTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_mutation_uses_revision_and_generation_fences(self):
        from hermes_omnigent.native_model import apply_model
        from unittest.mock import AsyncMock
        client = types.SimpleNamespace(rpc=AsyncMock(side_effect=[{'revision': 4, 'execution_generation': 8}, {'status': 'applied'}]))
        await apply_model(client, 'owned-session', 'new-model')
        args = client.rpc.await_args_list[1]
        self.assertEqual(args.args, ('session.mutate',))
        self.assertEqual(args.kwargs['expected_revision'], 4)
        self.assertEqual(args.kwargs['expected_generation'], 8)
        self.assertEqual(args.kwargs['payload'], {'model': 'new-model'})

    async def test_confirmation_required_is_never_automatically_approved(self):
        from hermes_omnigent.native_model import apply_model
        from unittest.mock import AsyncMock
        client = types.SimpleNamespace(rpc=AsyncMock(side_effect=[{'revision': 4, 'execution_generation': 8}, {'status': 'confirmation_required', 'confirm': 'not-approved'}]))
        with self.assertRaisesRegex(RuntimeError, 'CONFIRMATION_REQUIRED'):
            await apply_model(client, 'owned-session', 'new-model')
        self.assertEqual(client.rpc.await_count, 2)


class ModelResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_selects_native_default_instead_of_acknowledging_without_change(self):
        from omnigent.harnesses.hermes_native import bridge
        with patch.object(compat, 'hermes_models', return_value=[{'id': 'default-model', 'isDefault': True}]), \
                patch.object(bridge, 'bridge_dir_for_session_id', return_value=Path('/owned')), \
                patch.object(compat, 'inject_hermes_model') as change:
            result = await compat.change_model('owned-session', None)
            self.assertEqual(result.status_code, 204)
            change.assert_called_once_with(Path('/owned'), 'default-model')

    async def test_reset_without_known_default_fails_without_mutation(self):
        with patch.object(compat, 'hermes_models', return_value=[{'id': 'other'}]), \
                patch.object(compat, 'inject_hermes_model') as change:
            result = await compat.change_model('owned-session', None)
            self.assertEqual(result.status_code, 400)
            change.assert_not_called()

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hermes_omnigent import session_store, native_gateway


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.legacy = self.root / f'omnigent-{os.getuid()}' / 'hermes-native'
        self.source = self.legacy / ('b' * 32)
        self.target = self.profile / 'omnigent-plugin/sessions' / self.source.name
        for path in (self.legacy.parent, self.legacy, self.source, self.source / 'hermes_home'):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = patch.dict(os.environ, {'HERMES_OMNIGENT_PROFILE': str(self.profile)})
        env.start()
        self.addCleanup(env.stop)
        tmp = patch.object(session_store.tempfile, 'gettempdir', return_value=str(self.root))
        tmp.start()
        self.addCleanup(tmp.stop)

    def test_profile_bridge_is_on_persistent_volume_without_changing_global_tmpdir(self):
        self.assertEqual(session_store.bridge_root(self.legacy), self.target.parent)
        with patch.dict(os.environ, {'HERMES_OMNIGENT_PROFILE': ''}):
            self.assertEqual(session_store.bridge_root(self.legacy), self.legacy)

    def test_migration_copies_committed_wal_and_cursor_but_not_runtime_auth_relays(self):
        db = sqlite3.connect(self.source / 'hermes_home/state.db')
        self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO sessions VALUES (?)', ('retained',))
        db.commit()
        (self.source / 'hermes_forwarder.json').write_text(json.dumps({'hermes_session_id': 'retained', 'last_id': 7}))
        (self.source / 'tool_relay.json').write_text('{"token":"private-relay"}')
        (self.source / 'hermes_home/gateway.lock').write_text('old process')
        session_store.migrate_legacy(self.target)
        copied = sqlite3.connect(self.target / 'hermes_home/state.db')
        try:
            self.assertEqual(copied.execute('SELECT id FROM sessions').fetchone()[0], 'retained')
        finally:
            copied.close()
        self.assertEqual(json.loads((self.target / 'hermes_forwarder.json').read_text())['last_id'], 7)
        self.assertFalse((self.target / 'tool_relay.json').exists())
        self.assertFalse((self.target / 'hermes_home/gateway.lock').exists())
        self.assertTrue((self.source / 'hermes_home/state.db').exists())
        self.assertEqual(native_gateway.validate_home(self.target / 'hermes_home'), self.target / 'hermes_home')
        self.assertEqual((self.target / 'hermes_home/state.db').stat().st_mode & 0o777, 0o600)
        (self.target / 'sentinel').write_text('keep')
        session_store.migrate_legacy(self.target)
        self.assertEqual((self.target / 'sentinel').read_text(), 'keep')

    def test_migration_refuses_dangling_destination_symlink(self):
        self.target.parent.mkdir(parents=True, mode=0o700)
        self.target.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_INVALID'):
            session_store.migrate_legacy(self.target)
        self.assertTrue(self.target.is_symlink())

    def test_volume_group_permissions_are_tightened_on_owned_plugin_directories(self):
        self.target.parent.parent.mkdir(mode=0o770)
        self.target.parent.parent.chmod(0o770)
        session_store.migrate_legacy(self.target)
        for path in (self.target.parent.parent, self.target.parent, self.target):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for path in (self.target.parent.parent, self.target.parent, self.target, self.target / 'hermes_home'):
            path.chmod(0o770)
        session_store.migrate_legacy(self.target)
        for path in (self.target.parent.parent, self.target.parent, self.target, self.target / 'hermes_home'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_symlink_plugin_root_is_never_chmodded_or_followed(self):
        foreign = self.root / 'unrelated'
        foreign.mkdir(mode=0o750)
        mode = foreign.stat().st_mode
        self.target.parent.parent.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_INVALID'):
            session_store.migrate_legacy(self.target)
        self.assertEqual(foreign.stat().st_mode, mode)
        self.assertFalse((foreign / 'sessions').exists())

    def test_directory_owned_by_another_user_is_never_repaired(self):
        from types import SimpleNamespace
        path = self.profile / 'omnigent-plugin'
        path.mkdir(mode=0o770)
        with patch.object(session_store.os, 'fstat', return_value=SimpleNamespace(
                st_uid=os.getuid() + 1, st_mode=0o40770)), patch.object(session_store.os, 'fchmod') as chmod:
            with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_INVALID'):
                session_store.prepare_private_directory(path)
            chmod.assert_not_called()

    def test_migration_refuses_active_gateway(self):
        (self.source / 'hermes_home/.omnigent-gateway.json').write_text('{"pid":42}')
        with patch.object(native_gateway, 'owned_process', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'MIGRATION_BUSY'):
                session_store.migrate_legacy(self.target)
        self.assertFalse(self.target.exists())

    def test_migration_refuses_symlink_database_and_wrong_destination(self):
        (self.source / 'hermes_home/state.db').symlink_to(self.root / 'elsewhere.db')
        (self.root / 'elsewhere.db').write_text('unrelated')
        with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_INVALID'):
            session_store.migrate_legacy(self.target)
        with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_INVALID'):
            session_store.migrate_legacy(self.profile / 'other')
        self.assertFalse(self.target.exists())

class PortableHistoryTests(unittest.TestCase):
    def test_native_import_remaps_profile_bound_ids_and_lineage_without_mutation(self):
        from hermes_omnigent.session_import import portable_sessions
        rows = [{'id': 'local-root', 'source': 'local', 'user_id': 'old-profile-owner', 'messages': []},
                {'id': 'local-tip', 'source': 'local', 'parent_session_id': 'local-root',
                 'messages': [{'role': 'assistant', 'content': 'retained', 'active': 0}]}]
        result, mapping = portable_sessions(rows)
        self.assertEqual(result[1]['parent_session_id'], mapping['local-root'])
        self.assertEqual(result[0]['source'], 'cli')
        self.assertIsNone(result[0]['user_id'])
        self.assertFalse(result[0]['id'].startswith('local-'))
        self.assertEqual(result[1]['messages'], rows[1]['messages'])
        self.assertEqual(rows[0]['user_id'], 'old-profile-owner')
        self.assertEqual(portable_sessions(rows), (result, mapping))

    def test_remote_channel_rows_cannot_be_reinterpreted_as_owned_cli(self):
        from hermes_omnigent.session_import import portable_sessions
        with self.assertRaisesRegex(RuntimeError, 'SOURCE_UNSUPPORTED'):
            portable_sessions([{'id': 'remote-id', 'source': 'telegram'}])

    def test_native_import_uses_isolated_interpreter_and_suppresses_helper_output(self):
        import subprocess
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            (staging / 'hermes_home').mkdir()
            db = sqlite3.connect(staging / 'hermes_home/state.db')
            db.execute('CREATE TABLE state_meta (key TEXT, value TEXT)')
            db.execute('INSERT INTO state_meta VALUES (?,?)', ('gateway.local_policy.v1:x', '{}'))
            db.commit(); db.close()
            env = {'HERMES_OMNIGENT_PYTHON': '/native/python',
                   'HERMES_OMNIGENT_PYTHONPATH': '/native/modules', 'PYTHONHOME': '/unrelated'}
            with patch.dict(os.environ, env), patch.object(session_store.subprocess, 'run') as run:
                run.return_value = SimpleNamespace(returncode=0, stdout='HERMES_HISTORY_IMPORTED\n')
                session_store.import_native_history(staging)
                self.assertEqual(run.call_args.args[0][0], '/native/python')
                self.assertEqual(run.call_args.kwargs['env']['PYTHONPATH'], '/native/modules')
                self.assertNotIn('PYTHONHOME', run.call_args.kwargs['env'])
                run.return_value = SimpleNamespace(returncode=1, stdout='private-content')
                with self.assertRaisesRegex(RuntimeError, '^HERMES_SESSION_IMPORT_FAILED$'):
                    session_store.import_native_history(staging)
                run.side_effect = subprocess.TimeoutExpired('native-helper', 90)
                with self.assertRaisesRegex(RuntimeError, '^HERMES_SESSION_IMPORT_FAILED$'):
                    session_store.import_native_history(staging)

"""Transfer stopped local history through native Hermes portability APIs.

Run on a private staging copy, using the native Hermes interpreter. Runtime
receipts bound to the old profile identity are not transplanted.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid


def portable_sessions(rows):
    mapping = {row['id']: uuid.uuid5(uuid.NAMESPACE_URL,
        'hermes-omnigent:history:' + row['id']).hex for row in rows}
    result = []
    for row in rows:
        if row.get('source') not in ('local', 'cli', 'tui', 'oneshot', 'tool'):
            raise RuntimeError('HERMES_SESSION_SOURCE_UNSUPPORTED')
        row = dict(row, id=mapping[row['id']], source='cli', user_id=None)
        if row.get('parent_session_id'):
            row['parent_session_id'] = mapping.get(row['parent_session_id'])
        result.append(row)
    return result, mapping


def transfer(staging):
    from hermes_state import SessionDB
    home = staging / 'hermes_home'
    state_path = staging / 'hermes_forwarder.json'
    state = json.loads(state_path.read_text())
    snapshot = home / 'state.db'
    donor = SessionDB(snapshot, read_only=True)
    try:
        rows, mapping = portable_sessions(donor.export_all(include_inactive=True))
    finally:
        donor.close()
    previous_id = state['hermes_session_id']
    if previous_id not in mapping:
        raise RuntimeError('HERMES_SESSION_IMPORT_INCOMPLETE')
    with closing(sqlite3.connect(f'file:{snapshot}?mode=ro', uri=True)) as connection:
        high = connection.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
    if state.get('last_id', 0) < high or bool(state.get('partial_row_id')):
        raise RuntimeError('HERMES_SESSION_IMPORT_PENDING')
    snapshot.rename(home / 'original-state.db')
    imported = SessionDB(snapshot)
    try:
        result = imported.import_sessions(rows)
        if not result.get('ok') or result.get('imported') != len(rows):
            raise RuntimeError('HERMES_SESSION_IMPORT_INCOMPLETE')
    finally:
        imported.close()
    with closing(sqlite3.connect(f'file:{snapshot}?mode=ro', uri=True)) as connection:
        total, high = connection.execute('SELECT COUNT(*),COALESCE(MAX(id),0) FROM messages').fetchone()
    if total != sum(len(row.get('messages', [])) for row in rows):
        raise RuntimeError('HERMES_SESSION_IMPORT_INCOMPLETE')
    state.update(hermes_session_id=mapping[previous_id], last_id=high,
                 partial_row_id=0, partial_row_items=0, active_turn_id=None)
    state_path.write_text(json.dumps(state))
    state_path.chmod(0o600)
    snapshot.chmod(0o600)
    (home / 'original-state.db').unlink()


if __name__ == '__main__':
    try:
        path = Path(sys.argv[1])
        if not path.name.startswith('.migrate-') or path.is_symlink() or path.stat().st_uid != os.getuid():
            raise RuntimeError('HERMES_SESSION_DIRECTORY_INVALID')
        transfer(path)
        print('HERMES_HISTORY_IMPORTED')
    except Exception:
        print('HERMES_HISTORY_IMPORT_FAILED')
        raise SystemExit(1)

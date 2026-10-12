"""Hermes Agent plugin entrypoint. Credentials are accepted only by the UI."""
import json
import os
from pathlib import Path
import sys

_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
from hermes_omnigent.runtime import get_runtime, Failure


def register(ctx):
    if os.environ.get('OMNIGENT_HERMES_NATIVE_BRIDGE_DIR'):
        return
    runtime = get_runtime()
    runtime.activate()
    def run(action, parameters):
        try:
            if action == 'status':
                result = runtime.status()
            elif action == 'catalog':
                result = runtime.catalog()
            elif action == 'verify':
                result = runtime.start('verify', **parameters)
            else:
                state = runtime.state()
                result = {'url': state.get('server'), 'pairingUrl': '/omnigent'}
            return json.dumps(result, ensure_ascii=False)
        except Failure as error:
            return json.dumps({'ok': False, 'error': error.code})
    for action in ('status', 'catalog', 'verify', 'open'):
        properties = {'engine': {'type': 'string', 'enum': ['hermes', 'claude', 'codex', 'pi']},
                      'model': {'type': 'string'}} if action == 'verify' else {}
        ctx.register_tool(name='omnigent_' + action, toolset='omnigent',
            schema={'name': 'omnigent_' + action, 'description': {
                'status': 'Read Omnigent pairing and live host status. Pairing credentials must be entered in the Omnigent UI.',
                'catalog': 'Read actual Omnigent models and native agent modules for this host.',
                'verify': 'Create bounded test chats for installed native engines, require a real reply, and stop test sessions.',
                'open': 'Get the configured Omnigent URL and Hermes pairing page.'}[action],
                'parameters': {'type': 'object', 'properties': properties, 'additionalProperties': False}},
            handler=lambda parameters, _action=action, **kwargs: run(_action, parameters))
    def command(args='', **kwargs):
        action = str(args).strip() or 'status'
        if action in ('setup', 'reconnect'):
            return '請開啟 Hermes 的 Omnigent 配對面板（/omnigent），重新輸入網址、帳號與遮罩密碼。'
        if action == 'disconnect':
            try:
                return json.dumps(runtime.start('disconnect'), ensure_ascii=False)
            except Failure as error:
                return json.dumps({'ok': False, 'error': error.code})
        return run(action if action in ('status', 'catalog', 'verify', 'open') else 'status', {})
    ctx.register_command('omnigent', command, 'Omnigent pairing, catalog and native chat verification')
    ctx.register_hook('on_session_start', lambda **kwargs: runtime.activate())

"""CLI for unattended acceptance; passwords are never command arguments."""
import argparse
import json
from .runtime import get_runtime, Failure, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['status', 'catalog', 'acceptance', 'verify', 'disconnect', 'prepare'])
    parser.add_argument('--chat', action='store_true')
    parser.add_argument('--engine', choices=['hermes', 'claude', 'codex', 'pi'])
    parser.add_argument('--model')
    args = parser.parse_args()
    runtime = get_runtime()
    try:
        if args.action == 'status':
            result = runtime.status()
        elif args.action == 'catalog':
            result = runtime.catalog()
        else:
            with runtime.lock():
                if args.action == 'prepare':
                    runtime.require_active()
                    result = {'ok': bool(runtime.install())}
                elif args.action == 'disconnect':
                    result = runtime.disconnect()
                elif args.action == 'verify' or args.chat:
                    result = runtime.verify(engine=args.engine, model=args.model)
                else:
                    result = {}
                if args.action == 'acceptance':
                    state = runtime.status()
                    inventory = runtime.catalog()
                    ready_catalog = inventory.get('ok') and all(inventory.get('data', {}).get('engines', {}).get(name, {}).get('ready')
                        for name in state['installedEngines'])
                    result = {'ok': state['phase'] == 'ready' and bool(ready_catalog), 'phase': state['phase'],
                              'chatRequested': args.chat, 'installedEngines': state['installedEngines'],
                              'verification': state['verification']}
                    write(runtime.root / 'acceptance.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('ok') is False else 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': getattr(error, 'code', 'OPERATION_FAILED')}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

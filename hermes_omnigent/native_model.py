"""Change a private Hermes 0.22 session through its authenticated native owner."""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid


async def apply_model(client, session_id, model):
    snapshot = await client.rpc('session.resume', session_id=session_id)
    result = await client.rpc('session.mutate', session_id=session_id,
        request_id=uuid.uuid4().hex, operation='model', payload={'model': model},
        expected_revision=snapshot['revision'], expected_generation=snapshot['execution_generation'])
    if result.get('status') == 'confirmation_required':
        # Do not automatically approve a provider/account change.
        raise RuntimeError('HERMES_MODEL_CONFIRMATION_REQUIRED')


async def change(home, session_id, model):
    from native_gateway import validate_home, owned_process, read_receipt
    home = validate_home(home)
    if os.environ.get('HERMES_OMNIGENT_DIRECT_HERMES') != '1' or not owned_process(home, read_receipt(home)):
        raise RuntimeError('HERMES_GATEWAY_OWNER_UNKNOWN')
    os.environ['HERMES_HOME'] = str(home)
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import connect_gateway
    state = discover_gateway_endpoint(home, timeout=2)
    if state.state != 'ready':
        raise RuntimeError('HERMES_GATEWAY_NOT_READY')
    async with connect_gateway(endpoint=state.endpoint) as client:
        await apply_model(client, session_id, model)


if __name__ == '__main__':
    try:
        # Old Hermes releases have no native Gateway client; retain their TUI path.
        import importlib.util
        if importlib.util.find_spec('hermes_cli.gateway_client') is None:
            raise SystemExit(3)
        asyncio.run(asyncio.wait_for(change(Path(sys.argv[1]), sys.argv[2], sys.argv[3]), timeout=10))
        print('HERMES_MODEL_SELECTED')
    except Exception:
        print('HERMES_MODEL_CHANGE_FAILED')
        raise SystemExit(1)

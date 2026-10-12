"""Hermes-owned model inventory; prints whitelisted identifiers only."""
import json
import os

os.environ['HERMES_HOME'] = os.environ['HERMES_OMNIGENT_PROFILE']
from hermes_cli.inventory import load_picker_context, build_models_payload

context = load_picker_context()
inventory = build_models_payload(context, explicit_only=True, include_unconfigured=False,
                                 probe_custom_providers=False, probe_current_custom_provider=True,
                                 picker_hints=True, pricing=False, capabilities=False)
rows = []
for provider in inventory.get('providers', []):
    # Native --model preserves the current provider. Do not advertise an ID
    # from a different account/endpoint that this launch would silently reroute.
    if not provider.get('is_current'):
        continue
    for model in provider.get('models', []):
        identifier = model if isinstance(model, str) else model.get('id') if isinstance(model, dict) else None
        if identifier and identifier not in {row['id'] for row in rows}:
            rows.append({'id': identifier, 'displayName': identifier, 'model': identifier,
                         'isDefault': identifier == inventory.get('model')})
print(json.dumps(rows))

"""Hermes-owned model inventory; prints whitelisted identifiers only."""
import json
import os

os.environ['HERMES_HOME'] = os.environ['HERMES_OMNIGENT_PROFILE']
from hermes_cli.config import load_env
os.environ.update(load_env())
from hermes_cli.inventory import load_picker_context, build_models_payload

context = load_picker_context()
bare_custom = context.current_provider == 'custom' and bool(context.current_base_url)
inventory = build_models_payload(context, explicit_only=True, include_unconfigured=False,
                                 probe_custom_providers=False, probe_current_custom_provider=not bare_custom,
                                 picker_hints=True, pricing=False, capabilities=False, for_picker=True)
# Hermes0.19's bare custom inventory probes /models with an empty key even
# when model.api_key is configured. Resolve the same credentials as chat and
# probe only the active endpoint; keep the configured model when unavailable.
custom_models = None
fallback_endpoint = bool(context.current_base_url) and not any(p.get('is_current') for p in inventory.get('providers', []))
if bare_custom or fallback_endpoint:
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_cli.models import fetch_api_models
        runtime = resolve_runtime_provider(requested=context.current_provider)
        custom_models = fetch_api_models(runtime.get('api_key'), runtime.get('base_url'),
                                        timeout=5.0, api_mode=runtime.get('api_mode'))
    except Exception:
        pass
rows = []
for provider in inventory.get('providers', []):
    # Native --model preserves the current provider. Do not advertise an ID
    # from a different account/endpoint that this launch would silently reroute.
    if not provider.get('is_current'):
        continue
    available = custom_models if custom_models and provider.get('slug') == 'custom' else provider.get('models', [])
    for model in available:
        identifier = model if isinstance(model, str) else model.get('id') if isinstance(model, dict) else None
        if identifier and identifier not in {row['id'] for row in rows}:
            rows.append({'id': identifier, 'displayName': identifier, 'model': identifier,
                         'isDefault': identifier == inventory.get('model')})
# Auto + an explicit endpoint can have no picker provider row. The configured
# model remains selectable but still needs a successful real chat verification.
if fallback_endpoint and not rows:
    for model in custom_models or [inventory.get('model')]:
        identifier = model if isinstance(model, str) else model.get('id') if isinstance(model, dict) else None
        if identifier and identifier not in {row['id'] for row in rows}:
            rows.append({'id': identifier, 'displayName': identifier, 'model': identifier,
                         'isDefault': identifier == inventory.get('model')})
print(json.dumps(rows))

"""Resolve only this Hermes profile's owned local inference server.

The JSON output is a private subprocess pipe consumed by the bridge, never a
user-facing diagnostic. The shared server remains owned by the original profile.
"""
import contextlib
import io
import json
import os
from urllib.parse import urlsplit


def connection():
    os.environ['HERMES_HOME'] = os.environ['HERMES_OMNIGENT_PROFILE']
    from hermes_cli.local_runtime.endpoint import managed_root
    result = managed_root()
    if not result:
        raise RuntimeError('HERMES_LOCAL_MODEL_UNAVAILABLE')
    base, key = result
    url = urlsplit(base)
    if (url.scheme not in ('http', 'https') or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment
            or not isinstance(key, str) or len(key) > 8192):
        raise RuntimeError('HERMES_LOCAL_MODEL_UNAVAILABLE')
    return {'provider': 'custom', 'base_url': base.rstrip('/') + '/v1',
            'api_key': key or 'no-key-required', 'api_mode': 'chat_completions'}


if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            value = connection()
    except Exception:
        raise SystemExit(1) from None
    print(json.dumps(value))

"""Dashboard API; Hermes owns authentication and disable middleware."""
from pathlib import Path
import sys
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

root = str(Path(__file__).resolve().parents[1])
if root not in sys.path:
    sys.path.insert(0, root)
from hermes_omnigent.runtime import get_runtime, Failure

router = APIRouter()
runtime = get_runtime()
runtime.activate(prepare=True)


def authorize(request):
    from hermes_cli.web_server import _require_token
    _require_token(request)
    if not runtime.active():
        raise HTTPException(403, detail='PLUGIN_DISABLED')


def perform(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Failure as error:
        raise HTTPException(409 if error.code == 'OPERATION_BUSY' else 400, detail=error.code) from None


class PairRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engine: str | None = Field(default=None, pattern='^(hermes|claude|codex|pi)$')
    model: str | None = Field(default=None, max_length=256)


@router.get('/status')
def status(request: Request):
    authorize(request)
    return perform(runtime.status)


@router.post('/pairing/start')
def pairing(request: Request):
    authorize(request)
    perform(runtime.reset_pairing)
    return {'ok': True, 'pairingRequired': True}


@router.post('/pair')
async def pair(request: Request):
    # Manual validation keeps sensitive body values out of FastAPI's standard
    # validation-error `input` field. Public failures carry codes only.
    authorize(request)
    try:
        body = await request.body()
        if len(body) > 8192:
            raise ValueError()
        import json
        values = PairRequest.model_validate(json.loads(body))
    except Exception:
        raise HTTPException(400, detail='INVALID_PAIRING_REQUEST') from None
    return perform(runtime.start, 'pair', server=values.server, username=values.username,
                   password=values.password.get_secret_value())


@router.get('/catalog')
def catalog(request: Request):
    authorize(request)
    return perform(runtime.catalog)


@router.post('/verify')
def verify(request: Request, body: VerifyRequest):
    authorize(request)
    return perform(runtime.start, 'verify', engine=body.engine, model=body.model)


@router.post('/disconnect')
def disconnect(request: Request):
    authorize(request)
    return perform(runtime.start, 'disconnect')


@router.post('/prepare')
def prepare(request: Request):
    authorize(request)
    return perform(runtime.start, 'prepare')

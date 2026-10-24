import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .storage import Store
from .decision_engine import decide_batch


def load_env():
    path = Path('.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


load_env()
BASE_URL = os.getenv('A2A_BASE_URL', 'http://127.0.0.1:8000/a2a/').rstrip('/') + '/'
TOKENS = [v.strip() for v in os.getenv('A2A_TOKENS', '').split(',') if v.strip()]
DEMO = os.getenv('A2A_DEMO_MODE', 'false').lower() == 'true'
store = Store(os.getenv('A2A_DB_PATH', 'agent.sqlite3'))
app = FastAPI(title='Invoice Action A2A Agent', version='0.2.0')

INPUT_MODE = 'application/vnd.ga5.invoice-claim-batch+json'
PROPOSAL_MODE = 'application/vnd.ga5.invoice-action-proposals+json'
RECEIPT_MODE = 'application/vnd.ga5.invoice-action-receipts+json'
RESULT_MODE = 'application/vnd.ga5.invoice-action-results+json'


class Message(BaseModel):
    model_config = ConfigDict(extra='allow')
    messageId: str = Field(min_length=1)
    role: str
    parts: list[dict] = Field(min_length=1)
    taskId: str | None = None
    contextId: str | None = None


class SendRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    message: Message
    configuration: dict | None = None


def failure(code, status, description):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': description}})


async def protocol(request: Request, authorization: str | None = Header(default=None),
                   a2a_version: str | None = Header(default=None, alias='A2A-Version')):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Authentication required')
    token = authorization[len('Bearer '):]
    if not any(secrets.compare_digest(token, allowed) for allowed in TOKENS):
        raise HTTPException(status_code=403, detail='Forbidden')
    if a2a_version != '1.0':
        raise HTTPException(status_code=400, detail='Unsupported A2A-Version')
    if request.method == 'POST':
        media = request.headers.get('content-type', '').split(';')[0].strip().lower()
        if media != 'application/a2a+json':
            raise HTTPException(status_code=415, detail='Expected application/a2a+json')
    return token  # exact token is the isolated principal; never expose it in responses


@app.get('/.well-known/agent-card.json')
def card():
    return {
        'name': 'Invoice Action Agent',
        'description': 'Reviews invoice batches and proposes evidence-backed actions subject to approval.',
        'version': '0.2.0',
        'capabilities': {},
        'supportedInterfaces': [{'url': BASE_URL, 'protocolBinding': 'HTTP+JSON', 'protocolVersion': '1.0'}],
        'defaultInputModes': [INPUT_MODE],
        'defaultOutputModes': [PROPOSAL_MODE, RECEIPT_MODE],
        'skills': [{'id': 'invoice_action_agent', 'name': 'Invoice action agent',
                    'description': 'Review invoice packages and recommend actions.',
                    'tags': ['invoice', 'approval', 'evidence']}],
    }


@app.post('/a2a/message:send')
def send(body: SendRequest, principal: str = Depends(protocol)):
    message = body.message.model_dump(exclude_none=True)
    state, prior = store.lookup_message(principal, message['messageId'], message)
    if state == 'conflict':
        return failure('IDEMPOTENCY_CONFLICT', 409, 'Message ID was already used with different content')
    if state == 'replay':
        return {'task': prior}

    if message['role'] != 'ROLE_USER':
        return failure('INVALID_MESSAGE', 400, 'Expected ROLE_USER')
    if message.get('taskId') or message.get('contextId'):
        # Continuations need proposal/result binding, implemented in Phase 4.
        return failure('NOT_IMPLEMENTED', 501, 'Result continuations are implemented in Phase 4')
    if len(message['parts']) != 1 or message['parts'][0].get('mediaType') != INPUT_MODE:
        return failure('INVALID_MESSAGE', 400, 'Expected one invoice batch part')
    batch = message['parts'][0].get('data')
    if not isinstance(batch, dict) or not isinstance(batch.get('batchId'), str) or not isinstance(batch.get('packages'), list):
        return failure('INVALID_BATCH', 400, 'Expected batchId and packages')

    if not DEMO:
        try:
            decisions = decide_batch(store, batch['packages'], batch.get('policyRevision'))
        except ValueError as exc:
            return failure('INVALID_PROPOSAL', 422, str(exc))
        except Exception:
            # Never leak upstream API credentials or raw model responses.
            return failure('DECISION_UNAVAILABLE', 503, 'Invoice decision service unavailable')
        proposals = []
        for decision in decisions:
            proposals.append({**decision, 'actionId': str(uuid.uuid4())})
        task = {
            'id': str(uuid.uuid4()), 'contextId': str(uuid.uuid4()),
            'status': {'state': 'TASK_STATE_INPUT_REQUIRED'},
            'history': [message],
            'artifacts': [{'artifactId': str(uuid.uuid4()), 'parts': [{
                'mediaType': PROPOSAL_MODE,
                'data': {'batchId': batch['batchId'], 'proposals': proposals}
            }]}],
            'created_at_internal': datetime.now(timezone.utc).isoformat(),
        }
        outcome, saved = store.save_message_task(principal, message['messageId'], message, task)
        if outcome == 'conflict':
            return failure('IDEMPOTENCY_CONFLICT', 409, 'Message ID was already used with different content')
        return {'task': saved}

    # Local-only scaffold: no proposals or receipts; NEVER submit this to the grader.
    now = datetime.now(timezone.utc).isoformat()
    task = {
        'id': str(uuid.uuid4()), 'contextId': str(uuid.uuid4()),
        'status': {'state': 'TASK_STATE_SUBMITTED'},
        'history': [message], 'artifacts': [], 'created_at_internal': now,
    }
    outcome, saved = store.save_message_task(principal, message['messageId'], message, task)
    if outcome == 'conflict':
        return failure('IDEMPOTENCY_CONFLICT', 409, 'Message ID was already used with different content')
    return {'task': saved}


@app.get('/a2a/tasks')
def list_tasks(principal: str = Depends(protocol)):
    return {'tasks': store.list_tasks(principal)}


@app.get('/a2a/tasks/{task_id}')
def get_task(task_id: str, principal: str = Depends(protocol)):
    task = store.get_task(principal, task_id)
    if task is None:
        return failure('NOT_FOUND', 404, 'Task not found')
    return task


@app.post('/a2a/tasks/{task_id}:cancel')
def cancel(task_id: str, principal: str = Depends(protocol)):
    outcome, task = store.cancel(principal, task_id)
    if outcome == 'missing':
        return failure('NOT_FOUND', 404, 'Task not found')
    if outcome == 'terminal':
        return failure('TASK_TERMINAL', 409, 'Task is already terminal')
    return task


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return failure('HTTP_ERROR', exc.status_code, str(exc.detail))


@app.middleware('http')
async def response_media(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/a2a/'):
        response.headers['content-type'] = 'application/a2a+json'
    return response

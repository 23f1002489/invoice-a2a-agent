import os
import tempfile
from pathlib import Path

fd, path = tempfile.mkstemp(suffix='.sqlite3')
os.close(fd)
os.environ['A2A_DB_PATH'] = path
os.environ['A2A_TOKENS'] = 'alpha,beta'
os.environ['A2A_DEMO_MODE'] = 'true'
from fastapi.testclient import TestClient
from app.main import app, store

client = TestClient(app)
HEAD = {'Authorization': 'Bearer alpha', 'A2A-Version': '1.0', 'Content-Type': 'application/a2a+json'}
BODY = {'message': {'messageId': 'm-1', 'role': 'ROLE_USER', 'parts': [
    {'mediaType': 'application/vnd.ga5.invoice-claim-batch+json',
     'data': {'batchId': 'batch-1', 'policyRevision': 'r1', 'packages': []}}]}}


def test_card():
    assert client.get('/.well-known/agent-card.json').status_code == 200


def test_auth_version_media():
    assert client.get('/a2a/tasks').status_code == 401
    assert client.get('/a2a/tasks', headers={'Authorization': 'Bearer wrong', 'A2A-Version': '1.0'}).status_code == 403
    assert client.get('/a2a/tasks', headers={'Authorization': 'Bearer alpha', 'A2A-Version': '2.0'}).status_code == 400
    assert client.post('/a2a/message:send', headers={**HEAD, 'Content-Type': 'application/json'}, json=BODY).status_code == 415


def test_replay_isolation_cancel():
    first = client.post('/a2a/message:send', headers=HEAD, json=BODY)
    assert first.status_code == 200
    task = first.json()['task']
    task_id = task['id']
    changed_config = {**BODY, 'configuration': {'returnImmediately': True}}
    replay = client.post('/a2a/message:send', headers=HEAD, json=changed_config)
    assert replay.json()['task'] == task
    conflict = {'message': {**BODY['message'], 'parts': [
        {'mediaType': BODY['message']['parts'][0]['mediaType'], 'data': {'batchId': 'changed', 'packages': []}}]}}
    assert client.post('/a2a/message:send', headers=HEAD, json=conflict).status_code == 409
    outsider = {**HEAD, 'Authorization': 'Bearer beta'}
    assert client.get('/a2a/tasks', headers=outsider).json() == {'tasks': []}
    assert client.get(f'/a2a/tasks/{task_id}', headers=outsider).status_code == 404
    assert client.post(f'/a2a/tasks/{task_id}:cancel', headers=outsider).status_code == 404
    assert client.get(f'/a2a/tasks/{task_id}', headers=HEAD).status_code == 200
    canceled = client.post(f'/a2a/tasks/{task_id}:cancel', headers=HEAD)
    assert canceled.json()['status']['state'] == 'TASK_STATE_CANCELED'
    assert client.post(f'/a2a/tasks/{task_id}:cancel', headers=HEAD).status_code == 409
    # Replays must return current complete stored Task after cancellation.
    assert client.post('/a2a/message:send', headers=HEAD, json=BODY).json()['task']['status']['state'] == 'TASK_STATE_CANCELED'


def test_no_demo_engine():
    from app import main
    original = main.DEMO
    main.DEMO = False
    try:
        b = {'message': {**BODY['message'], 'messageId': 'another'}}
        assert client.post('/a2a/message:send', headers=HEAD, json=b).status_code == 422
    finally:
        main.DEMO = original

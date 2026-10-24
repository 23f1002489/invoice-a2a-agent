import pytest
from fastapi.testclient import TestClient
from app.main import app
import os

token = os.getenv("A2A_TEST_TOKEN")
if not token:
    raise SystemExit("Set A2A_TEST_TOKEN in PowerShell")

@pytest.fixture
def auth_headers():
    return {
        "Authorization": f"Bearer {token}", # Must match what is in A2A_TOKENS
        "A2A-Version": "1.0",
        "Content-Type": "application/a2a+json"
    }

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-token-principal-1",
        "A2A-Version": "1.0",
        "Content-Type": "application/a2a+json"
    }

def test_approval_and_receipt_lifecycle(client, auth_headers):
    # 1. Initial message to get proposals
    payload = {
        "message": {
            "messageId": "msg-p4-001",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": "application/vnd.ga5.invoice-claim-batch+json",
                "data": {
                    "batchId": "b-p4", 
                    "policyRevision": "rev-1",
                    "packages": [{
                        "packageId": "pkg-1", 
                        "vendorName": "Acme", 
                        "invoiceNumber": "INV-1", 
                        "amountMinor": 1000, 
                        "currency": "INR",
                        "documents": [{"docId": "doc-1", "content": "Invoice details [REF_01] approved for payment [REF_02]."}]
                    }]
                }
            }]
        }
    }
    res = client.post("/a2a/message:send", headers=auth_headers, json=payload)
    assert res.status_code == 200, res.text
    task_data = res.json()["task"]
    task_id = task_data["id"]
    context_id = task_data.get("contextId", "ctx-default")
    
    # Extract proposal details to craft a valid continuation
    proposal_part = next(p for p in task_data["artifacts"] if p["mediaType"] == "application/vnd.ga5.invoice-action-proposals+json")
    prop = proposal_part["data"]["proposals"][0]
    
    # 2. Send result continuation
    continuation_payload = {
        "message": {
            "messageId": "msg-p4-002",
            "taskId": task_id,
            "contextId": context_id,
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": "application/vnd.ga5.invoice-action-results+json",
                "data": {
                    "batchId": "b-p4",
                    "results": [{
                        "packageId": prop["packageId"],
                        "actionId": prop["actionId"],
                        "action": prop["action"],
                        "outcome": "ACCEPTED",
                        "receiptNonce": "nonce-abc-12345"
                    }]
                }
            }]
        }
    }
    
    res_cont = client.post("/a2a/message:send", headers=auth_headers, json=continuation_payload)
    assert res_cont.status_code == 200, res_cont.text
    completed_task = res_cont.json()["task"]
    
    # 3. Verify task is COMPLETED and receipts contain the execution
    assert completed_task["status"] == "TASK_STATE_COMPLETED"
    receipt_artifact = next(p for p in completed_task["artifacts"] if p["mediaType"] == "application/vnd.ga5.invoice-action-receipts+json")
    executions = receipt_artifact["data"]["executions"]
    assert len(executions) == 1
    assert executions[0]["receiptNonce"] == "nonce-abc-12345"
    assert executions[0]["packageId"] == prop["packageId"]
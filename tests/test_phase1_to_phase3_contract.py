"""Run: python -m pytest -q tests/test_phase1_to_phase3_contract.py"""
import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from app import main, decision_engine as engine
from app.storage import Store

HEAD = {"Authorization": "Bearer alpha", "A2A-Version": "1.0", "Content-Type": "application/a2a+json"}
INPUT = "application/vnd.ga5.invoice-claim-batch+json"
REFS = ["[CURRENT:1]", "[CURRENT:2]", "[CURRENT:3]"]


def package(n):
    return {"packageId": f"pkg-{n}", "documents": [{"type": "current", "text": f"Reconciled invoice {n}: " + " ".join(REFS)}], "invoice": {"vendorName": "Acme", "invoiceNumber": f"INV-{n}", "amountMinor": 1000 + n, "currency": "INR"}}


def model_response(packages, revision):
    return [{"packageId": p["packageId"], "action": "settle_invoice", "facts": p["invoice"], "evidenceRefs": REFS[:], "rationale": f"settle_invoice: The reconciled invoice is supported by {REFS[0]} and {REFS[1]}; no hold or approval condition applies."} for p in packages]


def request_body(packages, message_id="msg-123", configuration=None):
    return {"message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"mediaType": INPUT, "data": {"batchId": "batch-1", "policyRevision": "r1", "packages": packages}}]}, "configuration": configuration or {"returnImmediately": False}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(str(tmp_path / "agent.sqlite3")))
    monkeypatch.setattr(main, "TOKENS", ["alpha", "beta"])
    monkeypatch.setattr(main, "DEMO", False)
    calls = []

    def fake_model(packages, revision):
        calls.append(copy.deepcopy(packages))
        return model_response(packages, revision)

    monkeypatch.setattr(engine, "ask_model", fake_model)
    with TestClient(main.app) as client:
        yield client, calls


def post(client, body):
    return client.post("/a2a/message:send", headers=HEAD, json=body)


def test_configuration_and_reordered_keys_do_not_recompute(env):
    client, calls = env
    original = request_body([package(1)], str(uuid.uuid4()))
    first = post(client, original)
    assert first.status_code == 200, first.text
    assert len(calls) == 1
    replay = copy.deepcopy(original)
    replay["configuration"] = {"returnImmediately": True, "historyLength": 0}
    # JSON objects are semantically identical despite reordered dictionary keys.
    replay["message"] = dict(reversed(list(replay["message"].items())))
    replay["message"]["parts"][0]["data"] = dict(reversed(list(replay["message"]["parts"][0]["data"].items())))
    second = post(client, replay)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(calls) == 1, "Replay unexpectedly called the model"


def test_semantic_conflict_preserves_original(env):
    client, calls = env
    original = request_body([package(2)], str(uuid.uuid4()))
    first = post(client, original)
    assert first.status_code == 200, first.text
    task = first.json()["task"]
    changed = copy.deepcopy(original)
    changed["message"]["parts"][0]["data"]["packages"][0]["invoice"]["amountMinor"] += 99
    conflict = post(client, changed)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get(f"/a2a/tasks/{task['id']}", headers=HEAD).json() == task
    assert len(calls) == 1


def test_core_cache_and_fresh_audit(env):
    client, calls = env
    core = [package(i) for i in range(12)]
    first = post(client, request_body(core, str(uuid.uuid4())))
    assert first.status_code == 200, first.text
    assert len(calls) == 1 and len(calls[0]) == 12
    second = post(client, request_body(core + [package(99)], str(uuid.uuid4())))
    assert second.status_code == 200, second.text
    assert len(calls) == 2
    assert [p["packageId"] for p in calls[1]] == ["pkg-99"], "Core packages were unnecessarily sent to model"
    proposals = second.json()["task"]["artifacts"][0]["parts"][0]["data"]["proposals"]
    assert len(proposals) == 13
    assert {p["packageId"] for p in proposals} == {p["packageId"] for p in core + [package(99)]}


def test_strict_proposal_contract(env):
    client, _ = env
    packages = [package(i) for i in range(12)]
    response = post(client, request_body(packages, str(uuid.uuid4())))
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert len(task["artifacts"]) == 1
    parts = task["artifacts"][0]["parts"]
    assert len(parts) == 1
    assert parts[0]["mediaType"] == main.PROPOSAL_MODE
    assert len(parts[0]["data"]["proposals"]) == len(packages)
    seen = set()
    for p, source in zip(parts[0]["data"]["proposals"], packages):
        assert p["packageId"] == source["packageId"]
        assert isinstance(p["actionId"], str) and len(p["actionId"]) >= 12
        assert p["actionId"] not in seen
        seen.add(p["actionId"])
        assert p["action"] in engine.ACTIONS
        assert p["facts"] == source["invoice"]
        assert len(p["evidenceRefs"]) == 3 and len(set(p["evidenceRefs"])) == 3
        assert set(p["evidenceRefs"]) == set(REFS)
        assert 60 <= len(p["rationale"]) <= 1500
        assert p["action"] in p["rationale"]
        assert sum(ref in p["rationale"] for ref in p["evidenceRefs"]) >= 2
        engine.validate_decision(source, {k: v for k, v in p.items() if k != "actionId"})
    assert len(task["history"]) == 1
    assert not any(part.get("mediaType") == main.RECEIPT_MODE for artifact in task["artifacts"] for part in artifact["parts"])


def test_rejects_invalid_action_or_evidence():
    source = package(1)
    valid = model_response([source], "r1")[0]
    for mutation in (lambda x: x.update(action="pay_now"), lambda x: x.update(evidenceRefs=[REFS[0], REFS[1], "[DECOY:99]"]), lambda x: x.update(rationale="Too short")):
        invalid = copy.deepcopy(valid)
        mutation(invalid)
        with pytest.raises((ValueError, Exception)):
            engine.validate_decision(source, invalid)

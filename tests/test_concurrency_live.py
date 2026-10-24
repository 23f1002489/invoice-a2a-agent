"""Run from project root: python -m pytest -q tests/test_concurrency_live.py"""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

HEADERS = {"Authorization": "Bearer dev-token-alpha", "A2A-Version": "1.0", "Content-Type": "application/a2a+json"}

def pkg(pid="p1", amount=100):
    return {"packageId": pid, "source": f"Current authoritative paragraph [{pid}-A] [{pid}-B] [{pid}-C] confirms amount {amount} and approval required.", "amountMinor": amount}

def request(mid="concurrent-1", amount=100):
    return {"message": {"messageId": mid, "role": "ROLE_USER", "parts": [{"mediaType": main.INPUT_MODE, "data": {"batchId": "b1", "policyRevision": "r1", "packages": [pkg(amount=amount)]}}]}, "configuration": {"returnImmediately": False}}

def fake_proposal(p):
    pid = p["packageId"]
    refs = [f"[{pid}-{x}]" for x in "ABC"]
    return {"packageId": pid, "action": "request_approval", "facts": {"vendorName": "Test Vendor", "invoiceNumber": "INV-1", "amountMinor": p["amountMinor"], "currency": "INR"}, "evidenceRefs": refs, "rationale": f"Choose request_approval: {refs[0]} and {refs[1]} establish that the otherwise valid invoice requires authorization before settlement."}

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", main.SQLiteStore(str(tmp_path / "db.sqlite3")))
    monkeypatch.setattr(main, "DEMO", False)
    monkeypatch.setattr(main, "TOKENS", ["dev-token-alpha", "dev-token-beta"])
    return main.app

def test_simultaneous_identical_requests_single_model_call(setup, monkeypatch):
    """Known Phase 3 weakness: likely FAILS until an atomic in-flight claim is implemented."""
    counter = 0
    lock = threading.Lock()
    def slow_model(packages, policy_revision):
        nonlocal counter
        with lock:
            counter += 1
        time.sleep(0.25)  # enlarge race window
        return [fake_proposal(p) for p in packages]
    monkeypatch.setattr("app.decision_engine.ask_model", slow_model)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: TestClient(setup).post("/a2a/message:send", headers=HEADERS, json=request()), range(8)))
    assert all(r.status_code == 200 for r in responses), [(r.status_code, r.text) for r in responses]
    tasks = [r.json()["task"] for r in responses]
    assert len({t["id"] for t in tasks}) == 1
    assert all(t == tasks[0] for t in tasks)
    assert counter == 1, f"Model called {counter} times for same messageId; need atomic claim before AI call"

def test_simultaneous_conflicting_messages_one_409(setup, monkeypatch):
    monkeypatch.setattr("app.decision_engine.ask_model", lambda packages, policy_revision: [fake_proposal(p) for p in packages])
    barrier = threading.Barrier(2)
    def submit(amount):
        barrier.wait()
        return TestClient(setup).post("/a2a/message:send", headers=HEADERS, json=request("same-id", amount))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, amount) for amount in (100, 200)]
        responses = [f.result() for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert "IDEMPOTENCY_CONFLICT" in next(r.text for r in responses if r.status_code == 409)
    winner = next(r.json()["task"] for r in responses if r.status_code == 200)
    with TestClient(setup) as client:
        read = client.get(f"/a2a/tasks/{winner['id']}", headers=HEADERS)
    assert read.status_code == 200 and read.json() == winner

def test_concurrent_different_messages_same_package_cache_single_call(setup, monkeypatch):
    """Optional strict requirement: one model call for identical uncached package across distinct messages."""
    counter = 0
    lock = threading.Lock()
    def slow_model(packages, policy_revision):
        nonlocal counter
        with lock: counter += 1
        time.sleep(0.25)
        return [fake_proposal(p) for p in packages]
    monkeypatch.setattr("app.decision_engine.ask_model", slow_model)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda i: TestClient(setup).post("/a2a/message:send", headers=HEADERS, json=request(f"distinct-{i}")), range(6)))
    assert all(r.status_code == 200 for r in responses)
    assert counter == 1, f"Model called {counter} times for same uncached package; need package-level in-flight locking"

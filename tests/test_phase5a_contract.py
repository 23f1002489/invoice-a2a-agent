
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import main, decision_engine
from app.storage import SQLiteStore


HEAD = {
    "Authorization": "Bearer alpha",
    "A2A-Version": "1.0",
    "Content-Type": "application/a2a+json",
}
HEAD_B = {**HEAD, "Authorization": "Bearer beta"}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main, "store",
        SQLiteStore(str(tmp_path / "phase5a.sqlite"))
    )
    monkeypatch.setattr(main, "TOKENS", ["alpha", "beta"])
    monkeypatch.setattr(main, "DEMO", False)

    def fake_model(packages, revision):
        return [
            {
                "packageId": p["packageId"],
                "action": "settle_invoice",
                "facts": {
                    "vendorName": "Vendor",
                    "invoiceNumber": p["packageId"],
                    "amountMinor": 100,
                    "currency": "INR",
                },
                "evidenceRefs": ["[X1]", "[X2]", "[X3]"],
                "rationale": (
                    "settle_invoice is supported by [X1] and "
                    "[X2]; [X3] confirms approval is unnecessary."
                ),
            }
            for p in packages
        ]

    monkeypatch.setattr(
        decision_engine, "ask_model", fake_model
    )
    return TestClient(main.app)


def create(client, n=2, headers=HEAD):
    packages = [
        {
            "packageId": f"package-{i}-{uuid.uuid4().hex[:6]}",
            "documents": [{
                "content": (
                    "Invoice verified [X1] checked [X2] "
                    "within authority [X3]"
                )
            }],
        }
        for i in range(n)
    ]

    batch_id = "batch-" + uuid.uuid4().hex
    msg = {
        "messageId": "init-" + uuid.uuid4().hex,
        "role": "ROLE_USER",
        "parts": [{
            "mediaType": main.INPUT_MODE,
            "data": {
                "batchId": batch_id,
                "policyRevision": "r1",
                "packages": packages,
            },
        }],
    }

    response = client.post(
        "/a2a/message:send",
        headers=headers,
        json={"message": msg},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"], msg, batch_id


def continuation(task, batch_id, outcomes=None):
    proposals = task["artifacts"][0]["parts"][0]["data"]["proposals"]
    outcomes = outcomes or ["ACCEPTED"] * len(proposals)

    results = [
        {
            "packageId": p["packageId"],
            "actionId": p["actionId"],
            "action": p["action"],
            "outcome": outcome,
            "receiptNonce": "nonce-" + uuid.uuid4().hex,
        }
        for p, outcome in zip(proposals, outcomes)
    ]

    return {
        "messageId": "result-" + uuid.uuid4().hex,
        "role": "ROLE_USER",
        "taskId": task["id"],
        "contextId": task["contextId"],
        "parts": [{
            "mediaType": main.RESULT_MODE,
            "data": {
                "batchId": batch_id,
                "results": results,
            },
        }],
    }


def send(client, msg, headers=HEAD):
    return client.post(
        "/a2a/message:send",
        headers=headers,
        json={"message": msg},
    )


def test_mixed_outcomes_receipts_and_history(ctx):
    task, original, batch = create(ctx)
    msg = continuation(task, batch, ["ACCEPTED", "REJECTED"])

    response = send(ctx, msg)
    assert response.status_code == 200, response.text

    completed = response.json()["task"]
    assert completed["status"]["state"] == "TASK_STATE_COMPLETED"
    assert completed["history"] == [original, msg]
    assert completed["artifacts"][0] == task["artifacts"][0]

    receipts = completed["artifacts"][-1]["parts"][0]["data"]["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["outcome"] == "ACCEPTED"
    assert receipts[0]["packageId"] == (
        msg["parts"][0]["data"]["results"][0]["packageId"]
    )


def test_wrong_bindings_do_not_complete(ctx):
    task, _, batch = create(ctx)
    valid = continuation(task, batch)

    edits = [
        ("context", lambda m: m.update(contextId="wrong")),
        ("batch", lambda m: m["parts"][0]["data"].update(batchId="wrong")),
        ("actionId", lambda m: m["parts"][0]["data"]["results"][0].update(actionId="wrong")),
        ("action", lambda m: m["parts"][0]["data"]["results"][0].update(action="hold_invoice")),
        ("missing", lambda m: m["parts"][0]["data"]["results"].pop()),
        ("duplicate", lambda m: m["parts"][0]["data"]["results"].__setitem__(
            1, copy.deepcopy(m["parts"][0]["data"]["results"][0])
        )),
        ("outcome", lambda m: m["parts"][0]["data"]["results"][0].update(outcome="UNKNOWN")),
        ("nonce", lambda m: m["parts"][0]["data"]["results"][0].update(receiptNonce="")),
    ]

    for name, edit in edits:
        msg = copy.deepcopy(valid)
        msg["messageId"] = "bad-" + name + "-" + uuid.uuid4().hex
        edit(msg)

        response = send(ctx, msg)
        assert response.status_code in (400, 409, 422), (
            name, response.status_code, response.text
        )

        current = ctx.get(
            "/a2a/tasks/" + task["id"], headers=HEAD
        ).json()
        assert current["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    assert send(ctx, valid).status_code == 200


def test_replay_and_semantic_conflict(ctx):
    task, original, batch = create(ctx)

    replay = send(ctx, original)
    assert replay.status_code == 200
    assert replay.json()["task"]["id"] == task["id"]

    altered = copy.deepcopy(original)
    altered["parts"][0]["data"]["batchId"] = "changed"
    assert send(ctx, altered).status_code == 409

    msg = continuation(task, batch)
    first = send(ctx, msg)
    assert first.status_code == 200

    second = send(ctx, msg)
    assert second.status_code == 200
    assert second.json() == first.json()

    changed = copy.deepcopy(msg)
    changed["parts"][0]["data"]["results"][0]["receiptNonce"] = "changed"
    assert send(ctx, changed).status_code == 409


def test_cross_principal_isolation(ctx):
    task, _, batch = create(ctx)

    assert ctx.get(
        "/a2a/tasks/" + task["id"], headers=HEAD_B
    ).status_code == 404

    assert ctx.post(
        "/a2a/tasks/" + task["id"] + ":cancel",
        headers=HEAD_B,
    ).status_code == 404

    assert send(
        ctx, continuation(task, batch), HEAD_B
    ).status_code == 404

    tasks = ctx.get(
        "/a2a/tasks", headers=HEAD_B
    ).json()["tasks"]

    assert all(t["id"] != task["id"] for t in tasks)


def test_cancel_completion_race_exactly_one_wins(ctx):
    task, _, batch = create(ctx)
    msg = continuation(task, batch)

    def complete():
        return send(ctx, msg)

    def cancel():
        return ctx.post(
            "/a2a/tasks/" + task["id"] + ":cancel",
            headers=HEAD,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(complete)
        b = pool.submit(cancel)
        responses = [a.result(), b.result()]

    assert sorted(r.status_code for r in responses) == [200, 409], [
        (r.status_code, r.text) for r in responses
    ]

    current = ctx.get(
        "/a2a/tasks/" + task["id"], headers=HEAD
    ).json()

    assert current["status"]["state"] in (
        "TASK_STATE_COMPLETED",
        "TASK_STATE_CANCELED",
    )

    if current["status"]["state"] == "TASK_STATE_CANCELED":
        assert len(current["artifacts"]) == 1


def test_restart_durability(ctx):
    task, _, batch = create(ctx)

    path = main.store.path
    main.store = SQLiteStore(path)

    assert ctx.get(
        "/a2a/tasks/" + task["id"],
        headers=HEAD,
    ).status_code == 200

    assert send(
        ctx, continuation(task, batch)
    ).status_code == 200

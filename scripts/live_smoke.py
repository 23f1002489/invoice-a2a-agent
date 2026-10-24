
import argparse
import copy
import os
import uuid

import httpx


parser = argparse.ArgumentParser()
parser.add_argument("--base", required=True)
args = parser.parse_args()

base = args.base.rstrip("/")
if not base.startswith("https://"):
    raise SystemExit("A public HTTPS base URL is required")

token = os.getenv("A2A_TEST_TOKEN")
if not token:
    raise SystemExit("Set A2A_TEST_TOKEN in PowerShell")

origin = base.split("/a2a")[0]

headers = {
    "Authorization": f"Bearer {token}",
    "A2A-Version": "1.0",
    "Content-Type": "application/a2a+json",
}

uid = uuid.uuid4().hex

package = {
    "packageId": f"live-{uid}",
    "documents": [{
        "content": (
            f"Current review: Pilot Paper invoice INV-{uid[:8]} "
            f"for 1200 INR. [L-{uid[:6]}-1] "
            "Delivery and vendor identity are verified. "
            f"[L-{uid[:6]}-2] "
            "Within autonomous payment authority and no prior "
            f"payment recorded. [L-{uid[:6]}-3]"
        )
    }],
}

message = {
    "messageId": f"live-init-{uid}",
    "role": "ROLE_USER",
    "parts": [{
        "mediaType": "application/vnd.ga5.invoice-claim-batch+json",
        "data": {
            "batchId": f"live-batch-{uid}",
            "policyRevision": "live-smoke-1",
            "packages": [package],
        },
    }],
}


with httpx.Client(timeout=44, follow_redirects=False) as client:

    card = client.get(
        origin + "/.well-known/agent-card.json"
    )
    assert card.status_code == 200, card.text
    assert card.json()["supportedInterfaces"]
    print("PASS: Public Agent Card")

    noauth = client.get(base + "/tasks")
    assert noauth.status_code in (401, 403)
    print("PASS: Authentication required")

    response = client.post(
        base + "/message:send",
        headers=headers,
        json={"message": message},
    )
    assert response.status_code == 200, response.text
    assert response.headers.get(
        "content-type", ""
    ).startswith("application/a2a+json")

    task = response.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    print("PASS: Proposal creation", task["id"])

    again = client.post(
        base + "/message:send",
        headers=headers,
        json={"message": message},
    )
    assert again.status_code == 200
    assert again.json()["task"]["id"] == task["id"]
    print("PASS: Idempotent replay")

    altered = copy.deepcopy(message)
    altered["parts"][0]["data"]["batchId"] += "-changed"

    conflict = client.post(
        base + "/message:send",
        headers=headers,
        json={"message": altered},
    )
    assert conflict.status_code == 409
    print("PASS: Semantic conflict")

    fetched = client.get(
        base + "/tasks/" + task["id"],
        headers=headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == task["id"]
    print("PASS: Task retrieval")

    listed = client.get(
        base + "/tasks",
        headers=headers,
    )
    assert listed.status_code == 200
    assert any(
        t["id"] == task["id"]
        for t in listed.json()["tasks"]
    )
    print("PASS: Task listing")

    canceled = client.post(
        base + "/tasks/" + task["id"] + ":cancel",
        headers=headers,
    )
    assert canceled.status_code == 200
    assert (
        canceled.json()["status"]["state"]
        == "TASK_STATE_CANCELED"
    )
    print("PASS: Cancellation")

print("\nAll live smoke checks passed.")

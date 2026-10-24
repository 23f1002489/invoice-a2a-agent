
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

from dotenv import load_dotenv

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)

from app.postgres_storage import Store

url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("DATABASE_URL is missing")

store = Store(url)

test_id = uuid.uuid4().hex
principal = "neon-test-" + test_id

print("Testing Neon PostgreSQL...")

assert store.get_task(principal, test_id) is None
print("PASS: Task lookup")

assert store.list_tasks(principal) == []
print("PASS: Task listing")

key = "decision-test-" + test_id
decision = {"action": "hold_invoice"}

store.put_decision(key, decision)
assert store.get_decision(key) == decision
print("PASS: Decision cache")

reservation_key = "reservation-test-" + test_id

assert store.reserve(
    "package", reservation_key, test_id, "owner-1"
) == "claimed"

assert store.reserve(
    "package", reservation_key, test_id, "owner-2"
) == "busy"

store.release("package", reservation_key, "owner-1")

assert store.reserve(
    "package", reservation_key, test_id, "owner-2"
) == "claimed"

store.release("package", reservation_key, "owner-2")
print("PASS: Reservation locking")

print("\nNeon storage smoke test passed.")

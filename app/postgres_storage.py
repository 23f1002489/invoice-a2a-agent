
import copy
import json
import time
import uuid
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .storage import canonical, message_hash


class Store:
    def __init__(self, url):
        self.url = url
        self.initialize()

    @contextmanager
    def transaction(self):
        with psycopg.connect(
            self.url,
            row_factory=dict_row,
            connect_timeout=10,
        ) as db:
            with db.cursor() as cur:
                yield cur

    def initialize(self):
        with self.transaction() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_principal
                ON tasks(principal, created_at, id)
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    principal TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    message_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    response_snapshot TEXT,
                    PRIMARY KEY(principal, message_id)
                )
            """)
            c.execute("""
                ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS response_snapshot TEXT
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    kind TEXT NOT NULL,
                    reservation_key TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY(kind, reservation_key)
                )
            """)

    def get_task(self, principal, task_id):
        with self.transaction() as c:
            c.execute(
                "SELECT payload FROM tasks WHERE principal=%s AND id=%s",
                (principal, task_id),
            )
            row = c.fetchone()
            return json.loads(row["payload"]) if row else None

    def list_tasks(self, principal):
        with self.transaction() as c:
            c.execute(
                """
                SELECT payload FROM tasks
                WHERE principal=%s
                ORDER BY created_at, id
                """,
                (principal,),
            )
            return [json.loads(r["payload"]) for r in c.fetchall()]

    def _prior(self, c, principal, message_id, message):
        c.execute(
            """
            SELECT message_hash, task_id, response_snapshot
            FROM messages
            WHERE principal=%s AND message_id=%s
            """,
            (principal, message_id),
        )
        row = c.fetchone()

        if row is None:
            return None, None

        if row["message_hash"] != message_hash(message):
            return "conflict", None

        if row["response_snapshot"] is not None:
            return "replay", json.loads(row["response_snapshot"])

        # Compatibility with older records.
        c.execute(
            """
            SELECT payload FROM tasks
            WHERE principal=%s AND id=%s
            """,
            (principal, row["task_id"]),
        )
        task = c.fetchone()

        if task is None:
            return "missing", None

        return "replay", json.loads(task["payload"])

    def lookup_message(self, principal, message_id, message):
        with self.transaction() as c:
            return self._prior(c, principal, message_id, message)

    def save_message_task(self, principal, message_id, message, task):
        with self.transaction() as c:
            status, old = self._prior(
                c, principal, message_id, message
            )
            if status is not None:
                return status, old

            saved = copy.deepcopy(task)
            created_at = saved.pop("created_at_internal", "")

            c.execute(
                """
                INSERT INTO tasks
                    (id, principal, context_id, state, payload, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    saved["id"],
                    principal,
                    saved["contextId"],
                    saved["status"]["state"],
                    canonical(saved),
                    created_at,
                ),
            )

            c.execute(
                """
                INSERT INTO messages
                    (principal, message_id, message_hash,
                     task_id, response_snapshot)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    principal,
                    message_id,
                    message_hash(message),
                    saved["id"],
                    canonical(saved),
                ),
            )

            task.pop("created_at_internal", None)
            return "created", saved

    def cancel(self, principal, task_id):
        with self.transaction() as c:
            c.execute(
                """
                SELECT payload FROM tasks
                WHERE principal=%s AND id=%s
                FOR UPDATE
                """,
                (principal, task_id),
            )
            row = c.fetchone()

            if row is None:
                return "missing", None

            task = json.loads(row["payload"])

            if task["status"]["state"] in (
                "TASK_STATE_COMPLETED",
                "TASK_STATE_CANCELED",
                "TASK_STATE_FAILED",
            ):
                return "terminal", task

            task["status"] = {"state": "TASK_STATE_CANCELED"}

            c.execute(
                """
                UPDATE tasks
                SET state=%s, payload=%s
                WHERE principal=%s AND id=%s
                """,
                (
                    "TASK_STATE_CANCELED",
                    canonical(task),
                    principal,
                    task_id,
                ),
            )

            return "canceled", task

    def get_decision(self, cache_key):
        with self.transaction() as c:
            c.execute(
                "SELECT payload FROM decisions WHERE cache_key=%s",
                (cache_key,),
            )
            row = c.fetchone()
            return json.loads(row["payload"]) if row else None

    def put_decision(self, cache_key, decision):
        with self.transaction() as c:
            c.execute(
                """
                INSERT INTO decisions(cache_key, payload)
                VALUES (%s, %s)
                ON CONFLICT(cache_key) DO NOTHING
                """,
                (cache_key, canonical(decision)),
            )

    def reserve(
        self, kind, key, digest, owner, lease_seconds=100
    ):
        with self.transaction() as c:
            now = time.time()

            c.execute(
                """
                INSERT INTO reservations
                    (kind, reservation_key, digest, owner, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(kind, reservation_key) DO NOTHING
                """,
                (
                    kind, key, digest, owner,
                    now + lease_seconds,
                ),
            )

            if c.rowcount == 1:
                return "claimed"

            c.execute(
                """
                SELECT digest, owner, expires_at
                FROM reservations
                WHERE kind=%s AND reservation_key=%s
                FOR UPDATE
                """,
                (kind, key),
            )
            row = c.fetchone()

            if kind == "message" and row["digest"] != digest:
                return "conflict"

            if row["expires_at"] > now and row["owner"] != owner:
                return "busy"

            c.execute(
                """
                UPDATE reservations
                SET digest=%s, owner=%s, expires_at=%s
                WHERE kind=%s AND reservation_key=%s
                """,
                (
                    digest, owner, now + lease_seconds,
                    kind, key,
                ),
            )

            return "claimed"

    def release(self, kind, key, owner):
        with self.transaction() as c:
            c.execute(
                """
                DELETE FROM reservations
                WHERE kind=%s AND reservation_key=%s AND owner=%s
                """,
                (kind, key, owner),
            )

    def finish_task(
        self, principal, task_id, context_id, message_id,
        message, results, batch_id, receipt_mode
    ):
        with self.transaction() as c:
            status, old = self._prior(
                c, principal, message_id, message
            )
            if status is not None:
                return status, old

            c.execute(
                """
                SELECT payload FROM tasks
                WHERE principal=%s AND id=%s
                FOR UPDATE
                """,
                (principal, task_id),
            )
            row = c.fetchone()

            if row is None:
                return "missing", None

                        # Another worker may have completed the same message
            # while this transaction waited for the Task row lock.
            status, old = self._prior(
                c, principal, message_id, message
            )
            if status is not None:
                return status, old

            task = json.loads(row["payload"])

            if task["contextId"] != context_id:
                return "binding", None

            if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                return "terminal", None

            try:
                source = task["artifacts"][0]["parts"][0]["data"]
                if source["batchId"] != batch_id:
                    return "binding", None

                proposals = source["proposals"]
                indexed = {
                    p["packageId"]: p for p in proposals
                }
            except (KeyError, IndexError, TypeError):
                return "binding", None

            if (
                not isinstance(results, list)
                or len(results) != len(proposals)
                or len(indexed) != len(proposals)
            ):
                return "binding", None

            seen = set()
            accepted = []

            for result in results:
                if not isinstance(result, dict):
                    return "binding", None

                pid = result.get("packageId")

                if pid in seen or pid not in indexed:
                    return "binding", None

                seen.add(pid)
                proposal = indexed[pid]

                if (
                    result.get("actionId") != proposal["actionId"]
                    or result.get("action") != proposal["action"]
                    or result.get("outcome") not in (
                        "ACCEPTED", "REJECTED"
                    )
                ):
                    return "binding", None

                nonce = result.get("receiptNonce")

                if not isinstance(nonce, str) or not nonce:
                    return "binding", None

                if result["outcome"] == "ACCEPTED":
                    accepted.append({
                        "packageId": pid,
                        "actionId": proposal["actionId"],
                        "action": proposal["action"],
                        "outcome": "ACCEPTED",
                        "receiptNonce": nonce,
                    })

            if seen != set(indexed):
                return "binding", None

            task["history"].append(message)

            task["artifacts"].append({
                "artifactId": str(uuid.uuid4()),
                "parts": [{
                    "mediaType": receipt_mode,
                    "data": {
                        "batchId": batch_id,
                        "receipts": accepted,
                    },
                }],
            })

            task["status"] = {
                "state": "TASK_STATE_COMPLETED"
            }

            c.execute(
                """
                UPDATE tasks
                SET state=%s, payload=%s
                WHERE principal=%s AND id=%s
                """,
                (
                    "TASK_STATE_COMPLETED",
                    canonical(task),
                    principal,
                    task_id,
                ),
            )

            c.execute(
                """
                INSERT INTO messages
                    (principal, message_id, message_hash,
                     task_id, response_snapshot)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    principal,
                    message_id,
                    message_hash(message),
                    task_id,
                    canonical(task),
                ),
            )

            return "completed", task

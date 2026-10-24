import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager

LOCK = threading.RLock()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def message_hash(message):
    return hashlib.sha256(canonical(message).encode('utf-8')).hexdigest()


class Store:
    def __init__(self, path):
        self.path = path
        self.initialize()

    @contextmanager
    def transaction(self):
        with LOCK:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def initialize(self):
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                context_id TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS messages (
                principal TEXT NOT NULL, message_id TEXT NOT NULL,
                message_hash TEXT NOT NULL, task_id TEXT NOT NULL,
                PRIMARY KEY (principal, message_id),
                FOREIGN KEY (task_id) REFERENCES tasks(id))''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_tasks_principal ON tasks(principal)')
            db.execute('CREATE TABLE IF NOT EXISTS decisions (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def get_task(self, principal, task_id):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM tasks WHERE principal=? AND id=?', (principal, task_id)).fetchone()
            return json.loads(row['payload']) if row else None

    def list_tasks(self, principal):
        with self.transaction() as db:
            rows = db.execute('SELECT payload FROM tasks WHERE principal=? ORDER BY created_at, id', (principal,)).fetchall()
            return [json.loads(r['payload']) for r in rows]

    def save_message_task(self, principal, message_id, message, task):
        digest = message_hash(message)
        with self.transaction() as db:
            previous = db.execute('SELECT message_hash, task_id FROM messages WHERE principal=? AND message_id=?', (principal, message_id)).fetchone()
            if previous:
                if previous['message_hash'] != digest:
                    return 'conflict', None
                row = db.execute('SELECT payload FROM tasks WHERE id=?', (previous['task_id'],)).fetchone()
                return 'replay', json.loads(row['payload'])
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (
                task['id'], principal, task['contextId'], task['status']['state'],
                canonical(task), task['created_at_internal']))
            db.execute('INSERT INTO messages VALUES (?,?,?,?)', (principal, message_id, digest, task['id']))
            task.pop('created_at_internal', None)
            db.execute('UPDATE tasks SET payload=? WHERE id=?', (canonical(task), task['id']))
            return 'created', task

    def lookup_message(self, principal, message_id, message):
        with self.transaction() as db:
            row = db.execute('SELECT message_hash, task_id FROM messages WHERE principal=? AND message_id=?', (principal, message_id)).fetchone()
            if row is None:
                return None, None
            if row['message_hash'] != message_hash(message):
                return 'conflict', None
            task = db.execute('SELECT payload FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
            return 'replay', json.loads(task['payload'])

    def cancel(self, principal, task_id):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM tasks WHERE principal=? AND id=?', (principal, task_id)).fetchone()
            if row is None:
                return 'missing', None
            task = json.loads(row['payload'])
            if task['status']['state'] in ('TASK_STATE_COMPLETED', 'TASK_STATE_CANCELED', 'TASK_STATE_FAILED'):
                return 'terminal', task
            task['status'] = {'state': 'TASK_STATE_CANCELED'}
            db.execute('UPDATE tasks SET state=?, payload=? WHERE principal=? AND id=?', (
                'TASK_STATE_CANCELED', canonical(task), principal, task_id))
            return 'canceled', task

    def get_decision(self, cache_key):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM decisions WHERE cache_key=?', (cache_key,)).fetchone()
            return json.loads(row['payload']) if row else None

    def put_decision(self, cache_key, decision):
        with self.transaction() as db:
            db.execute('INSERT OR IGNORE INTO decisions(cache_key,payload) VALUES (?,?)', (cache_key, canonical(decision)))

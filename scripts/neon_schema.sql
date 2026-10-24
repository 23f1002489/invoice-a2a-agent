
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    context_id TEXT NOT NULL,
    state TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_principal
ON tasks (principal, created_at);

CREATE TABLE IF NOT EXISTS messages (
    principal TEXT NOT NULL,
    message_id TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    task_id TEXT,
    response_snapshot JSONB,
    PRIMARY KEY (principal, message_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    cache_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_key TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reservations_expiry
ON reservations (lease_expires_at);

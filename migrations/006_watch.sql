ALTER TABLE sources ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sources ADD COLUMN last_count INTEGER;
ALTER TABLE sources ADD COLUMN last_error_at TEXT;
ALTER TABLE job_sources ADD COLUMN missing_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE job_sources ADD COLUMN present INTEGER NOT NULL DEFAULT 1;
CREATE TABLE alert_batches (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL,
    jobs TEXT NOT NULL,
    desktop_requested INTEGER NOT NULL,
    telegram_requested INTEGER NOT NULL,
    desktop_attempted_at TEXT,
    telegram_attempted_at TEXT,
    desktop_error TEXT,
    telegram_error TEXT
);
CREATE TABLE job_notifications (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    content_hash TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    batch_id INTEGER NOT NULL REFERENCES alert_batches(id),
    PRIMARY KEY(job_id, content_hash)
);

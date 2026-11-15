CREATE TABLE job_reviews (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    decision TEXT NOT NULL CHECK (decision IN ('saved', 'rejected', 'later')),
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE keyword_matches (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

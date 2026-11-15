CREATE TABLE profile_extractions (
    content_hash TEXT PRIMARY KEY, payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);
CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
    content_hash TEXT NOT NULL, scoring_version TEXT NOT NULL,
    profile_version INTEGER NOT NULL REFERENCES candidate_profiles(id),
    payload TEXT NOT NULL, weights TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE(job_id, content_hash)
);
CREATE TABLE score_overrides (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    recommendation TEXT NOT NULL CHECK(recommendation IN ('apply','maybe','skip')),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

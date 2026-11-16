ALTER TABLE applications RENAME TO applications_m3;
CREATE TABLE applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    state TEXT NOT NULL CHECK(state IN ('saved','preparing','awaiting_review','approved','blocked','manual_required','withdrawn','submitted','submitting','unknown_result')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    submitted_at TEXT
);
INSERT INTO applications SELECT * FROM applications_m3;
DROP TABLE applications_m3;
CREATE TABLE application_approvals (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    material_ids TEXT NOT NULL,
    answers_hash TEXT NOT NULL,
    answers_content TEXT NOT NULL,
    approved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);
CREATE TABLE application_attempts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    submission_possible INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL
);
CREATE INDEX attempts_job ON application_attempts(job_id);
CREATE TABLE application_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    attempt_id INTEGER REFERENCES application_attempts(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

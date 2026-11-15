CREATE TABLE applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    state TEXT NOT NULL CHECK(state IN ('saved','preparing','awaiting_review','approved','blocked','manual_required','withdrawn','submitted')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    submitted_at TEXT
);
CREATE TABLE material_versions (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    profile_version INTEGER NOT NULL REFERENCES candidate_profiles(id),
    kind TEXT NOT NULL CHECK(kind IN ('letter','resume','answers')),
    content TEXT NOT NULL,
    path TEXT NOT NULL,
    warnings TEXT NOT NULL,
    provenance TEXT NOT NULL,
    job_hash TEXT NOT NULL,
    pdf_path TEXT,
    pdf_hash TEXT,
    ats_warnings TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);
CREATE INDEX materials_job_kind ON material_versions(job_id,kind,id);
CREATE TABLE screening_questions (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    label TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '[]',
    required INTEGER NOT NULL DEFAULT 1,
    UNIQUE(job_id,label)
);
INSERT INTO applications(job_id,state) SELECT job_id,'saved' FROM job_reviews WHERE decision='saved';

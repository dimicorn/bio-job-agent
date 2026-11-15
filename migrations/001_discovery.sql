CREATE TABLE candidate_profiles (
    id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);
CREATE TABLE employers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, industry TEXT NOT NULL,
    review_url TEXT NOT NULL, industry_verified INTEGER NOT NULL
);
CREATE TABLE sources (
    id TEXT PRIMARY KEY, employer_id INTEGER NOT NULL REFERENCES employers(id),
    adapter TEXT NOT NULL, board TEXT NOT NULL, config TEXT NOT NULL,
    health TEXT NOT NULL DEFAULT 'never', last_error TEXT, last_checked_at TEXT,
    last_success_at TEXT, next_allowed_at TEXT
);
CREATE TABLE sync_runs (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    profile_id INTEGER NOT NULL REFERENCES candidate_profiles(id),
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
    fetched INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0, malformed INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE raw_postings (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL, disposition TEXT NOT NULL,
    UNIQUE(source_id, external_id, content_hash)
);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY, employer_id INTEGER NOT NULL REFERENCES employers(id),
    title_key TEXT NOT NULL, location_key TEXT NOT NULL, canonical_url TEXT NOT NULL,
    payload TEXT NOT NULL, description TEXT NOT NULL,
    discovered_at TEXT NOT NULL, last_verified_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX jobs_identity ON jobs(employer_id, title_key, location_key);
CREATE INDEX jobs_url ON jobs(canonical_url);
CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
    source_id TEXT NOT NULL REFERENCES sources(id), external_id TEXT NOT NULL,
    source_url TEXT NOT NULL, apply_url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    raw_posting_id INTEGER NOT NULL REFERENCES raw_postings(id), eligible INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_id, external_id)
);

ALTER TABLE job_sources ADD COLUMN first_seen_reliable INTEGER NOT NULL DEFAULT 0;
UPDATE job_sources SET first_seen_reliable=1 WHERE EXISTS (
    SELECT 1 FROM sync_runs r WHERE r.source_id=job_sources.source_id
      AND r.status='ok' AND r.finished_at < job_sources.first_seen_at
);

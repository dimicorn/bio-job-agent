ALTER TABLE jobs ADD COLUMN owner_origin_id INTEGER REFERENCES job_sources(id);
ALTER TABLE job_sources ADD COLUMN posting_payload TEXT;
UPDATE jobs SET owner_origin_id=COALESCE(
    (SELECT MIN(id) FROM job_sources WHERE job_id=jobs.id
     AND source_url=json_extract(jobs.payload, '$.source_url')),
    (SELECT MIN(id) FROM job_sources WHERE job_id=jobs.id)
);
UPDATE job_sources SET posting_payload=(SELECT payload FROM jobs WHERE jobs.owner_origin_id=job_sources.id)
WHERE id IN (SELECT owner_origin_id FROM jobs);
CREATE TABLE duplicate_separations (
    origin_a INTEGER NOT NULL REFERENCES job_sources(id),
    origin_b INTEGER NOT NULL REFERENCES job_sources(id),
    PRIMARY KEY(origin_a, origin_b), CHECK(origin_a < origin_b)
);

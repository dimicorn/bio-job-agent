import json
from difflib import SequenceMatcher

from app.models.posting import Posting
from app.pipeline.normalize import canonical_url, key, location_key


def store_job(conn, job: Posting, source_id: str, employer_id: int, raw_id: int, now: str):
    origin = conn.execute(
        "SELECT job_id FROM job_sources WHERE source_id=? AND external_id=?",
        (source_id, job.external_id),
    ).fetchone()
    job_id = origin[0] if origin else None
    if job_id is None:
        row = conn.execute(
            "SELECT id FROM jobs WHERE employer_id=? AND canonical_url=?",
            (employer_id, canonical_url(job.source_url)),
        ).fetchone()
        job_id = row[0] if row else None
    if job_id is None:
        matches = conn.execute(
            "SELECT id, description FROM jobs WHERE employer_id=? AND title_key=? AND location_key=?",
            (employer_id, key(job.title), location_key(job.location)),
        )
        for match in matches:
            if (
                len(job.description) >= 80
                and SequenceMatcher(
                    None, key(job.description), key(match["description"]), autojunk=True
                ).ratio()
                >= 0.92
            ):
                job_id = match["id"]
                break
    payload = job.model_dump_json()
    if job_id is None:
        cursor = conn.execute(
            """INSERT INTO jobs(employer_id,title_key,location_key,canonical_url,
            payload,description,discovered_at,last_verified_at) VALUES (?,?,?,?,?,?,?,?)""",
            (
                employer_id,
                key(job.title),
                location_key(job.location),
                canonical_url(job.source_url),
                payload,
                job.description,
                now,
                now,
            ),
        )
        job_id = cursor.lastrowid
    elif origin:
        # Refresh the canonical facts only from its original source; keep other origins intact.
        current = json.loads(
            conn.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        )
        if (
            canonical_url(current["source_url"]) == canonical_url(job.source_url)
            or current["external_id"] == job.external_id
        ):
            conn.execute(
                """UPDATE jobs SET title_key=?,location_key=?,canonical_url=?,payload=?,
                description=?,last_verified_at=? WHERE id=?""",
                (
                    key(job.title),
                    location_key(job.location),
                    canonical_url(job.source_url),
                    payload,
                    job.description,
                    now,
                    job_id,
                ),
            )
        else:
            conn.execute("UPDATE jobs SET last_verified_at=? WHERE id=?", (now, job_id))
    else:
        conn.execute("UPDATE jobs SET last_verified_at=? WHERE id=?", (now, job_id))
    conn.execute(
        """INSERT INTO job_sources(job_id,source_id,external_id,source_url,apply_url,
        first_seen_at,last_seen_at,raw_posting_id) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,external_id) DO UPDATE SET source_url=excluded.source_url,
        apply_url=excluded.apply_url,last_seen_at=excluded.last_seen_at,raw_posting_id=excluded.raw_posting_id,eligible=1""",
        (job_id, source_id, job.external_id, job.source_url, job.apply_url, now, now, raw_id),
    )
    return job_id

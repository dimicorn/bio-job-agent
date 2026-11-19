import re
from difflib import SequenceMatcher

from app.models.posting import Posting
from app.pipeline.normalize import canonical_url, key, location_key


def _requisitions(job):
    # Only explicit labels count; arbitrary numbers in a description are not IDs.
    return set(
        re.findall(
            r"\b(?:requisition|req|job)\s*(?:id|number|no)?\s*[:#-]\s*([a-z0-9-]+)",
            (job.title + " " + job.description).lower(),
        )
    )


def _probable(a, b):
    ra, rb = _requisitions(a), _requisitions(b)
    if ra and rb and ra.isdisjoint(rb):
        return False
    # Explicit team labels are evidence of separate openings, even with similar boilerplate.
    team_pattern = r"(?im)^\s*(?:team|department)\s*:\s*([^\n.;]+)"
    teams_a = {key(x) for x in re.findall(team_pattern, a.description)}
    teams_b = {key(x) for x in re.findall(team_pattern, b.description)}
    if teams_a and teams_b and teams_a.isdisjoint(teams_b):
        return False
    ta, tb = key(a.title), key(b.title)
    # Require the same substantive title words; allow small connective wording changes.
    stop = {"the", "a", "an", "of", "and", "for", "in", "at"}
    wa, wb = set(ta.split()) - stop, set(tb.split()) - stop
    if not wa or not wb or (wa != wb and SequenceMatcher(None, ta, tb).ratio() < 0.9):
        return False
    la, lb = location_key(a.location), location_key(b.location)
    if not la or not lb or not (set(la.split()) & set(lb.split())):
        return False
    if len(a.description) < 80 or len(b.description) < 80:
        return False
    return (
        SequenceMatcher(None, key(a.description), key(b.description), autojunk=True).ratio() >= 0.85
    )


def group_stored_jobs(conn, rows):
    """Group eligible rows without moving or deleting any job history.

    Complete-link matching prevents a third origin from bridging an explicit separation
    or joining different requisitions through a similar middle posting.
    """
    separated = {
        tuple(row) for row in conn.execute("SELECT origin_a, origin_b FROM duplicate_separations")
    }
    groups = []
    for row in sorted(rows, key=lambda item: (not bool(item.get("application_state")), item["id"])):
        posting = Posting.model_validate({k: row[k] for k in Posting.model_fields})
        for group in groups:
            if row.get("application_state") and any(
                other.get("application_state") for other in group
            ):
                continue
            compatible = True
            for other in group:
                other_posting = Posting.model_validate({k: other[k] for k in Posting.model_fields})
                pairs = (
                    tuple(sorted((a["id"], b["id"])))
                    for a in row["origins"]
                    for b in other["origins"]
                )
                if (
                    row["employer_id"] != other["employer_id"]
                    or any(pair in separated for pair in pairs)
                    or not (
                        canonical_url(posting.source_url) == canonical_url(other_posting.source_url)
                        or _probable(posting, other_posting)
                    )
                ):
                    compatible = False
                    break
            if compatible:
                group.append(row)
                break
        else:
            groups.append([row])
    result = []
    for group in groups:
        representative = dict(group[0])
        representative["grouped_job_ids"] = [item["id"] for item in group]
        representative["origins"] = [origin for item in group for origin in item["origins"]]
        representative["locations"] = list(
            dict.fromkeys(
                location
                for item in group
                for location in (item["locations"] or [item["location"]])
                if location
            )
        )
        result.append(representative)
    return result


def _update_canonical(conn, job_id, job, now):
    conn.execute(
        """UPDATE jobs SET title_key=?,location_key=?,canonical_url=?,payload=?,
        description=?,last_verified_at=? WHERE id=?""",
        (
            key(job.title),
            location_key(job.location),
            canonical_url(job.source_url),
            job.model_dump_json(),
            job.description,
            now,
            job_id,
        ),
    )


def handover(conn, job_id):
    """Move ownership only when the current origin has expired, to a known present posting."""
    owner = conn.execute("SELECT owner_origin_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    if owner and conn.execute("SELECT present FROM job_sources WHERE id=?", (owner,)).fetchone()[0]:
        return
    replacement = conn.execute(
        """SELECT id,posting_payload,last_seen_at FROM job_sources
        WHERE job_id=? AND present=1 AND eligible=1 AND posting_payload IS NOT NULL
        ORDER BY id LIMIT 1""",
        (job_id,),
    ).fetchone()
    if replacement:
        _update_canonical(
            conn,
            job_id,
            Posting.model_validate_json(replacement["posting_payload"]),
            replacement["last_seen_at"],
        )
        conn.execute("UPDATE jobs SET owner_origin_id=? WHERE id=?", (replacement["id"], job_id))


def store_job(conn, job: Posting, source_id: str, employer_id: int, raw_id: int, now: str):
    origin = conn.execute(
        "SELECT id,job_id FROM job_sources WHERE source_id=? AND external_id=?",
        (source_id, job.external_id),
    ).fetchone()
    job_id = origin["job_id"] if origin else None
    if job_id is None:
        candidates = conn.execute(
            """SELECT * FROM jobs WHERE employer_id=? AND NOT EXISTS
            (SELECT 1 FROM applications WHERE job_id=jobs.id) ORDER BY id""",
            (employer_id,),
        )
        for row in candidates:
            previous = Posting.model_validate_json(row["payload"])
            if canonical_url(previous.source_url) == canonical_url(job.source_url) or _probable(
                previous, job
            ):
                job_id = row["id"]
                break
    if job_id is None:
        job_id = conn.execute(
            """INSERT INTO jobs(employer_id,title_key,location_key,canonical_url,
            payload,description,discovered_at,last_verified_at) VALUES (?,?,?,?,?,?,?,?)""",
            (
                employer_id,
                key(job.title),
                location_key(job.location),
                canonical_url(job.source_url),
                job.model_dump_json(),
                job.description,
                now,
                now,
            ),
        ).lastrowid
    conn.execute(
        """INSERT INTO job_sources(job_id,source_id,external_id,source_url,apply_url,
        first_seen_at,last_seen_at,raw_posting_id,first_seen_reliable,posting_payload)
        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,external_id) DO UPDATE SET
        source_url=excluded.source_url,apply_url=excluded.apply_url,last_seen_at=excluded.last_seen_at,
        raw_posting_id=excluded.raw_posting_id,eligible=1,missing_count=0,present=1,
        posting_payload=excluded.posting_payload""",
        (
            job_id,
            source_id,
            job.external_id,
            job.source_url,
            job.apply_url,
            now,
            now,
            raw_id,
            int(
                bool(
                    conn.execute(
                        "SELECT last_success_at FROM sources WHERE id=?", (source_id,)
                    ).fetchone()[0]
                )
            ),
            job.model_dump_json(),
        ),
    )
    origin_id = conn.execute(
        "SELECT id FROM job_sources WHERE source_id=? AND external_id=?",
        (source_id, job.external_id),
    ).fetchone()[0]
    owner = conn.execute("SELECT owner_origin_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    if owner is None:
        conn.execute("UPDATE jobs SET owner_origin_id=? WHERE id=?", (origin_id, job_id))
        owner = origin_id
    if owner == origin_id:
        _update_canonical(conn, job_id, job, now)
    else:
        conn.execute("UPDATE jobs SET last_verified_at=? WHERE id=?", (now, job_id))
        handover(conn, job_id)
    conn.execute("UPDATE jobs SET status='open' WHERE id=?", (job_id,))
    return job_id


def unmerge(conn, job_id, origin_id):
    origin = conn.execute("SELECT * FROM job_sources WHERE id=?", (origin_id,)).fetchone()
    if origin and origin["job_id"] != job_id:
        anchor_origins = conn.execute(
            "SELECT id FROM job_sources WHERE job_id=?", (job_id,)
        ).fetchall()
        other_origins = conn.execute(
            "SELECT id FROM job_sources WHERE job_id=?", (origin["job_id"],)
        ).fetchall()
        anchor = conn.execute(
            "SELECT employer_id,payload FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        other = conn.execute(
            "SELECT employer_id,payload FROM jobs WHERE id=?", (origin["job_id"],)
        ).fetchone()
        if not anchor or anchor["employer_id"] != other["employer_id"]:
            raise ValueError("Origin does not belong to this probable duplicate group")
        a = Posting.model_validate_json(anchor["payload"])
        b = Posting.model_validate_json(other["payload"])
        if canonical_url(a.source_url) != canonical_url(b.source_url) and not _probable(a, b):
            raise ValueError("Origin does not belong to this probable duplicate group")
        for left in anchor_origins:
            for right in other_origins:
                pair = sorted((left["id"], right["id"]))
                conn.execute("INSERT OR IGNORE INTO duplicate_separations VALUES (?,?)", pair)
        conn.commit()
        return origin["job_id"]

    others = conn.execute(
        "SELECT id FROM job_sources WHERE job_id=? AND id!=?", (job_id, origin_id)
    ).fetchall()
    if not origin or not others:
        raise ValueError("Origin must belong to a job with multiple origins")
    if not origin["posting_payload"]:
        raise ValueError("Refresh this source before unmerging its historical origin")
    if (
        conn.execute("SELECT owner_origin_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        == origin_id
    ):
        raise ValueError(
            "Unmerge a non-canonical origin to preserve the original job and application"
        )
    job = Posting.model_validate_json(origin["posting_payload"])
    old = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    new_id = conn.execute(
        """INSERT INTO jobs(employer_id,title_key,location_key,canonical_url,payload,
        description,discovered_at,last_verified_at,status,owner_origin_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            old["employer_id"],
            key(job.title),
            location_key(job.location),
            canonical_url(job.source_url),
            job.model_dump_json(),
            job.description,
            origin["first_seen_at"],
            origin["last_seen_at"],
            "open" if origin["present"] else "closed",
            origin_id,
        ),
    ).lastrowid
    conn.execute("UPDATE job_sources SET job_id=? WHERE id=?", (new_id, origin_id))
    for other in others:
        a, b = sorted((origin_id, other["id"]))
        conn.execute("INSERT OR IGNORE INTO duplicate_separations VALUES (?,?)", (a, b))
    conn.commit()
    return new_id

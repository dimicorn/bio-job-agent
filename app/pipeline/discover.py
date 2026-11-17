import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx

from app.candidate.profile import import_profile
from app.pipeline.dedupe import store_job
from app.pipeline.filter import exclusion
from app.sources.base import PublicClient, RateLimited
from app.sources.registry import ADAPTERS

LOG = logging.getLogger(__name__)


def utcnow():
    return datetime.now(UTC).isoformat()


def register_source(conn, source):
    conn.execute(
        """INSERT INTO employers(name,industry,review_url,industry_verified) VALUES (?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET industry=excluded.industry,review_url=excluded.review_url,
        industry_verified=excluded.industry_verified""",
        (source.employer, source.industry, str(source.review_url), source.industry_verified),
    )
    employer_id = conn.execute(
        "SELECT id FROM employers WHERE name=?", (source.employer,)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO sources(id,employer_id,adapter,board,config) VALUES (?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET employer_id=excluded.employer_id,adapter=excluded.adapter,
        board=excluded.board,config=excluded.config""",
        (source.id, employer_id, source.adapter, source.board, source.model_dump_json()),
    )
    return employer_id


def discover(
    conn, profile, sources, *, timeout=20, user_agent="BioJobAgent/0.1", transport=None, pause=None
):
    results = []
    profile_id = import_profile(conn, profile)
    with httpx.Client(timeout=timeout, transport=transport) as http:
        client = PublicClient(http, user_agent, **({"pause": pause} if pause else {}))
        for source in sources:
            employer_id = register_source(conn, source)
            if not source.enabled:
                continue
            now = utcnow()
            next_allowed = conn.execute(
                "SELECT next_allowed_at FROM sources WHERE id=?", (source.id,)
            ).fetchone()[0]
            if next_allowed and datetime.fromisoformat(next_allowed) > datetime.now(UTC):
                results.append(
                    {"source": source.id, "status": "cooldown", "next_allowed_at": next_allowed}
                )
                continue
            # Commit reservation before networking, so repeated refreshes respect the interval.
            # BEGIN IMMEDIATE serializes concurrent refresh reservations.
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            next_allowed = conn.execute(
                "SELECT next_allowed_at FROM sources WHERE id=?", (source.id,)
            ).fetchone()[0]
            if next_allowed and datetime.fromisoformat(next_allowed) > datetime.now(UTC):
                conn.commit()
                results.append({"source": source.id, "status": "cooldown"})
                continue
            next_allowed = (
                datetime.now(UTC) + timedelta(seconds=source.min_interval_seconds)
            ).isoformat()
            conn.execute(
                "UPDATE sources SET next_allowed_at=? WHERE id=?", (next_allowed, source.id)
            )
            run_id = conn.execute(
                """INSERT INTO sync_runs(source_id,profile_id,started_at,status)
                VALUES (?,?,?,'running')""",
                (source.id, profile_id, now),
            ).lastrowid
            conn.commit()
            counts = {"fetched": 0, "accepted": 0, "rejected": 0, "malformed": 0}
            error = None
            seen = set()
            try:
                if not source.industry_verified:
                    raise ValueError("Employer industry has not been reviewed")
                adapter = ADAPTERS[source.adapter](source, client)
                raws = adapter.fetch()
                counts["fetched"] = len(raws)
                for raw in raws:
                    payload = json.dumps(raw, sort_keys=True)
                    digest = hashlib.sha256(payload.encode()).hexdigest()
                    external_id = (
                        str(raw.get("id", f"malformed:{digest}"))
                        if isinstance(raw, dict)
                        else f"malformed:{digest}"
                    )
                    job = None
                    try:
                        job = adapter.parse(raw)
                        seen.add(job.external_id)
                        reason = exclusion(job, source, profile)
                    except (ValueError, KeyError, TypeError, AttributeError) as exc:
                        reason = "malformed"
                        LOG.warning(
                            json.dumps(
                                {
                                    "event": "malformed_posting",
                                    "source": source.id,
                                    "external_id": external_id,
                                    "error": type(exc).__name__,
                                }
                            )
                        )
                    conn.execute(
                        """INSERT INTO raw_postings(source_id,external_id,content_hash,payload,
                        fetched_at,disposition) VALUES (?,?,?,?,?,?) ON CONFLICT(source_id,external_id,
                        content_hash) DO UPDATE SET fetched_at=excluded.fetched_at,disposition=excluded.disposition""",
                        (source.id, external_id, digest, payload, now, reason or "accepted"),
                    )
                    raw_id = conn.execute(
                        "SELECT id FROM raw_postings WHERE source_id=? AND external_id=? AND content_hash=?",
                        (source.id, external_id, digest),
                    ).fetchone()[0]
                    if reason:
                        counts["malformed" if reason == "malformed" else "rejected"] += 1
                        # A known source role changing outside the filter stops appearing immediately.
                        conn.execute(
                            "UPDATE job_sources SET eligible=0,raw_posting_id=? WHERE source_id=? AND external_id=?",
                            (raw_id, source.id, external_id),
                        )
                    else:
                        store_job(conn, job, source.id, employer_id, raw_id, now)
                        counts["accepted"] += 1
                status = (
                    "partial"
                    if counts["malformed"] or not getattr(adapter, "complete", True)
                    else "ok"
                )
                if status == "ok":
                    conn.execute(
                        "UPDATE sources SET last_success_at=?,last_count=?,consecutive_failures=0 WHERE id=?",
                        (now, counts["fetched"], source.id),
                    )
                    if source.adapter != "site":
                        close_missing(conn, source.id, seen)
                else:
                    error = "Incomplete snapshot; no vacancies expired"
            except Exception as exc:  # noqa: BLE001 - isolate arbitrary adapter failures at this boundary.
                conn.rollback()
                counts["accepted"] = counts["rejected"] = counts["malformed"] = 0
                status = "error"
                # HTTP errors can contain URLs; no credentials or response bodies are logged.
                error = f"{type(exc).__name__}: {exc}"[:1000]
                if isinstance(exc, RateLimited):
                    next_allowed = (datetime.now(UTC) + timedelta(seconds=exc.seconds)).isoformat()
                LOG.error(
                    json.dumps({"event": "source_failed", "source": source.id, "error": error})
                )
            conn.execute(
                """UPDATE sync_runs SET finished_at=?,status=?,fetched=?,accepted=?,rejected=?,
                malformed=?,error=? WHERE id=?""",
                (utcnow(), status, *counts.values(), error, run_id),
            )
            conn.execute(
                "UPDATE sources SET health=?,last_checked_at=?,next_allowed_at=? WHERE id=?",
                (status, now, next_allowed, source.id),
            )
            if error:
                conn.execute(
                    "UPDATE sources SET last_error=?,last_error_at=?,consecutive_failures=consecutive_failures+1 WHERE id=?",
                    (error, now, source.id),
                )
            conn.commit()
            results.append({"source": source.id, "status": status, **counts, "error": error})
    return results


def close_missing(conn, source_id, seen):
    """Two complete absences retire an origin; another present origin keeps the job open.

    Called inside the source snapshot transaction. Applications are separate and untouched.
    """
    for row in conn.execute(
        "SELECT id,external_id FROM job_sources WHERE source_id=?", (source_id,)
    ):
        if row["external_id"] in seen:
            conn.execute(
                "UPDATE job_sources SET missing_count=0,present=1 WHERE id=?", (row["id"],)
            )
        else:
            conn.execute(
                "UPDATE job_sources SET missing_count=missing_count+1,present=CASE WHEN missing_count+1>=2 THEN 0 ELSE present END WHERE id=?",
                (row["id"],),
            )
    conn.execute(
        """UPDATE jobs SET status='closed' WHERE id IN
        (SELECT job_id FROM job_sources WHERE source_id=?)
        AND NOT EXISTS (SELECT 1 FROM job_sources js WHERE js.job_id=jobs.id AND js.present=1)""",
        (source_id,),
    )
    conn.execute(
        """UPDATE jobs SET status='open' WHERE id IN
        (SELECT job_id FROM job_sources WHERE source_id=? AND present=1)""",
        (source_id,),
    )

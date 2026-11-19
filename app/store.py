import csv
import io
import json
from datetime import UTC, datetime, timedelta

from app.applications import company_cooldown
from app.models.posting import Posting
from app.pipeline.filter import exclusion, geography


def parse_date(value):
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return None


def list_jobs(conn, profile, sources, cfg=None, now=None, *, prospects_only=False):
    current_sources = {s.id: s for s in sources if s.enabled}
    reviews = {row["job_id"]: row["decision"] for row in conn.execute("SELECT * FROM job_reviews")}
    applications = {r["job_id"]: r["state"] for r in conn.execute("SELECT * FROM applications")}
    result = []
    for row in conn.execute("SELECT * FROM jobs ORDER BY discovered_at DESC,id DESC"):
        job = Posting.model_validate_json(row["payload"])
        origins = [
            dict(x)
            for x in conn.execute(
                """SELECT js.*,s.adapter,s.employer_id FROM job_sources js
            JOIN sources s ON s.id=js.source_id WHERE js.job_id=? ORDER BY js.first_seen_at,js.id""",
                (row["id"],),
            )
        ]
        eligible = [
            x
            for x in origins
            if x["eligible"]
            and x["present"]
            and x["source_id"] in current_sources
            and not exclusion(job, current_sources[x["source_id"]], profile)
        ]
        retained = not prospects_only and (
            reviews.get(row["id"]) == "saved" or row["id"] in applications
        )
        if (not eligible or row["status"] != "open") and not retained:
            continue
        posted = parse_date(job.posted_at)
        reliable = [parse_date(x["first_seen_at"]) for x in eligible if x["first_seen_reliable"]]
        # An initial snapshot cannot establish when an unknown-date job appeared.
        first_seen = (
            min((x for x in reliable if x), default=None)
            if all(x["first_seen_reliable"] for x in origins)
            else None
        )
        date = posted or first_seen
        days = cfg.search.max_posting_age_days if cfg else 0
        current = now or datetime.now(UTC)
        stale = bool(days and date and date < current.astimezone(UTC) - timedelta(days=days))
        if stale and not retained:
            continue
        label = (
            f"posted {job.posted_at}"
            if posted
            else f"first seen {first_seen.isoformat()}"
            if first_seen
            else "posting date unknown"
        )
        if stale:
            label += " [stale]"
        if row["status"] != "open":
            label += " [closed]"
        cooldown = (
            company_cooldown(conn, job.employer, cfg.apply.company_cooldown_days, now=current)
            if cfg
            else None
        )
        result.append(
            {
                "id": row["id"],
                **job.model_dump(),
                "date_label": label,
                "company_cooldown_until": cooldown.isoformat() if cooldown else None,
                "discovered_at": row["discovered_at"],
                "last_verified_at": row["last_verified_at"],
                "status": row["status"],
                "review": reviews.get(row["id"], "new"),
                "application_state": applications.get(row["id"]),
                "geography": geography(job, profile),
                "origins": origins,
            }
        )
    return result


def review_job(conn, job_id: int, decision: str) -> None:
    if decision not in ("saved", "rejected", "later"):
        raise ValueError("Unknown review decision")
    conn.execute(
        """INSERT INTO job_reviews(job_id,decision) VALUES (?,?)
        ON CONFLICT(job_id) DO UPDATE SET decision=excluded.decision,
        decided_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')""",
        (job_id, decision),
    )
    if decision == "saved":
        conn.execute(
            "INSERT OR IGNORE INTO applications(job_id,state) VALUES (?, 'saved')", (job_id,)
        )
    # Each keypress survives quitting/interruption later in the review session.
    conn.commit()


def override_score(conn, job_id: int, score: int, recommendation: str, reason: str) -> None:
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("Override score must be an integer from 0 to 100")
    if recommendation not in ("apply", "maybe", "skip") or not reason.strip():
        raise ValueError("Override needs apply/maybe/skip and a reason")
    conn.execute(
        """INSERT INTO score_overrides(job_id,score,recommendation,reason) VALUES (?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET score=excluded.score,
        recommendation=excluded.recommendation,reason=excluded.reason,
        updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')""",
        (job_id, score, recommendation, reason.strip()),
    )
    conn.commit()


CSV_FIELDS = [
    "id",
    "title",
    "employer",
    "location",
    "geography",
    "score",
    "remote_policy",
    "employment_type",
    "posted_at",
    "discovered_at",
    "last_verified_at",
    "review",
    "application_state",
    "apply_url",
    "source_url",
    "provenance",
]


def safe_csv_cell(value):
    if value is None:
        return ""
    value = str(value)
    # Spreadsheet formula injection can be embedded in untrusted titles/descriptions.
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r"))
        else value
    )


def export_csv(jobs):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for job in jobs:
        row = {k: job.get(k) for k in CSV_FIELDS}
        row["provenance"] = json.dumps(
            [
                {"source": x["source_id"], "external_id": x["external_id"], "url": x["source_url"]}
                for x in job["origins"]
            ]
        )
        writer.writerow({k: safe_csv_cell(v) for k, v in row.items()})
    return output.getvalue()

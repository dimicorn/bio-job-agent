import csv
import io
import json
from datetime import UTC, datetime, timedelta

from app.applications import company_cooldown
from app.models.posting import Posting
from app.pipeline.dedupe import group_stored_jobs
from app.pipeline.filter import board, exclusion, exclusions, geography
from app.pipeline.normalize import key


def parse_date(value):
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return None


NOTES = {
    "unverified_industry": "industry: {employer} is not on the reviewed watchlist, so its "
    "industry is unchecked",
    "academic_employer": "industry: {employer} looks academic, NHS or a hospital",
    "excluded_role": "role: the title is an excluded role type (postdoc, academic, sales)",
    "seniority_or_role": "seniority: the title does not match your target_titles",
    "geography": "geography: location '{location}' is outside your regions or unknown",
}


def link_notes(job, profile, cfg, *, industry_verified, now=None):
    """Plain reasons the discovery filters would have excluded a job added by link."""
    values = {"employer": job.employer, "location": job.location or "unknown"}
    notes = [
        NOTES[reason].format(**values)
        for reason in exclusions(job, profile, industry_verified=industry_verified)
    ]
    days = cfg.search.max_posting_age_days if cfg else 0
    posted = parse_date(job.posted_at)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if days and posted and posted < current - timedelta(days=days):
        notes.append(f"freshness: posted {job.posted_at[:10]}, older than {days} days")
    return notes


def list_jobs(conn, profile, sources, cfg=None, now=None, *, prospects_only=False):
    current_sources = {s.id: s for s in sources if s.enabled}
    verified_employers = {
        key(s.employer) for s in sources if s.enabled and s.industry_verified and not board(s)
    }
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
        locations = list(
            dict.fromkeys(
                Posting.model_validate_json(x["posting_payload"]).location
                for x in origins
                if x["posting_payload"]
                and Posting.model_validate_json(x["posting_payload"]).location
            )
        )
        eligible = [
            x
            for x in origins
            if x["eligible"]
            and x["present"]
            and x["source_id"] in current_sources
            and not exclusion(job, current_sources[x["source_id"]], profile)
            and not exclusion(
                Posting.model_validate_json(x["posting_payload"]) if x["posting_payload"] else job,
                current_sources[x["source_id"]],
                profile,
            )
        ]
        # A job the person added by link stays visible whatever the filters say.
        linked = [x for x in origins if x["adapter"] == "link" and x["present"]]
        retained = not prospects_only and (
            reviews.get(row["id"]) == "saved" or row["id"] in applications
        )
        if (not (eligible or linked) or row["status"] != "open") and not retained:
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
        if stale and not retained and not linked:
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
                "employer_id": row["employer_id"],
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
                "locations": locations,
                "added_by_link": bool(linked),
                "board_sources": list(
                    dict.fromkeys(x["source_id"] for x in origins if x["adapter"] == "jsonfeed")
                ),
                "employer_reviewed": key(job.employer) in verified_employers,
                "filter_notes": link_notes(
                    job,
                    profile,
                    cfg,
                    industry_verified=key(job.employer) in verified_employers,
                    now=current,
                )
                if linked and not eligible
                else [],
            }
        )
    return group_stored_jobs(conn, result)


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


def override_score(
    conn, job_id: int, score: int, recommendation: str, reason: str, *, attended: bool = False
) -> None:
    """Save the person's score; attended marks an "apply anyway" from review (never auto mode)."""
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("Override score must be an integer from 0 to 100")
    if recommendation not in ("apply", "maybe", "skip") or not reason.strip():
        raise ValueError("Override needs apply/maybe/skip and a reason")
    conn.execute(
        """INSERT INTO score_overrides(job_id,score,recommendation,reason,attended)
        VALUES (?,?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET score=excluded.score,
        recommendation=excluded.recommendation,reason=excluded.reason,attended=excluded.attended,
        updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')""",
        (job_id, score, recommendation, reason.strip(), int(attended)),
    )
    conn.commit()


CSV_FIELDS = [
    "id",
    "title",
    "employer",
    "location",
    "geography",
    "score",
    "keyword_match",
    "keywords_present",
    "keywords_missing",
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
        match = job.get("keywords")
        if match:
            row["keyword_match"] = match["score"]
            row["keywords_present"] = "; ".join(match["present"])
            row["keywords_missing"] = "; ".join(match["missing"])
        row["provenance"] = json.dumps(
            [
                {"source": x["source_id"], "external_id": x["external_id"], "url": x["source_url"]}
                for x in job["origins"]
            ]
        )
        writer.writerow({k: safe_csv_cell(v) for k, v in row.items()})
    return output.getvalue()

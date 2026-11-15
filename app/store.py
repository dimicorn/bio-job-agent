import csv
import io
import json

from app.models.posting import Posting
from app.pipeline.filter import exclusion, geography


def list_jobs(conn, profile, sources):
    current_sources = {s.id: s for s in sources if s.enabled}
    reviews = {row["job_id"]: row["decision"] for row in conn.execute("SELECT * FROM job_reviews")}
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
            and x["source_id"] in current_sources
            and not exclusion(job, current_sources[x["source_id"]], profile)
        ]
        if not eligible:
            continue
        result.append(
            {
                "id": row["id"],
                **job.model_dump(),
                "discovered_at": row["discovered_at"],
                "last_verified_at": row["last_verified_at"],
                "status": row["status"],
                "review": reviews.get(row["id"], "new"),
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
    # Each keypress survives quitting/interruption later in the review session.
    conn.commit()


CSV_FIELDS = [
    "id",
    "title",
    "employer",
    "location",
    "geography",
    "remote_policy",
    "employment_type",
    "posted_at",
    "discovered_at",
    "last_verified_at",
    "review",
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

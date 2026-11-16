"""Approval integrity, execution gates and explicit manual confirmation."""

import json
from datetime import datetime
from pathlib import Path

import yaml

from app import answers, materials
from app.candidate.profile import import_profile
from app.resume import digest, require_reviewed


def approve(cfg, conn, profile, row, *, approved_this_run=0, now=None):
    check_gates(cfg, conn, row, used=approved_this_run, now=now)
    paths, ids = validate_materials(cfg, conn, profile, row)
    if conn.execute(
        "SELECT 1 FROM application_attempts WHERE job_id=? AND "
        "(submission_possible=1 OR status='started')",
        (row["id"],),
    ).fetchone():
        raise ValueError("A prior attempt may have submitted; reconcile manually, never retry")
    conn.execute(
        """INSERT INTO application_approvals(job_id,material_ids,answers_hash,answers_content) VALUES (?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET material_ids=excluded.material_ids,
        answers_hash=excluded.answers_hash,answers_content=excluded.answers_content,approved_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')""",
        (
            row["id"],
            json.dumps(ids),
            digest(answers.load_answers(cfg)),
            json.dumps(answers.load_answers(cfg)),
        ),
    )
    materials.state(conn, row["id"], "approved")
    row["application_state"] = "approved"
    return paths


def check_gates(cfg, conn, row, *, used=0, now=None, exclude_attempt=None):
    now = now or datetime.now().astimezone()
    gate = cfg.apply
    if row.get("score") is None or row["score"] < max(gate.min_score, cfg.analyst.min_score):
        raise ValueError("Application score is below the apply/analyst threshold")
    current = conn.execute(
        "SELECT state,submitted_at FROM applications WHERE job_id=?", (row["id"],)
    ).fetchone()
    if current and (current[0] in ("submitted", "withdrawn", "unknown_result") or current[1]):
        raise ValueError(
            "Submitted, withdrawn or uncertain applications cannot be approved/applied"
        )
    if not gate.active_hours[0] <= now.hour < gate.active_hours[1]:
        raise ValueError("Outside apply.active_hours (local time)")
    if used >= gate.max_per_run:
        raise ValueError("apply.max_per_run reached")
    # Reservations count too: concurrent processes cannot spend the same daily slot.
    count = conn.execute(
        """SELECT count(*) FROM (
        SELECT job_id FROM applications WHERE submitted_at IS NOT NULL
          AND date(submitted_at,'localtime')=date(?,'localtime')
        UNION SELECT job_id FROM application_attempts WHERE id != ?
          AND date(started_at,'localtime')=date(?,'localtime'))""",
        (now.isoformat(), exclude_attempt or -1, now.isoformat()),
    ).fetchone()[0]
    if count >= gate.daily_limit:
        raise ValueError("apply.daily_limit reached")


def validate_materials(cfg, conn, profile, row):
    require_reviewed(cfg, profile)
    version = import_profile(conn, profile)
    paths, ids = [], {}
    for kind in ("letter", "resume", "answers"):
        current = materials.latest(conn, row["id"], kind)
        if not current:
            raise ValueError(f"Prepare {kind} first")
        if current["profile_version"] != version or current["job_hash"] != materials.job_hash(row):
            raise ValueError(f"{kind} is stale; prepare it again")
        if json.loads(current["warnings"]):
            raise ValueError(f"{kind} has unresolved warnings; inspect review and correct it")
        path = Path(current["path"])
        if not path.exists() or path.read_text(encoding="utf-8") != current["content"]:
            raise ValueError(f"{kind} file changed; import/edit or rebuild it before approval")
        ids[kind] = current["id"]
        paths.append(str(path))
        if kind == "resume":
            if not current["pdf_path"] or not Path(current["pdf_path"]).exists():
                raise ValueError("Build the resume PDF first")
            if current["pdf_hash"] != digest(Path(current["pdf_path"]).read_bytes().hex()):
                raise ValueError("Resume PDF changed after ATS check; rebuild it")
            if current["ats_warnings"] is None or json.loads(current["ats_warnings"]):
                raise ValueError("Resume PDF has unresolved ATS warnings")
            paths.append(current["pdf_path"])
    current_answers = materials.latest(conn, row["id"], "answers")
    if yaml.safe_load(current_answers["content"]) != {"items": answers.plan(cfg, conn, row)}:
        raise ValueError("Screening answers changed; run answers again")
    return paths, ids


def confirm_submitted(conn, row, confirmation):
    current = conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()
    if not current or current[0] not in (
        "approved",
        "manual_required",
        "unknown_result",
        "submitting",
    ):
        raise ValueError("Approve and complete the manual application first")
    if confirmation.strip() != "submitted":
        raise ValueError("Type submitted only after completing the application yourself")
    conn.execute(
        "INSERT INTO application_events(job_id,action,detail) VALUES (?,?,?)",
        (row["id"], "manual_confirmation", json.dumps({"evidence": "user typed submitted"})),
    )
    materials.state(conn, row["id"], "submitted")
    row["application_state"] = "submitted"

"""Manual handoff and explicit user-confirmed application tracking."""

import json
from datetime import datetime
from pathlib import Path

import yaml

from app import answers, materials
from app.candidate.profile import import_profile
from app.resume import digest, require_reviewed


def approve(cfg, conn, profile, row, *, approved_this_run=0, now=None):
    require_reviewed(cfg, profile)
    now = now or datetime.now().astimezone()
    gate = cfg.apply
    if row.get("score") is None or row["score"] < max(gate.min_score, cfg.analyst.min_score):
        raise ValueError("Application score is below the apply/analyst threshold")
    if row.get("application_state") in ("submitted", "withdrawn"):
        raise ValueError("Submitted or withdrawn applications cannot be approved")
    if not gate.active_hours[0] <= now.hour < gate.active_hours[1]:
        raise ValueError("Outside apply.active_hours (local time)")
    if approved_this_run >= gate.max_per_run:
        raise ValueError("apply.max_per_run reached")
    count = conn.execute(
        """SELECT count(*) FROM applications
        WHERE submitted_at IS NOT NULL AND date(submitted_at,'localtime')=date(?,'localtime')""",
        (now.isoformat(),),
    ).fetchone()[0]
    if count >= gate.daily_limit:
        raise ValueError("apply.daily_limit reached")
    version = import_profile(conn, profile)
    paths = []
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
    materials.state(conn, row["id"], "approved")
    row["application_state"] = "approved"
    return paths


def confirm_submitted(conn, row, confirmation):
    current = conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()
    if not current or current[0] != "approved":
        raise ValueError("Approve and complete the manual application first")
    if confirmation.strip() != "submitted":
        raise ValueError("Type submitted only after completing the application yourself")
    materials.state(conn, row["id"], "submitted")
    row["application_state"] = "submitted"

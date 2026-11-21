"""One-step preparation: create only the missing or stale materials for a job.

Reuses the single-piece preparation functions. Current materials are left untouched,
and a material file the user edited is never replaced: it must be imported first.
Nothing here approves or submits an application.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app import answers, materials
from app.candidate.profile import import_profile
from app.console import log, warn
from app.resume import digest, require_reviewed

TERMINAL = ("submitted", "unknown_result", "withdrawn")


@dataclass
class Prepared:
    created: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unanswered: list[dict] = field(default_factory=list)


def edited_message(row, kind):
    return f"{kind} file changed; run bio-jobs import {row['id']} --kind {kind} before approval"


def status(conn, row, kind, version):
    """missing, edited, stale or current; an edit always wins so it is never discarded."""
    item = materials.latest(conn, row["id"], kind)
    if not item:
        return "missing", None
    path = Path(item["path"])
    if not path.exists():
        return "missing", item
    if path.read_text(encoding="utf-8") != item["content"]:
        return "edited", item
    if item["profile_version"] != version or item["job_hash"] != materials.job_hash(row):
        return "stale", item
    return "current", item


def pdf_current(item):
    if not item or not item["pdf_path"] or item["ats_warnings"] is None:
        return False
    path = Path(item["pdf_path"])
    return path.exists() and item["pdf_hash"] == digest(path.read_bytes().hex())


def prepare_materials(cfg, conn, profile, row, *, output=log):
    """Create a missing or stale letter, resume and checked resume PDF."""
    require_reviewed(cfg, profile)
    current = conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()
    if current and current[0] in TERMINAL:
        raise ValueError(f"Job {row['id']} is {current[0]}; nothing to prepare")
    version = import_profile(conn, profile)
    result = Prepared()

    found, _ = status(conn, row, "letter", version)
    if found == "edited":
        result.problems.append(edited_message(row, "letter"))
    elif found == "current":
        result.current.append("letter")
    else:
        output(f"Writing {'a new' if found == 'missing' else 'an updated'} cover letter...")
        try:
            if materials.write_letters(cfg, conn, profile, [row], force=True):
                result.created.append("letter")
            else:
                result.problems.append(
                    "Letter not written: the job is below analyst.min_score, rejected or closed"
                )
        except (ValueError, RuntimeError) as exc:
            result.problems.append(f"Letter failed: {exc}")

    found, item = status(conn, row, "resume", version)
    if found == "edited":
        result.problems.append(edited_message(row, "resume"))
        return result
    if found == "current":
        result.current.append("resume")
    else:
        item = materials.tailored_resume(cfg, conn, profile, row)
        result.created.append("resume")
    if pdf_current(item):
        result.current.append("resume PDF")
    else:
        output("Building the resume PDF and checking it for ATS...")
        try:
            _, issues = materials.build_resume_pdf(cfg, conn, profile, row)
            result.created.append("resume PDF")
            if issues:
                output(warn("ATS check found problems; they are listed with the resume below."))
        except (ValueError, RuntimeError, OSError) as exc:
            result.problems.append(f"Resume PDF failed: {exc}")
    return result


def prepare_answers(cfg, conn, profile, row, result, *, interactive, output=log):
    """Ask only what answers.yaml cannot answer, then refresh the draft if it is out of date."""
    version = import_profile(conn, profile)
    found, item = status(conn, row, "answers", version)
    if found == "edited":
        result.problems.append(edited_message(row, "answers"))
        return result
    if interactive and any(not i["answer"] for i in answers.plan(cfg, conn, row)):
        output("Screening questions the answer bank cannot answer:")
        answers.ask_missing(cfg, conn, row, interactive=True, output=output)
    items = answers.plan(cfg, conn, row)
    if found == "current" and yaml.safe_load(item["content"]) == {"items": items}:
        result.current.append("answers")
    else:
        answers.snapshot(cfg, conn, profile, row)
        result.created.append("answers")
    result.unanswered = [i for i in items if not i["answer"]]
    return result

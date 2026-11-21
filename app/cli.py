"""Terminal discovery, review and provider setup, following the sibling agents."""

import argparse
import contextlib
import json
import logging
import re
import sqlite3
import sys
import webbrowser
from datetime import date
from pathlib import Path

import httpx
import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text

from app import answers, applications, keywords, materials, stop
from app.candidate.profile import import_profile, load_profile
from app.db import connect, migrate
from app.pipeline.discover import discover
from app.resume import digest
from app.settings import ROOT, Settings
from app.sources.config import load_sources
from app.sources.feed_review import register_feed_review_command, run_feed_review_command
from app.sources.firms import register_firms_command, run_firms_command
from app.store import export_csv, list_jobs, override_score, review_job

console = Console(markup=False)


def _text(value) -> Text:
    # Source data must not become Rich markup or terminal escape sequences.
    value = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", str(value or ""))
    return Text(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value))


def _positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than zero")
    return number


def _date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD") from exc


def _filters(parser, *, review=False):
    parser.add_argument("--title", help="Title contains this text")
    parser.add_argument("--employer", help="Employer contains this text")
    parser.add_argument("--location", help="Location contains this text")
    parser.add_argument("--source", help="Source ID")
    parser.add_argument("--job", type=_positive, help="Only this job ID")
    parser.add_argument("--since", type=_date, help="Discovered on/after YYYY-MM-DD")
    parser.add_argument(
        "--status",
        choices=["new", "saved", "later", "rejected", "all"],
        default=None,
        help="Review decision (default excludes rejected)",
    )
    parser.add_argument("--application-state", choices=materials.STATES)
    parser.add_argument("--limit", type=_positive, default=50)
    parser.add_argument(
        "--all-scores",
        action="store_true",
        help="Include below-threshold and unscored jobs (for manual overrides)",
    )


def _select(rows, args, *, review=False, min_score=0):
    result = []
    for row in rows:
        if (
            getattr(args, "application_state", None)
            and row.get("application_state") != args.application_state
        ):
            continue
        if getattr(args, "job", None) and args.job not in row.get("grouped_job_ids", [row["id"]]):
            continue
        # A job added by link is the person's own pick: it skips the score gate too.
        if (
            not args.all_scores
            and min_score > 0
            and not row.get("added_by_link")
            and row.get("review") != "saved"
            and not row.get("application_state")
            and (row.get("score") is None or row["score"] < min_score)
        ):
            continue
        if any(
            getattr(args, field)
            and getattr(args, field).casefold() not in (row.get(field) or "").casefold()
            for field in ("title", "employer")
        ):
            continue
        if (
            args.location
            and args.location.casefold()
            not in " ".join(row.get("locations") or [row.get("location") or ""]).casefold()
        ):
            continue
        if args.source and not any(x["source_id"] == args.source for x in row["origins"]):
            continue
        if args.since and row["discovered_at"][:10] < args.since:
            continue
        if args.status is None:
            if row["review"] == "rejected" or (
                review and row["review"] == "saved" and not row.get("application_state")
            ):
                continue
        elif args.status != "all" and row["review"] != args.status:
            continue
        result.append(row)
    return result[: args.limit]


def _list(rows):
    table = Table(
        "#",
        "ID",
        "Title",
        "Employer",
        "Location",
        "Posted / discovered",
        "Review / application",
        "Score / evidence / gaps",
        "Resume keywords",
        "Apply link",
        title="Jobs — highest fit score first",
        show_lines=True,
    )
    for column in table.columns:
        column.overflow = "fold"
    for rank, row in enumerate(rows, 1):
        table.add_row(
            str(rank),
            str(row["id"]),
            _text(row["title"]),
            _text(row["employer"]),
            _text("; ".join(row.get("locations") or []) or row["location"] or "unknown"),
            _text(row.get("date_label", "posting date unknown")),
            row["review"]
            + " / "
            + (row.get("application_state") or "unprepared")
            + (
                f"\ncompany cooldown until {row['company_cooldown_until'][:10]}"
                if row.get("company_cooldown_until")
                else ""
            ),
            _text(_score_summary(row)),
            _text(_keyword_cell(row)),
            Text("Open", style=f"link {row['apply_url']}"),
        )
    console.print(table)
    # Keep full copyable links in redirected output and narrow terminals too.
    for row in rows:
        if len(row.get("grouped_job_ids", [])) > 1:
            console.print(_text(f"{row['id']} grouped stored job IDs: {row['grouped_job_ids']}"))
        for line in _link_lines(row)[:1]:
            console.print(_text(f"{row['id']} {line}"))
        for origin in row["origins"]:
            console.print(
                _text(
                    f"{row['id']} origin {origin['id']} ({origin['source_id']}): {origin['apply_url']}"
                ),
                soft_wrap=True,
            )
    console.print(f"{len(rows)} matching roles. Unscored jobs have no verified matching result.")


def _keyword_cell(row):
    if "keywords" not in row:
        return ""
    if row["keywords"] is None and not row.get("keywords_resume"):
        return "no resume.md"
    return keywords.summary(row["keywords"])


def _keyword_lines(row, limit=8):
    """Informational: the LLM score, score gate and filters never use this."""
    match = row.get("keywords")
    if not row.get("keywords_resume"):
        return ["Resume keywords: no resume.md to compare"]
    if not match:
        return ["Resume keywords: no recognised job keywords (informational)"]
    lines = [
        f"Resume keywords: {match['score']}% — {len(match['present'])} of {match['total']} "
        + "job keywords are in resume.md (informational; not part of the score)"
    ]
    for label, key in (("In resume", "present"), ("Missing", "missing")):
        items = match[key]
        if items:
            more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
            lines.append(f"  {label}: " + ", ".join(items[:limit]) + more)
    return lines


def _score_summary(row):
    evaluation = row.get("evaluation")
    override = row.get("override")
    parts = ["unscored" if row.get("score") is None else f"{row['score']}/100"]
    if evaluation:
        parts += [evaluation["recommendation"], f"confidence: {evaluation['confidence']}"]
        parts += [
            "Evidence: " + "; ".join(evaluation["strengths"][:2]),
            "Gaps: " + ("; ".join(evaluation["gaps"][:2]) or "none stated"),
        ]
        parts += ["Uncertainty: " + x for x in evaluation["uncertainty_flags"]]
    if override:
        parts.append(f"User override: {override['recommendation']} — {override['reason']}")
    return "\n".join(parts)


def _link_lines(row):
    if not row.get("added_by_link"):
        return []
    notes = row.get("filter_notes") or []
    if not notes:
        return ["Added by link."]
    return [
        "Added by link; normally excluded by: " + "; ".join(notes),
        "Kept for you to decide: save (s) or reject (r).",
    ]


def read_key() -> str:
    """One key without Enter on macOS/Linux, restoring the terminal on every exit."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1).lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _review(conn, rows, cfg=None, profile=None):
    if not sys.stdin.isatty():
        raise ValueError("Review needs an interactive terminal: uv run bio-jobs review")
    if not rows:
        console.print(
            "No jobs to review. Run bio-jobs scan or use --all-scores to adjust the score gate."
        )
    approved_this_run = 0
    for index, row in enumerate(rows, 1):
        console.rule(f"{index}/{len(rows)}")
        console.print(_text(f"{row['title']} — {row['employer']}"))
        if len(row.get("grouped_job_ids", [])) > 1:
            console.print(_text(f"Grouped stored job IDs: {row['grouped_job_ids']}"))
        console.print(_text(f"{row['location'] or 'unknown'}; discovered {row['discovered_at']}"))
        console.print(_text(row.get("date_label", "posting date unknown")))
        if row.get("company_cooldown_until"):
            console.print(_text(f"company cooldown until {row['company_cooldown_until'][:10]}"))
        for line in _link_lines(row):
            console.print(_text(line))
        console.print(_text(row["description"]))
        console.print(_text(_score_summary(row)))
        for line in _keyword_lines(row):
            console.print(_text(line))
        if row.get("evaluation"):
            console.print(
                _text(
                    f"Scoring version: {row['scoring_version']}; "
                    f"reviewed profile version: {row['profile_version']}"
                )
            )
            for name, dimension in row["evaluation"]["dimensions"].items():
                console.print(
                    _text(
                        f"{name} (weight {row['scoring_weights'][name]}): "
                        f"{dimension['score']} ({dimension['confidence']}); "
                        f"{dimension['explanation']}; "
                        f"candidate: {dimension['candidate_evidence'] or 'unknown'}; "
                        f"job: {dimension['job_evidence'] or 'unknown'}"
                    )
                )
            checks = [r for r in row["evaluation"]["must_haves"] if r["status"] != "met"]
            console.print("Check before applying:")
            if not checks:
                console.print(
                    "  Confirm eligibility and conditions with the employer; absence of a listed requirement is not confirmation."
                )
            for requirement in checks[:5]:
                console.print(
                    _text(
                        f"  {requirement['requirement']} ({requirement['status']}); "
                        f"candidate: {requirement['candidate_evidence'] or 'unknown'}; "
                        f"job: {requirement['job_evidence']}"
                    )
                )
            for requirement in row["evaluation"]["must_haves"]:
                console.print(
                    _text(
                        f"Must-have {requirement['status']}: {requirement['requirement']}; "
                        f"candidate: {requirement['candidate_evidence'] or 'unknown'}; "
                        f"job: {requirement['job_evidence']}"
                    )
                )
        for origin in row["origins"]:
            console.print(
                _text(f"Origin {origin['id']} ({origin['source_id']}): {origin['apply_url']}")
            )
        console.print(_text("Locations: " + ("; ".join(row.get("locations") or []) or "unknown")))
        _show_materials(conn, row, profile, cfg)
        console.print(
            "o open | s save | r reject | l later | v override | e edit letter | "
            "w rewrite | d diff | "
            + (
                "a approve/fill"
                if cfg and cfg.apply.mode == "assisted"
                else "a approve/open"
                if cfg and cfg.apply.mode == "manual"
                else "a approve"
            )
            + " | t mark submitted | m state | q quit"
        )
        while True:
            action = read_key()
            if action in ("q", ""):
                return
            if action in ("e", "w", "d", "a", "t", "m"):
                try:
                    if action == "d":
                        console.print(_text(materials.material_diff(conn, row["id"])))
                    elif cfg is None or profile is None:
                        raise ValueError("Preparation requires configured profile")
                    elif action == "e":
                        materials.edit_letter(cfg, conn, profile, row)
                        _show_materials(conn, row, profile)
                    elif action == "w":
                        instruction = input("Rewrite instruction: ").strip()
                        if instruction:
                            materials.write_letters(
                                cfg, conn, profile, [row], force=True, instruction=instruction
                            )
                            _show_materials(conn, row, profile)
                    elif action == "a":
                        # Gates first, so ineligible jobs never spend LLM calls on materials.
                        applications.check_gates(cfg, conn, row, used=approved_this_run)
                        applications.check_company(cfg, conn, row)
                        prepared = _prepare(conn, row, cfg, profile, interactive=True)
                        if prepared.problems:
                            console.print("Approval not recorded.")
                            continue
                        expected = {}
                        if prepared.created:
                            ids = {
                                kind: materials.latest(conn, row["id"], kind)["id"]
                                for kind in ("letter", "answers")
                            }
                            bank = digest(answers.load_answers(cfg))
                            try:
                                applications.validate_materials(cfg, conn, profile, row)
                            except applications.UnresolvedWarnings:
                                pass  # the warning prompt below is the confirmation
                            else:
                                if (
                                    input("Type approve to approve these materials: ")
                                    .strip()
                                    .lower()
                                    != "approve"
                                ):
                                    console.print("Approval cancelled; materials kept.")
                                    continue
                            expected = {"expected_ids": ids, "expected_answers_hash": bank}
                        try:
                            paths = applications.approve(
                                cfg,
                                conn,
                                profile,
                                row,
                                approved_this_run=approved_this_run,
                                **expected,
                            )
                        except applications.UnresolvedWarnings as exc:
                            for kind, warnings in exc.warnings.items():
                                console.print(_text(f"{kind} warnings:"))
                                for warning in warnings:
                                    console.print(_text(f"  - {warning}"))
                            if (
                                input("Type approve to accept these warnings: ").strip().lower()
                                != "approve"
                            ):
                                console.print("Approval cancelled.")
                                continue
                            paths = applications.approve(
                                cfg,
                                conn,
                                profile,
                                row,
                                approved_this_run=approved_this_run,
                                acknowledged_warnings=exc.warnings,
                                expected_ids=exc.ids,
                                expected_answers_hash=exc.answers_hash,
                            )
                        approved_this_run += 1
                        console.print(f"Approved. Apply mode: {cfg.apply.mode}.")
                        for path in paths:
                            console.print(_text(path))
                        if cfg.apply.mode == "assisted":
                            console.print(
                                "Press f to start filling; you review and press Submit yourself (any other key skips)."
                            )
                            if read_key() == "f":
                                from app.autopilot import run

                                run(
                                    cfg,
                                    conn,
                                    profile,
                                    [row],
                                    job=row["id"],
                                    output=lambda message: console.print(_text(message)),
                                )
                            continue
                        if cfg.apply.mode != "manual":
                            console.print(
                                f"Run bio-jobs apply {row['id']} to fill in {cfg.apply.mode} mode."
                            )
                            continue
                        applications.check_company(cfg, conn, row)
                        from app.autopilot import event

                        event(conn, row["id"], None, "manual_open", url=row["apply_url"])
                        if not webbrowser.open(row["apply_url"]):
                            console.print("Could not open browser; use the apply link above.")
                        console.print(
                            "Complete the application yourself, then t to record submission."
                        )
                    elif action == "t":
                        applications.confirm_submitted(
                            conn,
                            row,
                            input("Type submitted to confirm you completed the application: "),
                        )
                        console.print("Recorded submitted (user confirmed).")
                    elif action == "m":
                        value = input(
                            "State (saved/preparing/awaiting_review/blocked/manual_required/withdrawn): "
                        ).strip()
                        if value not in set(materials.STATES) - {
                            "approved",
                            "submitted",
                            "submitting",
                            "unknown_result",
                        }:
                            raise ValueError("Use a to approve or t to confirm submission")
                        materials.state(conn, row["id"], value)
                        row["application_state"] = value
                        console.print(f"State: {value}")
                except (ValueError, RuntimeError, OSError) as exc:
                    console.print(_text(str(exc)))
                continue
            if action == "o":
                if not webbrowser.open(row["apply_url"]):
                    console.print("Could not open a browser. Use the apply link above.")
                continue
            if action == "v":
                try:
                    score = int(input("Override score (0-100): ").strip())
                    recommendation = input("Recommendation (apply/maybe/skip): ").strip()
                    reason = input("Reason: ").strip()
                    override_score(conn, row["id"], score, recommendation, reason)
                    row["score"] = score
                    console.print("Override saved; original evaluation preserved.")
                except ValueError as exc:
                    console.print(_text(str(exc)))
                continue
            if action in ("s", "r", "l"):
                decision = {"s": "saved", "r": "rejected", "l": "later"}[action]
                review_job(conn, row["id"], decision)
                console.print(f"{decision.capitalize()}.")
                break


def _show_materials(conn, row, profile=None, cfg=None):
    current = conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()
    console.print(f"Application: {current[0] if current else 'unprepared'}")
    if cfg is not None:
        from app.resume_pdf import pdf_path

        console.print(_text(f"resume PDF (attached unchanged): {pdf_path(cfg)}"))
    for kind in ("letter", "answers"):
        item = materials.latest(conn, row["id"], kind)
        if not item:
            continue
        console.print(
            _text(f"{kind} v{item['id']} / profile {item['profile_version']}: {item['path']}")
        )
        if kind == "letter":
            console.print(_text(item["content"]))
            provenance = json.loads(item["provenance"])
            if provenance.get("source") == "user edit":
                console.print("Fact provenance: user-edited letter; verify its claims yourself.")
            for claim in provenance.get("claims", []):
                console.print(_text("Claim: " + claim["text"]))
                if not claim.get("fact_ids"):
                    console.print("  WARNING: no cited facts")
                for fact_id in claim.get("fact_ids", []):
                    if (
                        profile is None
                        or not isinstance(fact_id, int)
                        or not 0 <= fact_id < len(profile.facts)
                    ):
                        console.print(_text(f"  WARNING: cited fact {fact_id} no longer exists"))
                    else:
                        fact = profile.facts[fact_id]
                        console.print(_text(f"  Fact: {fact.fact}\n  CV evidence: {fact.evidence}"))
        if item["pdf_path"]:
            console.print(_text(item["pdf_path"]))
        for warning in json.loads(item["warnings"]) + json.loads(item["ats_warnings"] or "[]"):
            console.print(_text("WARNING: " + warning))


def _prepare(conn, row, cfg, profile, *, interactive, show="created"):
    """Create missing/stale materials, show them, then ask unanswered screening questions."""
    from app.prepare import prepare_answers, prepare_materials

    def say(message):
        console.print(_text(message))

    result = prepare_materials(cfg, conn, profile, row, output=say)
    if show == "always" or result.created:
        _show_materials(conn, row, profile, cfg)
    prepare_answers(cfg, conn, profile, row, result, interactive=interactive, output=say)
    if result.created:
        say("Prepared: " + ", ".join(result.created))
    if result.current:
        say("Already current, left unchanged: " + ", ".join(result.current))
    for problem in result.problems:
        say("Not prepared: " + problem)
    if result.unanswered:
        say("Unanswered screening questions:")
        for item in result.unanswered:
            say(f"  - {item['question']}" + ("" if item["required"] else " (optional)"))
        if not interactive:
            say(f"Answer them in a terminal: uv run bio-jobs prepare {row['id']}")
        elif any(item["required"] for item in result.unanswered):
            say("Approval will ask you to accept the unanswered required questions as warnings.")
    return result


def _stats(conn, cfg):
    console.print(
        f"Apply mode: {cfg.apply.mode}; "
        f"daily_limit={cfg.apply.daily_limit}, max_per_run={cfg.apply.max_per_run}, "
        f"min_score={cfg.apply.min_score}, active_hours={cfg.apply.active_hours} (local time)"
    )
    for value, count in conn.execute(
        "SELECT state,count(*) FROM applications GROUP BY state ORDER BY state"
    ):
        console.print(f"Application {value}: {count}")
    total = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    reviews = dict(conn.execute("SELECT decision,count(*) FROM job_reviews GROUP BY decision"))
    console.print(
        f"Stored jobs: {total}; new: {total - sum(reviews.values())}; "
        f"saved: {reviews.get('saved', 0)}; rejected: {reviews.get('rejected', 0)}; later: {reviews.get('later', 0)}"
    )
    table = Table(
        "Source",
        "Health",
        "Last checked",
        "Last success",
        "Next allowed",
        "Failures",
        "Last count",
        "Last error at",
        "Error",
        title="Source health",
    )
    for row in conn.execute("SELECT * FROM sources ORDER BY id"):
        table.add_row(
            *(
                _text(row[key])
                for key in (
                    "id",
                    "health",
                    "last_checked_at",
                    "last_success_at",
                    "next_allowed_at",
                    "consecutive_failures",
                    "last_count",
                    "last_error_at",
                    "last_error",
                )
            )
        )
    console.print(table)
    scored = conn.execute("SELECT count(DISTINCT job_id) FROM evaluations").fetchone()[0]
    console.print(f"Jobs with scoring history: {scored}")
    path = cfg.resolved(cfg.llm_state_dir) / "llm-usage.jsonl"
    events = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get("event") == "llm":
                    events.append(event)
                elif isinstance(event, dict) and event.get("event") == "scoring_failure":
                    console.print(
                        _text(
                            f"Scoring failure job {event['job_id']}: {event['category']}; "
                            f"{event['backend']}/{event['model']}; {event['action']}"
                        )
                    )
            except ValueError:
                continue
    table = Table(
        "Backend / model",
        "Purpose",
        "Calls",
        "Errors",
        "Usage reported",
        "Input",
        "Cached",
        "Output",
        "Known cost (USD)",
        title="LLM usage",
    )
    for backend, model, purpose in sorted(
        {(e["backend"], e.get("model") or "default", e.get("purpose", "unknown")) for e in events}
    ):
        selected = [
            e
            for e in events
            if e["backend"] == backend
            and (e.get("model") or "default") == model
            and e.get("purpose", "unknown") == purpose
        ]
        costs = [e["cost_usd"] for e in selected if e.get("cost_usd") is not None]
        table.add_row(
            _text(f"{backend} / {model}"),
            purpose,
            str(len(selected)),
            str(sum(e["status"] != "ok" for e in selected)),
            f"{sum(bool(e.get('usage_available')) for e in selected)}/{len(selected)} calls",
            *(str(sum(e.get(key, 0) for e in selected)) for key in ("input", "cached", "output")),
            f"{sum(costs):.6f} ({len(costs)}/{len(selected)} calls)" if costs else "unknown",
        )
    console.print(table)


def _add(conn, cfg, profile, sources, args):
    from app.analyst import analyze_new, attach_evaluations
    from app.pipeline.add import add

    result = add(
        conn,
        args.url,
        sources,
        employer=args.employer,
        timeout=cfg.http_timeout,
        user_agent=cfg.user_agent,
    )
    job_id = result["job_id"]
    if result["status"] == "existing":
        console.print(_text(f"Already stored as job {job_id}; nothing added."))
    elif result["status"] == "merged":
        console.print(
            _text(f"Matches stored job {job_id}; the link was added as another origin of it.")
        )
    else:
        where = "watchlist board" if result["on_watchlist"] else "not on the watchlist"
        console.print(_text(f"Added job {job_id} by link ({result['adapter']}; {where})."))
    scoring = None
    if result["status"] != "existing":
        try:
            analyze_new(cfg, conn, profile, sources, job_ids={job_id})
        except (ValueError, RuntimeError, OSError) as exc:
            scoring = str(exc)
    rows = attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources, cfg))
    row = next((r for r in rows if job_id in r.get("grouped_job_ids", [r["id"]])), None)
    if row is None:
        # Stored, but no longer open or otherwise hidden; show what is on record.
        console.print(_text(f"Job {job_id} is stored but not open for review."))
        return 0
    console.print(_text(f"{row['title']} — {row['employer']} ({row['location'] or 'unknown'})"))
    for line in _link_lines(row)[:1]:
        console.print(_text(line))
    if row.get("score") is not None:
        console.print(_text("Score: " + _score_summary(row).split("\n", 2)[0]))
        if row.get("evaluation"):
            console.print(_text(f"Recommendation: {row['evaluation']['recommendation']}"))
    else:
        console.print(
            _text(
                "Score: unscored"
                + (f" ({scoring})" if scoring else "")
                + ". Next: fix that, then uv run bio-jobs analyze."
            )
        )
    status = " --status all" if row["review"] != "new" else ""
    console.print(_text(f"Review: uv run bio-jobs review --job {row['id']}{status}"))
    return 0


def _run(args, cfg):
    from app.private import tighten_configured

    tighten_configured(cfg)
    if args.command == "feed-review":
        return run_feed_review_command(args)
    if args.command == "firms":
        return run_firms_command(args, timeout=cfg.http_timeout, user_agent=cfg.user_agent)
    if args.command == "doctor":
        from app.doctor import run

        return run(cfg, online=args.online)
    if args.command == "setup":
        from app.wizard import setup

        return int(not setup(cfg))
    if (
        args.command not in ("init", "import-profile")
        and not cfg.resolved(cfg.config_path).exists()
    ):
        if not sys.stdin.isatty():
            raise ValueError(
                "No config.yaml. Run ./setup.sh or uv run bio-jobs setup in a terminal."
            )
        from app.wizard import setup

        console.print("First run: choose an LLM and import your profile.")
        return int(not setup(cfg))
    if args.command == "llm-check":
        from app.llm import check

        configured = cfg.with_config()
        check(configured)
        console.print(
            "LLM check: OK (structured answer validated). See stats for the backend used."
        )
        return 0
    if args.command not in ("init", "import-profile"):
        cfg = cfg.with_config()
    tighten_configured(cfg)
    with connect(cfg.resolved(cfg.database_path)) as conn:
        migrate(conn)
        if args.command == "init":
            console.print("Database ready")
            return 0
        if args.command == "unmerge":
            from app.pipeline.dedupe import unmerge

            new_id = unmerge(conn, args.job, args.origin)
            console.print(f"Origin separated into job {new_id}")
            return 0
        if args.command == "stats":
            if args.runs:
                directory = ROOT / "run-summaries"
                for path in sorted(
                    directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
                ):
                    console.print(_text(path.read_text(encoding="utf-8")))
                return 0
            _stats(conn, cfg)
            return 0
        profile = load_profile(
            args.path
            if args.command == "import-profile" and args.path
            else cfg.resolved(cfg.candidate_path)
        )
        if args.command == "import-profile":
            console.print(
                json.dumps(
                    {"profile_version": import_profile(conn, profile), "reviewed": profile.reviewed}
                )
            )
            return 0
        if args.command == "extract-profile":
            from app.resume import extract_to_file

            target = args.output or cfg.resolved(cfg.candidate_path)
            if target == ROOT / "config/candidate.example.yaml":
                target = target.with_name("candidate.yaml")
            _extracted, dropped = extract_to_file(cfg, conn, profile, target, force=args.force)
            console.print(
                f"Extracted unreviewed profile: {target}; dropped {dropped} unsupported facts."
            )
            console.print(
                f"Next: open {target}, check every fact against your resume, then set reviewed: true."
            )
            if target != cfg.resolved(cfg.candidate_path):
                console.print(f"To use it, set BIO_CANDIDATE_PATH={target} in .env.")
            return 0
        if args.command == "resume-from-pdf":
            from app.resume_pdf import write_resume_md

            target, _markdown = write_resume_md(cfg, force=args.force, source=args.pdf)
            console.print(f"Wrote {target} from the resume PDF text.")
            console.print(
                f"Next: check {target} against your PDF and correct any layout or wording, "
                "then run uv run bio-jobs extract-profile --force and review the profile."
            )
            return 0
        if args.command == "resume":
            from app.resume_pdf import check

            md = cfg.resolved(cfg.resume_path)
            result = check(cfg, profile, md.read_text(encoding="utf-8") if md.exists() else "")
            console.print(_text(f"Resume PDF: {result.path}"))
            for problem in result.problems:
                console.print(_text("BLOCKER: " + problem))
            if result.problems:
                return 1
            console.print(
                "OK: applications attach this file as it is; its text has your name and email."
            )
            return 0
        if not profile.reviewed:
            console.print(
                "Profile is unreviewed; edit your candidate YAML before relying on these preferences."
            )
        if args.command in ("diff", "state"):
            row = conn.execute("SELECT id FROM jobs WHERE id=?", (args.job,)).fetchone()
            if not row:
                raise ValueError("Unknown job ID")
            if args.command == "diff":
                console.print(_text(materials.material_diff(conn, args.job, args.kind)))
            else:
                materials.state(conn, args.job, args.value)
                console.print(f"Application {args.job}: {args.value}")
            return 0
        sources = load_sources(cfg.resolved(cfg.sources_path))
        if args.command == "add":
            return _add(conn, cfg, profile, sources, args)
        if args.command == "watch":
            from app.watch import run

            result = run(
                cfg,
                conn,
                profile,
                sources,
                scheduled=args.scheduled,
                output=lambda message: console.print(_text(message)),
            )
            return int(bool(result.get("errors")))
        if getattr(args, "mode", None):
            cfg = cfg.model_copy(update={"apply": cfg.apply.model_copy(update={"mode": args.mode})})
        if args.command == "autopilot" and cfg.apply.mode not in ("auto", "dry_run"):
            raise ValueError(
                "autopilot requires apply.mode: auto (or --mode dry_run); "
                "review and approve jobs first"
            )
        poll = args.command == "autopilot" and not args.no_scan
        if args.command in ("scan", "analyze") or poll:
            from app.resume import require_reviewed

            require_reviewed(cfg, profile)
        if args.command in ("sync", "scan") or poll:
            results = discover(
                conn, profile, sources, timeout=cfg.http_timeout, user_agent=cfg.user_agent
            )
            table = Table("Source", "Status", "Fetched", "Accepted", "Error")
            for row in results:
                table.add_row(
                    *(
                        _text(row.get(key))
                        for key in ("source", "status", "fetched", "accepted", "error")
                    )
                )
            console.print(table)
            if args.command == "sync":
                return int(bool(results) and all(x["status"] == "error" for x in results))
        if args.command in ("scan", "analyze") or poll:
            from app.analyst import analyze_new

            done = analyze_new(cfg, conn, profile, sources)
            console.print(f"Scored {done} jobs; unchanged evaluations reused.")
            if not poll:
                return int(
                    args.command == "scan"
                    and bool(results)
                    and all(x["status"] == "error" for x in results)
                )
        from app.analyst import attach_evaluations

        ranked = attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources, cfg))
        if args.command in ("apply", "autopilot"):
            from app.autopilot import run

            run(
                cfg,
                conn,
                profile,
                ranked,
                job=getattr(args, "job", None),
                unattended=args.command == "autopilot",
                output=lambda message: console.print(_text(message)),
            )
            return 0
        if args.command == "import":
            row = next((r for r in ranked if r["id"] == args.job), None)
            if not row:
                raise ValueError("Unknown or ineligible job ID")
            old = materials.latest(conn, args.job, args.kind)
            if not old:
                raise ValueError(f"Prepare {args.kind} first")
            path = Path(old["path"])
            content = path.read_text(encoding="utf-8")
            import difflib

            console.print(
                _text(
                    "".join(
                        difflib.unified_diff(
                            old["content"].splitlines(keepends=True),
                            content.splitlines(keepends=True),
                            fromfile="stored",
                            tofile="external",
                        )
                    )
                    or "No changes."
                )
            )
            if content == old["content"]:
                return 0
            if input("Type import to save this version: ").strip() != "import":
                raise ValueError("Import cancelled")
            current = materials.latest(conn, args.job, args.kind)
            if current["id"] != old["id"] or path.read_text(encoding="utf-8") != content:
                raise ValueError("Material changed during confirmation; review again")
            warnings = ["Externally edited material: review all content and claims"]
            if args.kind == "answers" and yaml.safe_load(content) != {
                "items": answers.plan(cfg, conn, row)
            }:
                warnings.append(
                    "Answers differ from the answer bank; update the bank and regenerate before approval"
                )
            item = materials.save_version(
                cfg,
                conn,
                profile,
                row,
                args.kind,
                content,
                warnings,
                {"source": "external import", "previous_version": old["id"]},
            )
            console.print(_text(item["path"]))
            return 0
        if args.command in ("letters", "letter-pdf", "answers", "prepare"):
            from app.resume import require_reviewed

            require_reviewed(cfg, profile)
            selected = [r for r in ranked if not args.job or r["id"] == args.job]
            if args.job and not selected:
                raise ValueError("Unknown or ineligible job ID")
            if args.job and (
                selected[0].get("score") is None or selected[0]["score"] < cfg.analyst.min_score
            ):
                raise ValueError(applications.score_gate_message(cfg, selected[0]))
            if args.command == "prepare":
                result = _prepare(
                    conn,
                    selected[0],
                    cfg,
                    profile,
                    interactive=sys.stdin.isatty(),
                    show="always",
                )
                if result.problems:
                    return 1
                console.print(
                    "Next: approve in uv run bio-jobs review --application-state "
                    "awaiting_review (press a)."
                )
            elif args.command == "letters":
                done = materials.write_letters(cfg, conn, profile, selected, force=args.force)
                console.print(
                    f"Prepared {done} letters. Review shows warnings and fact provenance."
                )
                if args.job and not done:
                    console.print(
                        "No new letter prepared; the current version may already be up to date. Use --force to rebuild."
                    )
                    return 1
            elif args.command == "answers":
                if args.add:
                    if not args.job:
                        raise ValueError("--add requires --job")
                    answers.add_question(conn, args.job, args.add, args.option, not args.optional)
                if not args.show and not sys.stdin.isatty():
                    raise ValueError("answers needs an interactive terminal, or use --show")
                selected = [
                    r
                    for r in selected
                    if args.job
                    or conn.execute(
                        "SELECT 1 FROM screening_questions WHERE job_id=?", (r["id"],)
                    ).fetchone()
                ]
                answers.collect(
                    cfg,
                    conn,
                    profile,
                    selected,
                    interactive=not args.show,
                    output=lambda text: console.print(_text(text)),
                )
                console.print(
                    f"Screening drafts saved; answer bank: {cfg.resolved(cfg.apply.answers_file)}"
                )
            else:
                row = selected[0]
                console.print(_text(str(materials.build_letter_pdf(cfg, conn, profile, row))))
            return 0
        rows = _select(
            ranked, args, review=args.command == "review", min_score=cfg.analyst.min_score
        )
        keywords.attach_keyword_matches(conn, cfg, rows)
        if args.command == "list":
            _list(rows)
        elif args.command == "review":
            _review(conn, rows, cfg, profile)
        elif args.command == "export":
            args.path.write_text(export_csv(rows), encoding="utf-8-sig")
            console.print(f"Exported {len(rows)} jobs to {args.path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="bio-jobs", description="Local London industry job discovery"
    )
    parser.add_argument("-c", "--config", type=Path, help="LLM YAML config (default: config.yaml)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("help", help="Show commands")
    sub.add_parser("init", help="Apply database migrations")
    sub.add_parser("setup", help="Configure primary/fallback LLM and import a profile")
    doctor = sub.add_parser("doctor", help="Diagnose local setup and source health")
    doctor.add_argument(
        "--online", action="store_true", help="Also check each LLM with a tiny live request"
    )
    sub.add_parser("llm-check", help="Send one tiny structured test request")
    imp = sub.add_parser("import-profile", help="Version reviewed or example YAML preferences")
    imp.add_argument("path", nargs="?", type=Path)
    extraction = sub.add_parser("extract-profile", help="Extract resume facts for mandatory review")
    extraction.add_argument("--output", type=Path)
    extraction.add_argument("--force", action="store_true", help="Replace the output YAML")
    add = sub.add_parser(
        "add", help="Add one job by link (Greenhouse, Lever, Ashby, Workable or any job page)"
    )
    add.add_argument("url", help="https:// link to the job posting")
    add.add_argument("--employer", help="Employer name, when the page does not say")
    sub.add_parser("sync", help="Fetch public sources, respecting persistent cooldowns")
    sub.add_parser("scan", help="Sync public sources and score eligible jobs")
    watch = sub.add_parser("watch", help="One locked sync, score and new/changed job alert pass")
    watch.add_argument(
        "--scheduled",
        action="store_true",
        help="Also run approved autopilot queue when apply.mode is auto",
    )
    sub.add_parser("analyze", help="Score stored jobs with missing or stale evaluations")
    apply = sub.add_parser("apply", help="Apply one approved job or the approved queue")
    apply.add_argument("job", nargs="?", type=_positive)
    dry_help = "Fill and verify forms and save a screenshot, but never press Submit"
    apply.add_argument("--mode", choices=["dry_run"], help=dry_help)
    autopilot = sub.add_parser(
        "autopilot", aliases=["run"], help="One auto pass: sync, score, then apply approved jobs"
    )
    autopilot.add_argument("--no-scan", action="store_true", help="Use stored approved queue only")
    autopilot.add_argument("--mode", choices=["dry_run"], help=dry_help)
    prepare = sub.add_parser(
        "prepare",
        help="Create any missing or stale letter and answers, check the resume PDF; ask what is unknown",
    )
    prepare.add_argument("job", type=_positive)
    letters = sub.add_parser("letters", help="Batch checked cover letters at analyst.min_score")
    letters.add_argument("--job", type=_positive)
    letters.add_argument(
        "--force", action="store_true", help="Create a new version even if current"
    )
    letter_pdf = sub.add_parser("letter-pdf", help="Build a PDF of the saved cover letter")
    letter_pdf.add_argument("job", type=_positive)
    sub.add_parser(
        "resume", help="Check the resume PDF that applications attach unchanged (resume_pdf)"
    )
    from_pdf = sub.add_parser(
        "resume-from-pdf", help="Write resume.md from the text of your resume PDF"
    )
    from_pdf.add_argument("--pdf", type=Path, help="Resume PDF (default: resume_pdf in config)")
    from_pdf.add_argument("--force", action="store_true", help="Replace an existing resume.md")
    screening = sub.add_parser(
        "answers", help="Answer missing screening questions; reuse answers.yaml"
    )
    screening.add_argument("--job", type=_positive)
    screening.add_argument("--add", help="Register a screening question manually")
    screening.add_argument("--option", action="append", default=[])
    screening.add_argument("--optional", action="store_true")
    screening.add_argument(
        "--show",
        action="store_true",
        help="Save drafts and list unanswered questions without prompting",
    )
    material_import = sub.add_parser(
        "import", help="Review and import an externally edited material"
    )
    material_import.add_argument("job", type=_positive)
    material_import.add_argument("--kind", required=True, choices=["letter", "answers"])
    diff = sub.add_parser("diff", help="Diff latest two material versions")
    diff.add_argument("job", type=_positive)
    diff.add_argument("--kind", choices=["letter", "answers"])
    mark = sub.add_parser("state", help="Set preparation state (approval/submitted require review)")
    mark.add_argument("job", type=_positive)
    mark.add_argument(
        "value",
        choices=[
            s
            for s in materials.STATES
            if s not in ("approved", "submitted", "submitting", "unknown_result")
        ],
    )
    _filters(sub.add_parser("list", help="List matching jobs, highest score first"))
    _filters(sub.add_parser("review", help="Review jobs with single-key decisions"), review=True)
    exp = sub.add_parser("export", help="Export matching jobs to CSV")
    exp.add_argument("path", type=Path)
    _filters(exp)
    exp.set_defaults(limit=None)
    split = sub.add_parser("unmerge", help="Separate a non-canonical origin from a job")
    split.add_argument("job", type=_positive)
    split.add_argument("origin", type=_positive)
    stats = sub.add_parser("stats", help="Show job decisions, source health and LLM usage")
    stats.add_argument("--runs", action="store_true", help="Show local run summaries")
    register_firms_command(sub)
    register_feed_review_command(sub)
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help()
        return 0
    if args.command == "run":
        args.command = "autopilot"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cfg = Settings()
        if args.config:
            cfg.config_path = args.config.expanduser().resolve()
        with (
            stop.installed(),
            stop.prevent_sleep()
            if args.command
            in ("scan", "analyze", "letters", "prepare", "watch", "autopilot", "apply")
            else contextlib.nullcontext(),
        ):
            if args.command in ("sync", "scan", "watch", "autopilot", "apply"):
                from app.runlog import run

                with run(args.command) as summary:
                    result = _run(args, cfg)
                    if result:
                        summary["exit_code"] = result
                    return result
            return _run(args, cfg)

    except KeyboardInterrupt:
        console.print("Stopped.")
        return stop.exit_code()
    except (
        ValueError,
        TypeError,
        RuntimeError,
        OSError,
        sqlite3.Error,
        httpx.HTTPError,
        yaml.YAMLError,
    ) as exc:
        console.print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

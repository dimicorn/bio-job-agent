"""Terminal discovery, review and provider setup, following the sibling agents."""

import argparse
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

from app import answers, applications, materials
from app.candidate.profile import import_profile, load_profile
from app.db import connect, migrate
from app.pipeline.discover import discover
from app.settings import ROOT, Settings
from app.sources.config import load_sources
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
        if (
            not args.all_scores
            and min_score > 0
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
            Text("Open", style=f"link {row['apply_url']}"),
        )
    console.print(table)
    # Keep full copyable links in redirected output and narrow terminals too.
    for row in rows:
        if len(row.get("grouped_job_ids", [])) > 1:
            console.print(_text(f"{row['id']} grouped stored job IDs: {row['grouped_job_ids']}"))
        for origin in row["origins"]:
            console.print(
                _text(
                    f"{row['id']} origin {origin['id']} ({origin['source_id']}): {origin['apply_url']}"
                ),
                soft_wrap=True,
            )
    console.print(f"{len(rows)} matching roles. Unscored jobs have no verified matching result.")


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
        console.print(_text(row["description"]))
        console.print(_text(_score_summary(row)))
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
        _show_materials(conn, row, profile)
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
                        try:
                            paths = applications.approve(
                                cfg, conn, profile, row, approved_this_run=approved_this_run
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


def _show_materials(conn, row, profile=None):
    current = conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()
    console.print(f"Application: {current[0] if current else 'unprepared'}")
    for kind in ("letter", "resume", "answers"):
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


def _run(args, cfg):
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
            from app.resume import DROPPED_PREFIX, extract_profile

            target = args.output or cfg.resolved(cfg.candidate_path)
            if target == ROOT / "config/candidate.example.yaml":
                target = target.with_name("candidate.yaml")
            if target.exists() and not args.force:
                raise ValueError(f"{target} exists; choose --output or --force to replace it")
            extracted = extract_profile(cfg, conn, profile)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w" if args.force else "x", encoding="utf-8") as output:
                yaml.safe_dump(extracted.model_dump(), output, sort_keys=False)
            dropped = sum(u.startswith(DROPPED_PREFIX) for u in extracted.uncertainties)
            console.print(
                f"Extracted unreviewed profile: {target}; dropped {dropped} unsupported facts."
            )
            console.print(
                "Review every fact, correct errors, then set reviewed: true. "
                "Set BIO_CANDIDATE_PATH to this file in .env."
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
        if args.command == "autopilot" and cfg.apply.mode != "auto":
            raise ValueError("autopilot requires apply.mode: auto; review and approve jobs first")
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
        if args.command in ("letters", "resume", "pdf", "answers"):
            from app.resume import require_reviewed

            require_reviewed(cfg, profile)
            selected = [r for r in ranked if not args.job or r["id"] == args.job]
            if args.job and not selected:
                raise ValueError("Unknown or ineligible job ID")
            if args.job and (
                selected[0].get("score") is None or selected[0]["score"] < cfg.analyst.min_score
            ):
                raise ValueError(applications.score_gate_message(cfg, selected[0]))
            if args.command == "letters":
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
                if args.command == "resume":
                    item = materials.tailored_resume(cfg, conn, profile, row)
                    console.print(_text(item["path"]))
                else:
                    path, issues = materials.build_resume_pdf(cfg, conn, profile, row)
                    console.print(_text(path))
                    for issue in issues:
                        console.print(_text("ATS WARNING: " + issue))
                    if issues:
                        return 1
                    console.print("ATS check passed: name, email and sections extract in order.")
            return 0
        rows = _select(
            ranked, args, review=args.command == "review", min_score=cfg.analyst.min_score
        )
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
    autopilot = sub.add_parser(
        "autopilot", aliases=["run"], help="One auto pass: sync, score, then apply approved jobs"
    )
    autopilot.add_argument("--no-scan", action="store_true", help="Use stored approved queue only")
    letters = sub.add_parser("letters", help="Batch checked cover letters at analyst.min_score")
    letters.add_argument("--job", type=_positive)
    letters.add_argument(
        "--force", action="store_true", help="Create a new version even if current"
    )
    for command, help_text in (
        ("resume", "Tailor reviewed facts as editable Markdown"),
        ("pdf", "Build job resume PDF and run ATS checks"),
    ):
        preparation = sub.add_parser(command, help=help_text)
        preparation.add_argument("job", type=_positive)
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
    material_import.add_argument("--kind", required=True, choices=["letter", "resume", "answers"])
    diff = sub.add_parser("diff", help="Diff latest two material versions")
    diff.add_argument("job", type=_positive)
    diff.add_argument("--kind", choices=["letter", "resume", "answers"])
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
    sub.add_parser("stats", help="Show job decisions, source health and LLM usage")
    register_firms_command(sub)
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
        return _run(args, cfg)
    except KeyboardInterrupt:
        console.print("Stopped.")
        return 130
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

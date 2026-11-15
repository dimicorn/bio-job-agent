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
            not args.all_scores
            and min_score > 0
            and (row.get("score") is None or row["score"] < min_score)
        ):
            continue
        if any(
            getattr(args, field)
            and getattr(args, field).casefold() not in (row.get(field) or "").casefold()
            for field in ("title", "employer", "location")
        ):
            continue
        if args.source and not any(x["source_id"] == args.source for x in row["origins"]):
            continue
        if args.since and row["discovered_at"][:10] < args.since:
            continue
        if args.status is None:
            if row["review"] == "rejected" or (review and row["review"] == "saved"):
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
        "Review",
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
            _text(row["location"] or "unknown"),
            _text(f"{(row['posted_at'] or 'unknown')[:10]} / {row['discovered_at'][:10]}"),
            row["review"],
            _text(_score_summary(row)),
            Text("Open", style=f"link {row['apply_url']}"),
        )
    console.print(table)
    # Keep full copyable links in redirected output and narrow terminals too.
    for row in rows:
        console.print(_text(f"{row['id']}: {row['apply_url']}"), soft_wrap=True)
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


def _review(conn, rows):
    if not sys.stdin.isatty():
        raise ValueError("Review needs an interactive terminal: uv run bio-jobs review")
    if not rows:
        console.print(
            "No jobs to review. Run bio-jobs scan or use --all-scores to adjust the score gate."
        )
    for index, row in enumerate(rows, 1):
        console.rule(f"{index}/{len(rows)}")
        console.print(_text(f"{row['title']} — {row['employer']}"))
        console.print(_text(f"{row['location'] or 'unknown'}; discovered {row['discovered_at']}"))
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
            for requirement in row["evaluation"]["must_haves"]:
                console.print(
                    _text(
                        f"Must-have {requirement['status']}: {requirement['requirement']}; "
                        f"candidate: {requirement['candidate_evidence'] or 'unknown'}; "
                        f"job: {requirement['job_evidence']}"
                    )
                )
        console.print(_text(row["apply_url"]))
        console.print("o open | s save | r reject | l later | v override score/decision | q quit")
        while True:
            action = read_key()
            if action in ("q", ""):
                return
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
                    console.print("Override saved; original evaluation preserved.")
                except ValueError as exc:
                    console.print(_text(str(exc)))
                continue
            if action in ("s", "r", "l"):
                decision = {"s": "saved", "r": "rejected", "l": "later"}[action]
                review_job(conn, row["id"], decision)
                console.print(f"{decision.capitalize()}.")
                break


def _stats(conn, cfg):
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
            from app.resume import extract_profile

            target = args.output or cfg.resolved(cfg.candidate_path)
            if target == ROOT / "config/candidate.example.yaml":
                target = target.with_name("candidate.yaml")
            if target.exists() and not args.force:
                raise ValueError(f"{target} exists; choose --output or --force to replace it")
            extracted = extract_profile(cfg, conn, profile)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w" if args.force else "x", encoding="utf-8") as output:
                yaml.safe_dump(extracted.model_dump(), output, sort_keys=False)
            console.print(f"Extracted unreviewed profile: {target}")
            console.print(
                "Review every fact, correct errors, then set reviewed: true. "
                "Set BIO_CANDIDATE_PATH to this file in .env."
            )
            return 0
        if not profile.reviewed:
            console.print(
                "Profile is unreviewed; edit your candidate YAML before relying on these preferences."
            )
        sources = load_sources(cfg.resolved(cfg.sources_path))
        if args.command in ("scan", "analyze"):
            from app.resume import require_reviewed

            require_reviewed(cfg, profile)
        if args.command in ("sync", "scan"):
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
        if args.command in ("scan", "analyze"):
            from app.analyst import analyze_new

            done = analyze_new(cfg, conn, profile, sources)
            console.print(f"Scored {done} jobs; unchanged evaluations reused.")
            return int(
                args.command == "scan"
                and bool(results)
                and all(x["status"] == "error" for x in results)
            )
        from app.analyst import attach_evaluations

        ranked = attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources))
        rows = _select(
            ranked, args, review=args.command == "review", min_score=cfg.analyst.min_score
        )
        if args.command == "list":
            _list(rows)
        elif args.command == "review":
            _review(conn, rows)
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
    sub.add_parser("analyze", help="Score stored jobs with missing or stale evaluations")
    _filters(sub.add_parser("list", help="List matching jobs, highest score first"))
    _filters(sub.add_parser("review", help="Review jobs with single-key decisions"), review=True)
    exp = sub.add_parser("export", help="Export matching jobs to CSV")
    exp.add_argument("path", type=Path)
    _filters(exp)
    exp.set_defaults(limit=None)
    sub.add_parser("stats", help="Show job decisions, source health and LLM usage")
    register_firms_command(sub)
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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

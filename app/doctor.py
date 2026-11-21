"""Read-only local diagnostics; network/model checks require --online."""

import os
import shlex
import shutil
import sqlite3
import sys

import httpx
import yaml
from rich.console import Console
from rich.table import Table

from app.candidate.profile import load_profile, require_name
from app.llm import check, fallback_cfg
from app.settings import Settings
from app.sources.config import load_sources

console = Console(markup=False)
CLI_EXECUTABLES = {"codex": "codex", "claude_code": "claude", "opencode": "opencode", "pi": "pi"}


def _describe(a) -> str:
    """Human summary of one configured backend, without secrets."""
    parts = [a.backend]
    if a.provider:
        parts.append(f"provider {a.provider}")
    model = a.codex_model if a.backend == "codex" else a.model
    parts.append(f"model {model or 'CLI default'}")
    if a.base_url:
        parts.append(f"at {a.base_url}")
    if a.command:
        parts.append(f"command {a.command}")
    return ", ".join(parts)


def collect(cfg: Settings, *, online=False) -> tuple[list[tuple[str, str, str]], str | None]:
    """Return (rows, next_step). The next step is the most important unresolved action."""
    from app.private import tighten_configured

    tighten_configured(cfg)
    rows: list[tuple[str, str, str]] = []
    steps: list[tuple[int, str]] = []

    def report(name, ok, ok_detail, fix_detail=None, *, step=None, warn=False):
        status = "OK" if ok else ("WARN" if warn else "MISSING")
        rows.append((name, status, ok_detail if ok else (fix_detail or ok_detail)))
        if not ok and step:
            steps.append(step)

    report(
        "Python",
        sys.version_info >= (3, 12),
        f"{sys.version.split()[0]}",
        "Python 3.12+ required; ./setup.sh installs it via uv",
        step=(0, "./setup.sh"),
    )
    try:
        configured = cfg.with_config()
        tighten_configured(configured)
        report("LLM config", True, str(cfg.resolved(cfg.config_path)))
    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
        configured = None
        report(
            "LLM config",
            False,
            "",
            f"{exc}. Run uv run bio-jobs setup",
            step=(1, "uv run bio-jobs setup"),
        )
    candidate_path = cfg.resolved(cfg.candidate_path)
    try:
        profile = load_profile(candidate_path)
    except (ValueError, OSError, yaml.YAMLError):
        profile = None
        report(
            "Profile",
            False,
            "",
            f"Cannot read {candidate_path}; run uv run bio-jobs setup",
            step=(1, "uv run bio-jobs setup"),
        )
    if profile is not None:
        from app.resume import resume_status
        from app.resume_pdf import check as check_pdf
        from app.resume_pdf import pdf_path

        state, detail = resume_status(configured or cfg, profile)
        has_pdf = pdf_path(configured or cfg).is_file()
        fill = (
            "uv run bio-jobs resume-from-pdf"
            if has_pdf
            else "Fill resume.md with your resume (see resume.example.md)"
        )
        extract = "uv run bio-jobs extract-profile --force"
        report(
            "Resume",
            state == "current",
            detail,
            f"{detail}; then {extract}"
            if state in ("missing", "empty", "example")
            else f"{detail}; run {extract}",
            step=(3, fill) if state in ("missing", "empty", "example") else (4, extract),
            warn=True,
        )
        resume_md = ""
        if state not in ("missing", "empty"):
            resume_md = (
                (configured or cfg)
                .resolved((configured or cfg).resume_path)
                .read_text(encoding="utf-8")
            )
        pdf = check_pdf(configured or cfg, profile, resume_md)
        report(
            "Resume PDF",
            pdf.ok,
            f"{pdf.path} (attached unchanged)",
            "; ".join(pdf.problems),
            step=(2, f"Save your resume PDF at {pdf.path}") if not has_pdf else None,
            warn=True,
        )
        try:
            require_name(profile)
        except ValueError:
            report(
                "Candidate name",
                False,
                "",
                "Still 'Example candidate'; extract-profile reads it from your resume header, "
                f"or set name: in {candidate_path}",
                warn=True,
            )
        report(
            "Profile",
            profile.reviewed,
            f"reviewed; {len(profile.facts)} facts in {candidate_path}",
            f"not reviewed; check every fact in {candidate_path}, then set reviewed: true",
            step=(5, f"Review {candidate_path} and set reviewed: true"),
            warn=True,
        )
    try:
        sources = load_sources(cfg.resolved(cfg.sources_path))
        enabled = [s for s in sources if s.enabled]
        report(
            "Sources",
            bool(enabled),
            f"{len(enabled)} enabled in {cfg.resolved(cfg.sources_path)}",
            f"None enabled; edit {cfg.resolved(cfg.sources_path)}",
        )
    except (ValueError, OSError, yaml.YAMLError) as exc:
        report("Sources", False, "", f"Fix {cfg.resolved(cfg.sources_path)}: {exc}")
    from app.materials import browser_available

    report(
        "Chromium",
        browser_available(),
        "installed (PDFs and browser apply)",
        "Run uv run playwright install chromium",
    )
    if configured:
        a = configured.apply
        report(
            "Apply mode",
            True,
            f"{a.mode}; browser modes require approved materials; "
            f"daily_limit={a.daily_limit}, max_per_run={a.max_per_run}, min_score={a.min_score}, "
            f"active_hours={a.active_hours} (local time)",
        )
    path = cfg.resolved(cfg.database_path)
    if path.exists():
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
                from app.db import MIGRATIONS

                pending = sorted({p.name for p in MIGRATIONS.glob("*.sql")} - applied)
                # Every database command migrates first, so pending upgrades are not a fault.
                report(
                    "Database",
                    not pending,
                    str(path),
                    f"{len(pending)} upgrade(s) pending ({', '.join(pending)}); applied "
                    "automatically by the next command, or run uv run bio-jobs init",
                    warn=True,
                )
                conn.row_factory = sqlite3.Row
                for row in conn.execute("SELECT * FROM sources WHERE adapter!='link' ORDER BY id"):
                    report(
                        f"Source {row[0]}",
                        row["health"] in ("ok", "partial"),
                        f"{row['health']}; last success {row['last_success_at'] or 'never'}; "
                        + (
                            f"last error {row['last_error_at'] or 'never'}: {row['last_error'] or 'none'}; "
                            f"consecutive failures {row['consecutive_failures']}; last count {row['last_count']}"
                            if "consecutive_failures" in set(row.keys())
                            else "run bio-jobs init for health details"
                        ),
                    )
        except sqlite3.Error:
            report(
                "Database",
                False,
                "",
                "Invalid/uninitialised database; run uv run bio-jobs init",
                step=(2, "uv run bio-jobs init"),
            )
    else:
        report(
            "Database",
            False,
            "",
            "Not created yet; run uv run bio-jobs init",
            step=(2, "uv run bio-jobs init"),
        )
    env_path = cfg.resolved(cfg.env_path)
    if env_path.exists():
        report(
            ".env permissions",
            os.stat(env_path).st_mode & 0o077 == 0,
            "owner only",
            f"Readable by others; run chmod 600 {env_path}",
        )
    if configured:
        choices = [("Primary", configured)]
        if configured.analyst.fallback_backend:
            choices.append(("Fallback", fallback_cfg(configured)))
        for label, selected in choices:
            a = selected.analyst
            thinking = a.openai_extra.get("thinking")
            if (
                a.backend == "openai"
                and "deepseek" in (a.base_url or "").lower()
                and (not isinstance(thinking, dict) or thinking.get("type") != "disabled")
            ):
                report(
                    f"{label} thinking",
                    False,
                    "",
                    "DeepSeek thinking is on and uses output tokens; in config.yaml set "
                    "analyst.openai_extra: {thinking: {type: disabled}}",
                    warn=True,
                )
            setup_step = (1, "uv run bio-jobs setup")
            if a.backend in ("openai", "api"):
                name = a.api_key_env or ("ANTHROPIC_API_KEY" if a.backend == "api" else None)
                missing = [
                    item
                    for item, present in (
                        ("model", bool(a.model)),
                        ("base_url", a.backend != "openai" or bool(a.base_url)),
                        (f"{name} in .env", not name or bool(selected.secret(name))),
                    )
                    if not present
                ]
                ok = not missing
                report(
                    label,
                    ok,
                    _describe(a) + (f"; key {name} set" if name else "; no key required"),
                    f"{a.backend}: missing {', '.join(missing)}; run uv run bio-jobs setup",
                    step=setup_step,
                )
            else:
                executable = CLI_EXECUTABLES.get(a.backend)
                if a.backend == "command":
                    executable = shlex.split(a.command or "")[:1]
                    executable = executable[0] if executable else None
                found = bool(executable and shutil.which(executable))
                needs_model = a.backend in ("opencode", "pi") and not (a.provider and a.model)
                ok = found and not needs_model
                problems = []
                if not found:
                    problems.append(f"{executable or 'command'} not found in PATH")
                if needs_model:
                    problems.append("provider/model not set")
                report(
                    label,
                    ok,
                    _describe(a),
                    f"{a.backend}: {'; '.join(problems)}; install/sign in, then uv run bio-jobs setup",
                    step=setup_step,
                )
                if a.api_key_env and not selected.secret(a.api_key_env):
                    report(
                        f"{label} key",
                        False,
                        "",
                        f"Set {a.api_key_env} in .env or sign in through the CLI",
                        step=setup_step,
                    )
            if online and ok:
                try:
                    check(selected, direct=True)
                    report(f"{label} live check", True, "Structured answer validated")
                except (ValueError, RuntimeError, OSError, httpx.HTTPError, yaml.YAMLError):
                    report(
                        f"{label} live check",
                        False,
                        "",
                        "Model check failed; check CLI login, provider/model and credentials",
                        step=setup_step,
                    )
    step = min(steps)[1] if steps else None
    if step is None and configured and profile is not None and profile.reviewed:
        step = "uv run bio-jobs scan, then uv run bio-jobs review"
    return rows, step


def run(cfg: Settings, *, online=False) -> int:
    rows, step = collect(cfg, online=online)
    table = Table("Check", "Status", "Details", title="bio-jobs doctor")
    for row in rows:
        table.add_row(*row)
    console.print(table)
    if not online:
        console.print(
            "LLM credentials/model reachability were not tested. Use llm-check or doctor --online."
        )
    if step:
        console.print(f"Next step: {step}")
    return int(any(status == "MISSING" for _, status, _ in rows))

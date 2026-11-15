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

from app.candidate.profile import load_profile
from app.llm import check, fallback_cfg
from app.settings import Settings
from app.sources.config import load_sources

console = Console(markup=False)


def run(cfg: Settings, *, online=False) -> int:
    rows = []

    def report(name, ok, detail):
        rows.append((name, "OK" if ok else "MISSING", detail))

    report(
        "Python", sys.version_info >= (3, 12), "Python 3.12+ required; ./setup.sh installs via uv"
    )
    try:
        configured = cfg.with_config()
        report("LLM config", True, str(cfg.resolved(cfg.config_path)))
    except (ValueError, OSError, TypeError, yaml.YAMLError):
        configured = None
        report("LLM config", False, "Run uv run bio-jobs setup")
    try:
        profile = load_profile(cfg.resolved(cfg.candidate_path))
        report(
            "Profile", True, f"reviewed={profile.reviewed}; edit {cfg.resolved(cfg.candidate_path)}"
        )
    except (ValueError, OSError, yaml.YAMLError):
        report("Profile", False, "Run bio-jobs setup or bio-jobs import-profile <path>")
    try:
        sources = load_sources(cfg.resolved(cfg.sources_path))
        enabled = [s for s in sources if s.enabled]
        report("Sources", bool(enabled), f"{len(enabled)} enabled; edit config/sources.yaml")
    except (ValueError, OSError, yaml.YAMLError):
        report("Sources", False, "Fix BIO_SOURCES_PATH / config/sources.yaml")
    path = cfg.resolved(cfg.database_path)
    if path.exists():
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
                from app.db import MIGRATIONS

                pending = {p.name for p in MIGRATIONS.glob("*.sql")} - applied
                report("Database", not pending, "Run bio-jobs init" if pending else str(path))
                for row in conn.execute(
                    "SELECT id,health,last_checked_at FROM sources ORDER BY id"
                ):
                    report(
                        f"Source {row[0]}",
                        row[1] in ("ok", "partial"),
                        f"{row[1]}; checked {row[2] or 'never'}; run bio-jobs sync for a refresh",
                    )
        except sqlite3.Error:
            report("Database", False, "Invalid/uninitialised database; run bio-jobs init")
    else:
        report("Database", False, "Run bio-jobs init (or ./setup.sh)")
    env_path = cfg.resolved(cfg.env_path)
    if env_path.exists():
        report(".env permissions", os.stat(env_path).st_mode & 0o077 == 0, f"chmod 600 {env_path}")
    if configured:
        choices = [("Primary", configured)]
        if configured.analyst.fallback_backend:
            choices.append(("Fallback", fallback_cfg(configured)))
        for label, selected in choices:
            a = selected.analyst
            ok = True
            if a.backend in ("openai", "api"):
                name = a.api_key_env or ("ANTHROPIC_API_KEY" if a.backend == "api" else None)
                ok = (
                    bool(a.model)
                    and (a.backend != "openai" or bool(a.base_url))
                    and (not name or bool(selected.secret(name)))
                )
                report(
                    label,
                    ok,
                    f"{a.backend}: set model/base_url and {name or 'no key required'}; bio-jobs setup",
                )
            else:
                executable = {
                    "codex": "codex",
                    "claude_code": "claude",
                    "opencode": "opencode",
                    "pi": "pi",
                }.get(a.backend)
                if a.backend == "command":
                    executable = shlex.split(a.command or "")[:1]
                    executable = executable[0] if executable else None
                ok = bool(executable and shutil.which(executable))
                if a.backend in ("opencode", "pi"):
                    ok = ok and bool(a.provider and a.model)
                report(
                    label,
                    ok,
                    f"{a.backend}: install CLI in PATH, sign in/set key, select provider/model via setup",
                )
                if a.api_key_env and not selected.secret(a.api_key_env):
                    report(
                        f"{label} key",
                        False,
                        f"Set {a.api_key_env} in .env or sign in through the CLI",
                    )
            if online and ok:
                try:
                    check(selected, direct=True)
                    report(f"{label} live check", True, "Structured answer validated")
                except (ValueError, RuntimeError, OSError, httpx.HTTPError, yaml.YAMLError):
                    report(
                        f"{label} live check",
                        False,
                        "Model check failed; check CLI login, provider/model and credentials",
                    )
    table = Table("Check", "Status", "Details", title="bio-jobs doctor")
    for row in rows:
        table.add_row(*row)
    console.print(table)
    if not online:
        console.print(
            "LLM credentials/model reachability were not tested. Use llm-check or doctor --online."
        )
    return int(any(status == "MISSING" for _, status, _ in rows))

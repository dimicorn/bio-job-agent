"""Re-runnable terminal setup. Credentials are saved only in the local .env."""

import getpass
import re
import shutil
import sys

import httpx
import yaml
from dotenv import set_key
from rich.console import Console

from app.candidate.profile import import_profile, load_profile
from app.db import connect, migrate
from app.llm import check, fallback_cfg
from app.settings import ROOT, Analyst, Settings

console = Console(markup=False)
OPTIONS = [
    "DeepSeek API",
    "OpenAI API",
    "Other OpenAI-compatible API (including local servers)",
    "OpenCode",
    "Pi",
    "Codex CLI",
    "Claude Code CLI",
    "Claude API",
    "Custom command",
]


def _ask(prompt: str, default: str = "") -> str:
    while True:
        value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        if value or default:
            return value or default


def _yes(prompt: str, default=False) -> bool:
    return input(f"{prompt} {'[Y/n]' if default else '[y/N]'}: ").strip().lower() in (
        ("y", "yes", "") if default else ("y", "yes")
    )


def _pick(title: str, options: list[str]) -> int:
    console.print(title)
    for i, option in enumerate(options, 1):
        console.print(f"  {i}) {option}")
    while True:
        value = input("> ").strip()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value) - 1


def save_env(cfg: Settings, name: str, value: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            "Environment variable names must contain only letters, digits and underscores"
        )
    if "\n" in value or "\r" in value:
        raise ValueError("Environment values must be a single line")
    path = cfg.resolved(cfg.env_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    set_key(str(path), name, value, quote_mode="always")
    path.chmod(0o600)


def _key(cfg: Settings, name: str | None) -> None:
    if not name or cfg.secret(name):
        return
    value = getpass.getpass(f"{name} (hidden; Enter to configure later): ").strip()
    if value:
        save_env(cfg, name, value)
        console.print(f"Saved {name} in {cfg.resolved(cfg.env_path)} (owner access only).")


def _choose(cfg: Settings, index: int | None = None) -> Analyst:
    i = _pick("Choose an LLM:", OPTIONS) if index is None else index
    values = {}
    if i in (0, 1, 2):
        presets = [
            ("https://api.deepseek.com/v1", "deepseek-flash", "DEEPSEEK_API_KEY"),
            ("https://api.openai.com/v1", "", "OPENAI_API_KEY"),
        ]
        if i < 2:
            url, default_model, name = presets[i]
        else:
            url = _ask("Base URL, before /chat/completions", "http://localhost:11434/v1")
            default_model = ""
            name = input("API key environment variable (Enter for no key): ").strip() or None
        _key(cfg, name)
        values = {
            "backend": "openai",
            "base_url": url,
            "api_key_env": name,
            "model": _ask("Model ID", default_model),
        }
    elif i in (3, 4):
        backend = "opencode" if i == 3 else "pi"
        console.print("Use model IDs from `opencode models` or `pi --list-models`.")
        provider = _ask("Provider", "opencode" if i == 3 else "deepseek")
        default_model = "deepseek-v4.1-flash-free" if provider == "opencode" else "deepseek-flash"
        model = _ask("Model ID (without provider/ prefix)", default_model)
        name = "DEEPSEEK_API_KEY" if provider == "deepseek" else None
        _key(cfg, name)
        values = {"backend": backend, "provider": provider, "model": model, "api_key_env": name}
    elif i == 5:
        console.print("Codex must be installed and signed in: codex login")
        values = {
            "backend": "codex",
            "codex_model": input("Codex model (Enter for CLI default): ").strip() or None,
        }
    elif i == 6:
        console.print("Claude Code must be installed and signed in with its CLI.")
        values = {
            "backend": "claude_code",
            "model": input("Claude model (Enter for CLI default): ").strip() or None,
        }
    elif i == 7:
        _key(cfg, "ANTHROPIC_API_KEY")
        values = {
            "backend": "api",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": _ask("Claude model ID"),
        }
    else:
        console.print(
            "The command receives the prompt on stdin and must print only JSON on stdout."
        )
        console.print("Use an absolute executable/script path; it runs from a scratch directory.")
        values = {"backend": "command", "command": _ask("Command")}
    return Analyst.model_validate(values)


def setup(cfg: Settings) -> bool:
    if not sys.stdin.isatty():
        raise ValueError(
            "Setup needs a terminal. Run `./setup.sh` or `uv run bio-jobs setup` interactively."
        )
    path = cfg.resolved(cfg.config_path)
    document = yaml.safe_load(path.read_text()) if path.exists() else {}
    document = document or {}
    if not isinstance(document, dict):
        raise TypeError("config.yaml must contain a mapping; correct it before rerunning setup")
    if path.exists() and not _yes("LLM config exists. Choose primary/fallback again?"):
        configured = cfg.with_config()
    else:
        primary = _choose(cfg)
        previous = document.get("analyst") or {}
        for key in ("preferences", "batch_size", "parallel", "min_score", "weights"):
            if key in previous:
                validated = Analyst.model_validate({key: previous[key]})
                setattr(primary, key, getattr(validated, key))
        index = _pick("Fallback when the primary reaches its limit:", ["None", *OPTIONS])
        if index:
            fallback = _choose(cfg, index - 1)
            primary.fallback_backend = fallback.backend
            primary.fallback_settings = fallback.model_dump(
                exclude={"backend", "fallback_backend", "fallback_settings"}
            )
        document["analyst"] = primary.model_dump()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        configured = cfg.with_config()
        console.print(f"Saved LLM config: {path}")

    candidate = cfg.resolved(cfg.candidate_path)
    if candidate == ROOT / "config/candidate.example.yaml":
        candidate = ROOT / "config/candidate.yaml"
        if not candidate.exists():
            shutil.copyfile(ROOT / "config/candidate.example.yaml", candidate)
        save_env(cfg, "BIO_CANDIDATE_PATH", "config/candidate.yaml")
    profile = load_profile(candidate)
    with connect(cfg.resolved(cfg.database_path)) as conn:
        migrate(conn)
        version = import_profile(conn, profile)
    console.print(
        f"Imported profile version {version} from {candidate}; reviewed={profile.reviewed}."
    )
    resume = configured.resolved(configured.resume_path)
    example = ROOT / "resume.example.md"
    if not resume.exists() and example.exists():
        resume.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, resume)
    console.print(
        "Fill resume.md, run bio-jobs extract-profile --force, then review "
        "every extracted fact and set reviewed: true only when accurate."
    )
    checks = [("Primary", configured)]
    if configured.analyst.fallback_backend:
        checks.append(("Fallback", fallback_cfg(configured)))
    passed = True
    for label, selected in checks:
        console.print(f"{label}: testing {selected.analyst.backend} with one tiny request…")
        try:
            check(selected, direct=True)
            console.print(f"{label}: OK (schema validated).")
        except (ValueError, RuntimeError, OSError, httpx.HTTPError, yaml.YAMLError):
            passed = False
            console.print(
                f"{label}: check failed. Run bio-jobs doctor; correct config/credentials and rerun setup."
            )
    console.print(
        "Next: uv run bio-jobs doctor; uv run bio-jobs extract-profile --force; "
        "review candidate YAML; uv run bio-jobs scan; uv run bio-jobs list"
    )
    return passed

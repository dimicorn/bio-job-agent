"""Re-runnable terminal setup. Credentials are saved only in the local .env."""

import getpass
import re
import shutil
import sys

import httpx
import yaml
from dotenv import set_key

from app.candidate.profile import import_profile, load_profile
from app.console import console
from app.db import connect, migrate
from app.llm import check, fallback_cfg
from app.settings import ROOT, Analyst, Apply, Settings

# Scoring choices shared by primary and fallback; fallback_settings must never override them.
SHARED = (
    "preferences",
    "letter_style",
    "letter_length",
    "batch_size",
    "parallel",
    "min_score",
    "weights",
)
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
        if "deepseek" in url.lower():
            # Thinking spends the output budget that detailed scoring batches need.
            values["openai_extra"] = {"thinking": {"type": "disabled"}}
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
    current = _current_llm(cfg) if path.exists() else None
    if current:
        console.print(f"Current LLM: {current}")
    if current and not _yes("Change the LLM?"):
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
            primary.fallback_settings = fallback_overrides(primary, fallback)
        document["analyst"] = primary.model_dump()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        configured = cfg.with_config()
        console.print(f"Saved LLM config: {path}")

    if "apply" in document:
        current_apply = Apply.model_validate(document["apply"])
        console.print(
            f"Current apply settings: mode {current_apply.mode}, daily_limit "
            f"{current_apply.daily_limit}, min_score {current_apply.min_score}, active hours "
            f"{current_apply.active_hours[0]}-{current_apply.active_hours[1]}"
        )
    if "apply" not in document or _yes("Change the apply settings?"):
        apply = Apply.model_validate(document.get("apply", {}))
        modes = ["manual", "assisted", "auto", "dry_run"]
        apply.mode = modes[_pick("Apply mode (assisted/auto use manual handoff until M4):", modes)]
        for field in ("daily_limit", "max_per_run", "min_score"):
            while True:
                try:
                    apply = Apply.model_validate(
                        {
                            **apply.model_dump(),
                            field: int(_ask(f"Apply {field}", str(getattr(apply, field)))),
                        }
                    )
                    break
                except ValueError:
                    console.print("Enter a valid limit (positive; score 0–100).")
        while True:
            try:
                hours = _ask(
                    "Active hours in local time: start,end", ",".join(map(str, apply.active_hours))
                )
                apply = Apply.model_validate(
                    {**apply.model_dump(), "active_hours": [int(x) for x in hours.split(",")]}
                )
                break
            except ValueError:
                console.print("Use start,end with 0 <= start < end <= 24.")
        apply.auto_consent = _yes(
            "Automatically accept eligible single-option consent?", default=apply.auto_consent
        )
        apply.auto_llm_answers = _yes(
            "Allow automatic submission with LLM answers to required free-text questions?",
            default=apply.auto_llm_answers,
        )
        document["apply"] = apply.model_dump(mode="json")
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        configured = cfg.with_config()

    candidate = cfg.resolved(cfg.candidate_path)
    if candidate == ROOT / "config/candidate.example.yaml":
        candidate = ROOT / "config/candidate.yaml"
        if not candidate.exists():
            shutil.copyfile(ROOT / "config/candidate.example.yaml", candidate)
        save_env(cfg, "BIO_CANDIDATE_PATH", "config/candidate.yaml")
    profile = load_profile(candidate)
    if _yes("Configure application contact details?", default=False):
        for field in (
            "first_name",
            "last_name",
            "email",
            "phone",
            "city",
            "linkedin",
            "github",
            "website",
        ):
            setattr(profile, field, _ask(field.replace("_", " ").title(), getattr(profile, field)))
        candidate.write_text(
            yaml.safe_dump(profile.model_dump(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    with connect(cfg.resolved(cfg.database_path)) as conn:
        migrate(conn)
        version = import_profile(conn, profile)
    console.print(
        f"Imported profile version {version} from {candidate}; reviewed={profile.reviewed}."
    )
    resume = configured.resolved(configured.resume_path)
    extracted_now = _resume_from_pdf(configured, resume)
    example = ROOT / "resume.example.md"
    if not resume.exists() and example.exists():
        resume.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, resume)
        console.print(f"Created {resume} from the example; replace it with your resume.")
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
    if passed and not extracted_now:
        _offer_extraction(configured, candidate, profile)
    from app.doctor import collect

    _rows, step = collect(cfg)
    console.print(f"Next step: {step}" if step else "Setup complete. Run uv run bio-jobs doctor.")
    return passed


def _resume_from_pdf(cfg: Settings, resume) -> bool:
    """Fill resume.md from the resume PDF's text; ask before replacing a real resume.md."""
    from app.resume import resume_status_text
    from app.resume_pdf import pdf_path, write_resume_md

    pdf = pdf_path(cfg)
    if not pdf.is_file():
        console.print(
            f"No resume PDF at {pdf}. Save your resume PDF there: applications attach it "
            "unchanged, and setup can fill resume.md from its text."
        )
        return False
    console.print(f"Resume PDF: {pdf} (attached to applications unchanged).")
    filled = resume_status_text(resume) == "filled"
    if filled and not _yes(f"Replace {resume} with text extracted from the PDF?"):
        return False
    try:
        write_resume_md(cfg, force=filled)
    except (ValueError, OSError) as exc:
        console.print(f"Could not extract the PDF text: {exc}")
        return False
    console.print(
        f"Wrote {resume} from the PDF. Check it against your PDF and correct anything the "
        "extraction got wrong, then run uv run bio-jobs extract-profile --force."
    )
    return True


def _current_llm(cfg: Settings) -> str | None:
    try:
        a = cfg.with_config().analyst
    except (ValueError, TypeError, OSError, yaml.YAMLError):
        return None
    from app.doctor import _describe

    summary = _describe(a)
    if a.fallback_backend:
        summary += f"; fallback {_describe(fallback_cfg(cfg.with_config()).analyst)}"
    return summary


def fallback_overrides(primary: Analyst, fallback: Analyst) -> dict:
    """Only the provider fields where the fallback differs from the primary."""
    excluded = {*SHARED, "backend", "fallback_backend", "fallback_settings"}
    chosen = fallback.model_dump(exclude=excluded)
    base = primary.model_dump(exclude=excluded)
    return {key: value for key, value in chosen.items() if value != base[key]}


def _offer_extraction(cfg: Settings, candidate, profile) -> None:
    """Offer extract-profile when the resume is filled but not reflected in the profile."""
    from app.resume import extract_to_file, resume_status

    state, detail = resume_status(cfg, profile)
    if state not in ("unextracted", "changed"):
        return
    console.print(detail + ".")
    if profile.reviewed:
        console.print(
            f"Extracting replaces the reviewed facts in {candidate} with unreviewed ones."
        )
    if not _yes(
        f"Extract your profile now? This sends the resume to {cfg.analyst.backend}.",
        default=not profile.reviewed,
    ):
        return
    console.print("Extracting…")
    try:
        with connect(cfg.resolved(cfg.database_path)) as conn:
            extracted, dropped = extract_to_file(cfg, conn, profile, candidate, force=True)
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
        console.print(
            f"Extraction failed: {exc}. Retry with uv run bio-jobs extract-profile --force"
        )
        return
    console.print(
        f"Extracted {len(extracted.facts)} facts for {extracted.name or 'unknown name'} into "
        f"{candidate}; dropped {dropped} without resume evidence."
    )

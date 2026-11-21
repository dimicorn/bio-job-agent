import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

Backend = Literal["codex", "claude_code", "api", "openai", "command", "opencode", "pi"]


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specialization: int = Field(default=30, ge=0, le=100)
    technical: int = Field(default=20, ge=0, le=100)
    seniority_leadership: int = Field(default=15, ge=0, le=100)
    industry_therapeutic: int = Field(default=10, ge=0, le=100)
    geography: int = Field(default=10, ge=0, le=100)
    research_outcomes: int = Field(default=10, ge=0, le=100)
    salary_arrangements: int = Field(default=5, ge=0, le=100)

    @model_validator(mode="after")
    def total(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError("analyst.weights must sum to 100")
        return self


class Analyst(BaseModel):
    """Provider settings; keys belong in .env, never in this model."""

    model_config = ConfigDict(extra="forbid")
    preferences: str = ""
    letter_style: str = ""
    letter_length: str = ""
    batch_size: int = Field(default=5, ge=1, le=50)
    parallel: int = Field(default=2, ge=1, le=16)
    min_score: int = Field(default=50, ge=0, le=100)
    weights: Weights = Field(default_factory=Weights)
    backend: Backend = "codex"
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    openai_extra: dict[str, Any] = Field(default_factory=dict)
    command: str | None = None
    codex_model: str | None = None
    codex_effort: str | None = None
    fallback_backend: Backend | None = None
    fallback_settings: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=120, gt=0, le=900)
    max_tokens: int = Field(default=12000, gt=0)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_posting_age_days: int | None = Field(default=7, ge=0)


class Apply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["manual", "assisted", "auto"] = "manual"
    auto_consent: bool = False
    auto_llm_answers: bool = False
    company_cooldown_days: int = Field(default=30, ge=0)
    daily_limit: int = Field(default=5, ge=1, le=1000)
    max_per_run: int = Field(default=5, ge=1, le=1000)
    min_score: int = Field(default=80, ge=0, le=100)
    active_hours: tuple[int, int] = (8, 23)
    answers_file: Path = Path("answers.yaml")
    profile_dir: Path = Path("data/browser-profile")
    audit_dir: Path = Path("debug/apply")
    submit_wait_minutes: float = Field(default=10, gt=0, le=120)
    confirmation_timeout_seconds: float = Field(default=45, gt=0, le=300)
    action_seconds: float = Field(default=0.3, ge=0, le=10)

    @model_validator(mode="after")
    def hours(self):
        start, end = self.active_hours
        if not 0 <= start < end <= 24:
            raise ValueError("apply.active_hours must be [start, end], 0 <= start < end <= 24")
        return self


class Watch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score: int | None = Field(default=None, ge=0, le=100)
    notify: bool = True
    telegram: bool = False
    digest_dir: Path = Path("digests")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIO_", env_file=ROOT / ".env", extra="ignore")
    database_path: Path = ROOT / "data/jobs.sqlite3"
    candidate_path: Path = ROOT / "config/candidate.example.yaml"
    resume_path: Path = ROOT / "resume.md"
    resume_pdf_path: Path = ROOT / "resume.pdf"
    sources_path: Path = ROOT / "config/sources.yaml"
    http_timeout: float = 20
    user_agent: str = "BioJobAgent/0.1 (local single-user job discovery)"
    config_path: Path = ROOT / "config.yaml"
    env_path: Path = ROOT / ".env"
    llm_state_dir: Path = ROOT / "data"
    analyst: Analyst = Field(default_factory=Analyst)
    search: Search = Field(default_factory=Search)
    apply: Apply = Field(default_factory=Apply)
    materials_path: Path = ROOT / "materials"
    watch: Watch = Field(default_factory=Watch)

    def resolved(self, path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path

    def with_config(self) -> "Settings":
        path = self.resolved(self.config_path)
        if not path.exists():
            raise ValueError("No LLM config. Run `uv run bio-jobs setup`.")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise TypeError("config.yaml must contain a mapping with an analyst section")
        analyst = Analyst.model_validate(data.get("analyst", {}))
        # Validate fallback overrides now, rather than waiting for a primary limit.
        Analyst.model_validate({**analyst.model_dump(), **analyst.fallback_settings})
        resume_path = Path(data.get("resume", self.resume_path))
        if not resume_path.is_absolute():
            resume_path = path.parent / resume_path
        resume_pdf_path = Path(data.get("resume_pdf", self.resume_pdf_path))
        if not resume_pdf_path.is_absolute():
            resume_pdf_path = path.parent / resume_pdf_path
        watch = Watch.model_validate(data.get("watch", {}))
        if not watch.digest_dir.is_absolute():
            watch.digest_dir = path.parent / watch.digest_dir
        apply = Apply.model_validate(data.get("apply", {}))
        for field in ("answers_file", "profile_dir", "audit_dir"):
            value = getattr(apply, field)
            if not value.is_absolute():
                setattr(apply, field, path.parent / value)
        return self.model_copy(
            update={
                "analyst": analyst,
                "resume_path": resume_path,
                "resume_pdf_path": resume_pdf_path,
                "apply": apply,
                "watch": watch,
                "search": Search.model_validate(data.get("search", {})),
            }
        )

    def secret(self, name: str | None) -> str:
        if not name:
            return ""
        return os.environ.get(name) or dotenv_values(self.resolved(self.env_path)).get(name) or ""

    def subprocess_env(self) -> dict[str, str]:
        values = dotenv_values(self.resolved(self.env_path))
        return {**{k: v for k, v in values.items() if v is not None}, **os.environ}

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

Backend = Literal["codex", "claude_code", "api", "openai", "command", "opencode", "pi"]


class Analyst(BaseModel):
    """Provider settings; keys belong in .env, never in this model."""

    model_config = ConfigDict(extra="forbid")
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
    max_tokens: int = Field(default=2048, gt=0)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIO_", env_file=ROOT / ".env", extra="ignore")
    database_path: Path = ROOT / "data/jobs.sqlite3"
    candidate_path: Path = ROOT / "config/candidate.example.yaml"
    sources_path: Path = ROOT / "config/sources.yaml"
    http_timeout: float = 20
    user_agent: str = "BioJobAgent/0.1 (local single-user job discovery)"
    config_path: Path = ROOT / "config.yaml"
    env_path: Path = ROOT / ".env"
    llm_state_dir: Path = ROOT / "data"
    analyst: Analyst = Field(default_factory=Analyst)

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
        return self.model_copy(update={"analyst": analyst})

    def secret(self, name: str | None) -> str:
        if not name:
            return ""
        return os.environ.get(name) or dotenv_values(self.resolved(self.env_path)).get(name) or ""

    def subprocess_env(self) -> dict[str, str]:
        values = dotenv_values(self.resolved(self.env_path))
        return {**{k: v for k, v in values.items() if v is not None}, **os.environ}

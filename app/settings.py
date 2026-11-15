from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIO_", env_file=ROOT / ".env", extra="ignore")
    database_path: Path = ROOT / "data/jobs.sqlite3"
    candidate_path: Path = ROOT / "config/candidate.example.yaml"
    sources_path: Path = ROOT / "config/sources.yaml"
    http_timeout: float = 20
    user_agent: str = "BioJobAgent/0.1 (local single-user job discovery)"

    def resolved(self, path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path

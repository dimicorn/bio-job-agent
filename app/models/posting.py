from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    adapter: Literal["greenhouse", "lever"]
    board: str
    employer: str
    industry: Literal["biotech", "biopharma", "pharma", "cro", "cdmo", "commercial_life_sciences"]
    industry_verified: bool = False
    employer_url: HttpUrl
    review_url: HttpUrl
    enabled: bool = True
    min_interval_seconds: int = 900

    @field_validator("board", "id")
    @classmethod
    def safe_slug(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            raise ValueError("Expected a public board slug")
        return value

    @field_validator("min_interval_seconds")
    @classmethod
    def interval(cls, value: int) -> int:
        if value < 60:
            raise ValueError("Minimum refresh interval is 60 seconds")
        return value


class Posting(BaseModel):
    external_id: str
    title: str
    employer: str
    location: str | None = None
    country: str | None = None
    remote_policy: str = "unknown"
    employment_type: str | None = None
    seniority: str | None = None
    specialties: list[str] = []
    description: str = ""
    requirements: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    work_authorization: str | None = None
    source_url: str
    apply_url: str
    posted_at: str | None = None
    deadline: str | None = None

    @field_validator("external_id", mode="before")
    @classmethod
    def valid_id(cls, value):
        if value is None or isinstance(value, bool):
            raise ValueError("Missing source ID")
        return str(value)

    @field_validator("external_id", "title", "employer")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Required field is empty")
        return value.strip()

    @field_validator("source_url", "apply_url")
    @classmethod
    def public_link(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ValueError("Expected a public HTTPS job link")
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            raise ValueError("LinkedIn is excluded")
        return value

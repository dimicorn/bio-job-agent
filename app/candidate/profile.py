import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    evidence: str = Field(min_length=1, description="Verbatim supporting excerpt from resume")


class Regions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    london: bool = True
    slough: bool = False
    stevenage: bool = False
    uxbridge: bool = False
    cambridge: bool = False
    oxford: bool = False


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    reviewed: bool = False
    resume_hash: str | None = None
    extraction_version: str | None = None
    facts: list[Fact] = []
    uncertainties: list[str] = []
    specialties: list[str] = []
    target_titles: list[str]
    regions: Regions = Regions()
    include_unknown_location: bool = True


def load_profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate(yaml.safe_load(path.read_text()))


def import_profile(conn, profile: CandidateProfile) -> int:
    payload = json.dumps(profile.model_dump(), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO candidate_profiles(content_hash, payload) VALUES (?, ?)",
        (digest, payload),
    )
    return conn.execute(
        "SELECT id FROM candidate_profiles WHERE content_hash=?", (digest,)
    ).fetchone()[0]

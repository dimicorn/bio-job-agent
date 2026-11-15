"""Reviewed employer watchlist and read-only board/identity verification."""

import json
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.posting import SourceConfig
from app.pipeline.normalize import key, text
from app.sources.base import PublicClient
from app.sources.registry import ADAPTERS

DEFAULT_WATCHLIST = Path(__file__).resolve().parents[2] / "firms.yaml"


class Firm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    ats: Literal["greenhouse", "lever", "ashby", "workable", "site"]
    token: str
    tier: int = Field(default=2, ge=1, le=3)
    industry: Literal["biotech", "biopharma", "pharma", "cro", "cdmo", "commercial_life_sciences"]
    employer_url: HttpUrl
    evidence_url: HttpUrl
    industry_evidence: str = Field(min_length=20)
    area: str = Field(min_length=2)
    review_url: HttpUrl
    verified_on: str
    links: str = ""
    enabled: bool = True

    def source(self):
        slug = "-".join(key(self.name).split())
        return SourceConfig(
            id=f"{slug}-{self.ats}",
            adapter=self.ats,
            board=self.token,
            links=self.links,
            employer=self.name,
            industry=self.industry,
            industry_verified=True,
            employer_url=self.employer_url,
            review_url=self.review_url,
            enabled=self.enabled,
        )


def load_firms(path: Path = DEFAULT_WATCHLIST) -> list[Firm]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("firms"), list):
        raise TypeError("Watchlist must contain a firms list")
    firms = [Firm.model_validate(row) for row in data["firms"]]
    names = [key(f.name) for f in firms]
    boards = [(f.ats, f.token) for f in firms]
    if len(names) != len(set(names)) or len(boards) != len(set(boards)):
        raise ValueError("Duplicate employer or board in watchlist")
    for firm in firms:
        firm.source()  # Validate board URL/slug and link patterns before networking.
    return firms


def _identity(expected: str, actual: str):
    # Ignore spacing/punctuation, but do not match a small substring of another name.
    if not key(expected) or f" {key(expected)} " not in f" {key(actual)} ":
        raise ValueError("Employer identity does not match the reviewed board")


def validate_firms(firms, *, timeout=20, user_agent="BioJobAgent/0.1", transport=None, pause=None):
    results = []
    with httpx.Client(timeout=timeout, transport=transport) as http:
        client = PublicClient(http, user_agent, **({"pause": pause} if pause else {}))
        for firm in firms:
            result = {"name": firm.name, "ats": firm.ats, "token": firm.token, "status": "error"}
            try:
                source = firm.source()
                adapter = ADAPTERS[firm.ats](source, client)
                raws = adapter.fetch()
                if firm.ats == "greenhouse":
                    metadata = client.get_json(
                        f"https://boards-api.greenhouse.io/v1/boards/{firm.token}"
                    )
                    _identity(firm.name, metadata.get("name") or "")
                elif firm.ats == "workable":
                    _identity(firm.name, adapter.board_name or "")
                elif firm.ats == "site":
                    # Check the actual read page, not our locally supplied employer field.
                    _identity(firm.name, json.dumps(raws))
                else:
                    # Lever/Ashby have no board-name field. Public job descriptions must
                    # identify the employer; an empty board requires its public board page.
                    material = " ".join(text(json.dumps(row)) for row in raws)
                    if key(firm.name) not in key(material):
                        material = text(client.get_text(str(firm.review_url)))
                    _identity(firm.name, material)
                for raw in raws:
                    adapter.parse(raw)
                result.update(status="ok", fetched=len(raws))
            except Exception as exc:  # noqa: BLE001 - report every board even after failures.
                result["error"] = f"{type(exc).__name__}: {exc}"[:1000]
            results.append(result)
    return results


def register_firms_command(subparsers):
    parser = subparsers.add_parser(
        "firms", help="List reviewed employers or reverify public boards"
    )
    parser.add_argument("--validate", action="store_true", help="Explicit live, robots-aware check")
    parser.add_argument("--path", type=Path, default=DEFAULT_WATCHLIST)


def run_firms_command(args, *, timeout=20, user_agent="BioJobAgent/0.1"):
    firms = load_firms(args.path)
    if args.validate:
        rows = validate_firms(firms, timeout=timeout, user_agent=user_agent)
    else:
        rows = [
            {
                "name": f.name,
                "ats": f.ats,
                "token": f.token,
                "tier": f.tier,
                "area": f.area,
                "enabled": f.enabled,
            }
            for f in firms
        ]
    print(json.dumps(rows, indent=2))
    return int(any(row.get("status") == "error" for row in rows))

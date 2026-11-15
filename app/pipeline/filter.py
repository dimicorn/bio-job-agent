import re

from app.candidate.profile import CandidateProfile
from app.models.posting import Posting, SourceConfig
from app.pipeline.normalize import key

# Specific borough names avoid accepting arbitrary UK/remote-only locations.
LONDON = (
    "london",
    "hammersmith",
    "white city",
    "camden",
    "westminster",
    "islington",
    "hackney",
    "southwark",
    "lambeth",
    "greenwich",
    "tower hamlets",
    "wandsworth",
    "wimbledon",
    "croydon",
    "bromley",
    "bexley",
    "barnet",
    "brent",
    "ealing",
    "enfield",
    "haringey",
    "harrow",
    "havering",
    "hillingdon",
    "hounslow",
    "kingston upon thames",
    "lewisham",
    "merton",
    "newham",
    "redbridge",
    "richmond upon thames",
    "sutton",
    "waltham forest",
    "barking",
    "dagenham",
    "kensington",
    "chelsea",
    "hammersmith and fulham",
)
OPTIONAL = {
    "slough": ("slough",),
    "stevenage": ("stevenage",),
    "uxbridge": ("uxbridge",),
    "cambridge": ("cambridge",),
    "oxford": ("oxford", "oxfordshire", "milton park"),
}


def contains(value: str, phrase: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", value))


def geography(job: Posting, profile: CandidateProfile) -> str:
    location = key(job.location)
    if job.country and job.country.upper() not in {"GB", "UK"}:
        return "outside"
    if not location or location in {
        "unknown",
        "remote",
        "hybrid",
        "united kingdom",
        "uk",
        "england",
    }:
        return "unknown"
    if not re.sub(
        r"\b(?:remote|hybrid|on site|united kingdom|great britain|england|uk|gb)\b", "", location
    ).strip():
        return "unknown"
    # Do not accept London, Ontario or Cambridge, Massachusetts.
    if any(
        contains(location, x)
        for x in (
            "ontario",
            "canada",
            "massachusetts",
            "connecticut",
            "united states",
            "usa",
            "new london",
        )
    ):
        return "outside"
    if re.search(
        r"\b(?:london|cambridge)\s*,\s*(?:on|ma|ct|ca)\b", job.location or "", re.IGNORECASE
    ):
        return "outside"
    enabled = profile.regions.model_dump()
    # Uxbridge is intentionally independent of the Greater London switch.
    for region, aliases in OPTIONAL.items():
        if any(contains(location, alias) for alias in aliases) and enabled[region]:
            return region
    if enabled["london"] and any(contains(location, x) for x in LONDON):
        # Explicitly labelled Uxbridge-only must obey its own switch.
        if contains(location, "uxbridge") and not enabled["uxbridge"]:
            return "outside"
        return "london"
    return "outside"


def exclusion(job: Posting, source: SourceConfig, profile: CandidateProfile) -> str | None:
    employer = key(job.employer)
    title = key(job.title)
    if not source.industry_verified:
        return "unverified_industry"
    if any(
        contains(employer, x) for x in ("university", "college", "institute of", "nhs", "hospital")
    ):
        return "academic_employer"
    if any(
        contains(title, x)
        for x in (
            "postdoc",
            "postdoctoral",
            "phd",
            "doctoral",
            "academic",
            "studentship",
            "professor",
            "lecturer",
            "faculty",
            "research fellow",
            "sales",
            "business development",
        )
    ):
        return "excluded_role"
    # Match title tokens in any order (e.g. Principal PD/MSAT Scientist).
    if not any(
        all(contains(title, token) for token in key(t).split())
        for t in profile.target_titles
        if key(t)
    ):
        return "seniority_or_role"
    geo = geography(job, profile)
    if geo == "outside" or (geo == "unknown" and not profile.include_unknown_location):
        return "geography"
    return None

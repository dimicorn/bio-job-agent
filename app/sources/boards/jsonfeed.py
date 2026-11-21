"""Official JSON Feed 1.1 job feeds whose items carry Schema.org `JobPosting` data.

Used for BiotechnologyJobs.co.uk, which publishes `/jobs.json` under CC BY 4.0 and lists
it as a public feed in robots.txt. The feed holds only the latest active jobs, so it is a
partial view: listings are never closed because they dropped out of it. Each posting names
its own employer; the discovery filters vet that employer, not the board.
"""

from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter

JSON_FEED = "https://jsonfeed.org/version/1"
MAX_ITEMS = 500
REMOTE = {"TELECOMMUTE": "remote"}
WORKPLACE_TAGS = {"remote": "remote", "hybrid": "hybrid", "on-site": "on-site"}
LEVEL_TAGS = ("entry", "mid", "senior", "lead", "director")


def _list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _place(place) -> tuple[str | None, str | None]:
    address = (place or {}).get("address") or {}
    if isinstance(address, str):
        return address.strip() or None, None
    parts = [address.get(k) for k in ("addressLocality", "addressRegion")]
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip()) or None, (
        country.strip().upper() if isinstance(country, str) and country.strip() else None
    )


def _salary(value):
    amount = value if isinstance(value, dict) else {}
    quantity = amount.get("value")
    if not isinstance(quantity, dict):
        return None, None, None
    low, high = quantity.get("minValue"), quantity.get("maxValue")
    if quantity.get("value") is not None:
        low = high = quantity["value"]
    # Only annual figures compare with the configured salary preferences.
    if str(quantity.get("unitText") or "YEAR").upper() != "YEAR":
        return None, None, None
    try:
        return (
            float(low) if low is not None else None,
            float(high) if high is not None else None,
            amount.get("currency"),
        )
    except (TypeError, ValueError):
        return None, None, None


class JsonFeed(Adapter):
    closes_missing = False
    per_job_employer = True

    @property
    def endpoint(self):
        return self.source.board

    def fetch(self):
        payload = self.client.get_json(self.endpoint)
        if (
            not isinstance(payload, dict)
            or not str(payload.get("version", "")).startswith(JSON_FEED)
            or not isinstance(payload.get("items"), list)
        ):
            raise TypeError("Invalid JSON Feed payload")
        if len(payload["items"]) > MAX_ITEMS:
            raise ValueError("JSON Feed item limit exceeded")
        return payload["items"]

    def parse(self, raw):
        posting = raw["_jobposting"]
        if posting.get("@type") != "JobPosting":
            raise ValueError("Feed item has no JobPosting data")
        # The feed documents item IDs as canonical job URLs, safe as deduplication keys.
        url = raw["id"]
        employer = text((posting.get("hiringOrganization") or {}).get("name") or raw["_company"])
        locations, countries = [], []
        for place in _list(posting.get("jobLocation")):
            location, country = _place(place)
            if location:
                locations.append(location)
            if country:
                countries.append(country)
        for requirement in _list(posting.get("applicantLocationRequirements")):
            name = (requirement or {}).get("name")
            if isinstance(name, str) and name.strip():
                countries.append(name.strip().upper())
        countries = list(dict.fromkeys(countries))
        tags = [str(t).casefold() for t in _list(raw.get("tags"))]
        remote = REMOTE.get(str(posting.get("jobLocationType") or "").upper()) or next(
            (WORKPLACE_TAGS[t] for t in tags if t in WORKPLACE_TAGS), "unknown"
        )
        salary_min, salary_max, currency = _salary(posting.get("baseSalary"))
        employment = posting.get("employmentType")
        return Posting(
            external_id=url,
            title=text(posting.get("title") or raw["title"]),
            employer=employer,
            location="; ".join(dict.fromkeys(locations)) or None,
            # A single explicit country only; several leave geography to the location text.
            country=countries[0] if len(countries) == 1 else None,
            remote_policy=remote,
            employment_type=", ".join(_list(employment)) or None,
            seniority=next((t for t in tags if t in LEVEL_TAGS), None),
            description=text(posting.get("description") or raw.get("summary")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            source_url=url,
            # Application links on the board (/apply/*, /redirect/*) are robots-disallowed;
            # the job page is where the person applies.
            apply_url=posting.get("url") or url,
            # validThrough is a rolling feed expiry, not an employer deadline.
            posted_at=posting.get("datePosted") or raw.get("date_published"),
        )

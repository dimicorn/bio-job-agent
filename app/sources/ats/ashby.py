"""Read-only Ashby Posting API; unlisted roles are excluded."""

from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter


class Ashby(Adapter):
    @property
    def endpoint(self):
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.source.board}"

    def fetch(self):
        payload = self.client.get_json(self.endpoint)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise TypeError("Invalid Ashby jobs payload")
        return [
            job for job in payload["jobs"] if not isinstance(job, dict) or job.get("isListed", True)
        ]

    def parse(self, raw):
        locations = [raw.get("location")] + [
            x.get("location") for x in raw.get("secondaryLocations") or []
        ]
        country = ((raw.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        # Ashby returns country names; the discovery filter consumes ISO codes.
        country = {"United Kingdom": "GB", "United States": "US"}.get(country, country)
        if country and len(country) != 2:
            country = None
        return Posting(
            external_id=raw["id"],
            title=text(raw["title"]),
            employer=self.source.employer,
            location="; ".join(dict.fromkeys(x for x in locations if x)) or None,
            country=country,
            description=text(raw.get("descriptionPlain") or raw.get("descriptionHtml")),
            remote_policy=raw.get("workplaceType")
            or ("remote" if raw.get("isRemote") else "unknown"),
            employment_type=raw.get("employmentType"),
            source_url=raw["jobUrl"],
            apply_url=raw.get("applyUrl") or raw["jobUrl"],
            posted_at=raw.get("publishedAt"),
        )

"""Public Workable widget: full descriptions in one bounded board request."""

from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter


class Workable(Adapter):
    @property
    def endpoint(self):
        return f"https://apply.workable.com/api/v1/widget/accounts/{self.source.board}?details=true"

    def fetch(self):
        payload = self.client.get_json(self.endpoint)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise TypeError("Invalid Workable jobs payload")
        self.board_name = payload.get("name")
        # Use a common raw id as well, so raw_postings and job_sources share provenance.
        return [
            dict(job, id=job.get("shortcode")) if isinstance(job, dict) else job
            for job in payload["jobs"]
        ]

    def parse(self, raw):
        locations = [loc for loc in raw.get("locations") or [] if not loc.get("hidden")]
        if not locations:
            locations = [
                raw.get("location") or {"city": raw.get("city"), "country": raw.get("country")}
            ]
        place = "; ".join(
            dict.fromkeys(
                ", ".join(loc[k] for k in ("city", "region", "country") if loc.get(k))
                for loc in locations
            )
        )
        url = (
            raw.get("url")
            or raw.get("shortlink")
            or (f"https://apply.workable.com/{self.source.board}/j/{raw['shortcode']}/")
        )
        return Posting(
            external_id=raw["shortcode"],
            title=text(raw["title"]),
            employer=self.source.employer,
            location=place or None,
            description=" ".join(
                filter(
                    None, [text(raw.get(k)) for k in ("description", "requirements", "benefits")]
                )
            ),
            requirements=text(raw.get("requirements")) or None,
            remote_policy="remote"
            if raw.get("telecommuting") in (True, "true", "True") or raw.get("remote") is True
            else "unknown",
            employment_type=raw.get("employment_type"),
            source_url=url,
            apply_url=raw.get("application_url")
            or (f"https://apply.workable.com/{self.source.board}/j/{raw['shortcode']}/apply/"),
            posted_at=raw.get("published_on") or raw.get("published"),
        )

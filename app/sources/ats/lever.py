from datetime import UTC, datetime

from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter


class Lever(Adapter):
    @property
    def endpoint(self):
        return f"https://api.lever.co/v0/postings/{self.source.board}"

    def fetch(self):
        jobs = []
        # Documented skip/limit pagination; bounded to avoid runaway feeds.
        for page in range(100):
            batch = self.client.get_json(f"{self.endpoint}?mode=json&skip={page * 100}&limit=100")
            if not isinstance(batch, list):
                raise TypeError("Invalid Lever jobs payload")
            jobs.extend(batch)
            if len(batch) < 100:
                return jobs
        raise ValueError("Lever pagination limit exceeded; refusing incomplete snapshot")

    def parse(self, raw):
        categories = raw.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location")]
        description = text(raw.get("descriptionPlain") or raw.get("description"))
        lists = raw.get("lists") or []
        requirements = (
            " ".join(text(x.get("content")) for x in lists if "requir" in x.get("text", "").lower())
            or None
        )
        description = " ".join(
            filter(
                None,
                [
                    description,
                    *[text(x.get("content")) for x in lists],
                    text(raw.get("additionalPlain") or raw.get("additional")),
                ],
            )
        )
        try:
            created = raw.get("createdAt")
            posted = (
                datetime.fromtimestamp(int(created) / 1000, UTC).isoformat()
                if created is not None
                else None
            )
        except (ValueError, TypeError, OverflowError, OSError):
            posted = None
        return Posting(
            posted_at=posted,
            external_id=raw["id"],
            title=text(raw["text"]),
            employer=self.source.employer,
            location="; ".join(x for x in locations if x) or None,
            country=raw.get("country"),
            description=description,
            requirements=requirements,
            remote_policy=raw.get("workplaceType") or "unknown",
            employment_type=categories.get("commitment"),
            source_url=raw["hostedUrl"],
            apply_url=raw["applyUrl"],
        )

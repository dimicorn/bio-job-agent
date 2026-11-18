from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter


class Greenhouse(Adapter):
    @property
    def endpoint(self):
        return f"https://boards-api.greenhouse.io/v1/boards/{self.source.board}/jobs?content=true"

    def fetch(self):
        payload = self.client.get_json(self.endpoint)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise TypeError("Invalid Greenhouse jobs payload")
        if (payload.get("meta") or {}).get("total", len(payload["jobs"])) > len(payload["jobs"]):
            raise ValueError("Incomplete Greenhouse snapshot")
        return payload["jobs"]

    def parse(self, raw):
        # updated_at is an edit timestamp, never a posting date.
        return Posting(
            posted_at=raw.get("first_published"),
            external_id=raw["id"],
            title=text(raw["title"]),
            employer=self.source.employer,
            location=raw.get("location", {}).get("name"),
            description=text(raw.get("content")),
            source_url=raw["absolute_url"],
            apply_url=raw["absolute_url"],
        )

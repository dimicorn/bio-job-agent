"""Public career pages only: JSON-LD roles, bounded same-host links, or change snapshots.

Snapshots are retained in raw_postings for manual review; their generic titles do not
masquerade as scientist vacancies. No Workday internal API, browser, or login is used.
"""

import hashlib
import json
import logging
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.models.posting import Posting
from app.pipeline.normalize import text
from app.sources.base import Adapter, RateLimited

LOG = logging.getLogger(__name__)
MAX_PAGES = 40


def job_links(html: str, base: str, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    links = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urldefrag(urljoin(base, anchor["href"]))[0]
        parts = urlsplit(url)
        if (
            parts.scheme == "https"
            and parts.netloc == urlsplit(base).netloc
            and not parts.username
            and not parts.password
            and rx.search(parts.path)
            and url.rstrip("/") != base.rstrip("/")
            and url not in links
        ):
            links.append(url)
    if len(links) > MAX_PAGES:
        raise ValueError("Career page link limit exceeded; narrow the links pattern")
    return links


def structured_jobs(html: str, url: str) -> list[dict]:
    jobs = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kinds = value.get("@type", [])
            if kinds == "JobPosting" or isinstance(kinds, list) and "JobPosting" in kinds:
                job_url = urljoin(url, value.get("url") or url)
                identifier = value.get("identifier")
                if isinstance(identifier, dict):
                    identifier = identifier.get("value")
                jobs.append(
                    {
                        "id": str(identifier or job_url),
                        "url": job_url,
                        "kind": "job",
                        "posting": value,
                    }
                )
            else:
                for key in ("@graph", "mainEntity", "itemListElement", "item"):
                    if key in value:
                        visit(value[key])

    for tag in BeautifulSoup(html, "html.parser").find_all("script", type="application/ld+json"):
        try:
            visit(json.loads(tag.string or tag.get_text()))
        except (TypeError, ValueError):
            continue
    return jobs


def snapshot(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    body = text(str(soup))
    if len(body) < 80:
        raise ValueError("Career page has no usable public text (possibly a JavaScript shell)")
    # Include destinations: a new Workday/opportunities link is meaningful even
    # when the link label stays the same. Never follow external job links.
    destinations = sorted(
        {
            urljoin(url, a["href"])
            for a in soup.find_all("a", href=True)
            if urlsplit(urljoin(url, a["href"])).scheme == "https"
        }
    )
    content = body + "\n" + "\n".join(destinations)
    digest = hashlib.sha256(content.encode()).hexdigest()[:20]
    return {
        "id": f"page-{digest}",
        "url": url,
        "kind": "career_page",
        "text": body,
        "links": destinations,
    }


class Site(Adapter):
    @property
    def endpoint(self):
        return self.source.board

    def fetch(self):
        self.complete = True
        html = self.client.get_text(self.endpoint)
        if not self.source.links:
            return structured_jobs(html, self.endpoint) or [snapshot(html, self.endpoint)]
        urls = job_links(html, self.endpoint, self.source.links)
        if not urls:
            raise ValueError("No career page links match the configured pattern")
        jobs = []
        for url in urls:
            try:
                page = self.client.get_text(url)
                jobs.extend(structured_jobs(page, url) or [snapshot(page, url)])
            except RateLimited:
                # Stop the entire source immediately so discovery persists Retry-After.
                raise
            except Exception as exc:  # noqa: BLE001 - one failed detail page is nonfatal.
                self.complete = False
                LOG.warning(
                    "career_page_failed source=%s error=%s", self.source.id, type(exc).__name__
                )
        if not jobs:
            raise ValueError("No career detail pages could be read")
        return jobs

    def parse(self, raw):
        if raw["kind"] == "career_page":
            return Posting(
                external_id=raw["id"],
                title=f"{self.source.employer}: career page update",
                employer=self.source.employer,
                description=raw["text"],
                source_url=raw["url"],
                apply_url=raw["url"],
            )
        job = raw["posting"]
        places = job.get("jobLocation") or []
        if isinstance(places, dict):
            places = [places]
        locations = []
        for place in places:
            address = place.get("address") or {}
            if isinstance(address, str):
                locations.append(address)
            else:
                locations.append(
                    ", ".join(
                        address[k]
                        for k in ("addressLocality", "addressRegion", "addressCountry")
                        if isinstance(address.get(k), str)
                    )
                )
        employment = job.get("employmentType")
        return Posting(
            external_id=raw["id"],
            title=text(job["title"]),
            employer=self.source.employer,
            location="; ".join(locations) or None,
            description=text(job.get("description")),
            employment_type=", ".join(employment) if isinstance(employment, list) else employment,
            source_url=raw["url"],
            apply_url=raw["url"],
            posted_at=job.get("datePosted"),
            deadline=job.get("validThrough"),
        )

"""Stage public feed leads for human employer review, never for automatic discovery."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.pipeline.normalize import key

ATTRIBUTION = "Biotechnology Jobs: https://biotechnologyjobs.co.uk/"
NOISE = re.compile(
    r"\b(?:university|college|hospital|nhs|recruitment|recruiter|staffing|"
    r"postdoc|postdoctoral|studentship|professor|lecturer|sales)\b",
    re.IGNORECASE,
)
ATS_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
}


def review_feed(data, firms):
    """Return unreviewed leads only; no network, database writes or employer approval."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise TypeError("Expected a JSON Feed with an items list")
    known = {key(f.name) for f in firms}
    seen = set()
    leads = []
    for item in data["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("_jobposting"), dict):
            continue
        job = item["_jobposting"]
        org = job.get("hiringOrganization")
        if not isinstance(org, dict):
            continue
        name, title = org.get("name"), job.get("title")
        if not isinstance(name, str) or not isinstance(title, str) or not key(name):
            continue
        if NOISE.search(name + " " + title):
            continue
        location = job.get("jobLocation")
        if not isinstance(location, dict) or not isinstance(location.get("address"), dict):
            continue
        address = location["address"]
        if str(address.get("addressCountry") or "").upper() not in {"GB", "UK"}:
            continue
        if not re.search(r"\blondon\b", str(address.get("addressLocality", "")), re.IGNORECASE):
            continue
        url = item.get("url")
        if (
            not isinstance(url, str)
            or urlsplit(url).hostname != "biotechnologyjobs.co.uk"
            or urlsplit(url).scheme != "https"
        ):
            continue
        identity = (key(name), key(title), url)
        if identity in seen:
            continue
        seen.add(identity)
        # Only explicit original links are candidates. Never follow feed redirects or apply links.
        candidates = [job.get("sameAs"), item.get("external_url")]
        ats_url = next(
            (
                u
                for u in candidates
                if isinstance(u, str)
                and urlsplit(u).scheme == "https"
                and urlsplit(u).hostname in ATS_HOSTS
                and not urlsplit(u).username
                and not urlsplit(u).password
            ),
            None,
        )
        leads.append(
            {
                "employer": name,
                "title": title,
                "feed_url": url,
                "original_ats_url": ats_url,
                "employer_review": "reviewed" if key(name) in known else "unreviewed",
            }
        )
    return leads


def register_feed_review_command(subparsers):
    parser = subparsers.add_parser(
        "feed-review", help="Review a locally obtained Biotechnology Jobs JSON feed"
    )
    parser.add_argument("feed", type=Path)
    parser.add_argument("--firms", type=Path, default=None)


def run_feed_review_command(args):
    from app.sources.firms import DEFAULT_WATCHLIST, load_firms

    data = json.loads(args.feed.read_text(encoding="utf-8"))
    leads = review_feed(data, load_firms(args.firms or DEFAULT_WATCHLIST))
    print(json.dumps({"attribution": ATTRIBUTION, "leads": leads}, indent=2))
    return 0

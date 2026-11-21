"""One job by link: ATS postings through the discovery adapters, any other page fetched once.

Everything read from a page is untrusted employer data: it is reduced to plain text and
never followed, executed or rendered.
"""

import re
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.models.posting import Posting, SourceConfig
from app.pipeline.normalize import canonical_url, text
from app.sources.ats.ashby import Ashby
from app.sources.ats.greenhouse import Greenhouse
from app.sources.ats.lever import Lever
from app.sources.ats.workable import Workable
from app.sources.base import AccessRestricted, RateLimited
from app.sources.site import Site, structured_jobs

MAX_PAGE_CHARS = 2_000_000
MAX_DESCRIPTION = 20_000
MIN_DESCRIPTION = 200
ATS = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "workable": Workable}
SLUG = re.compile(r"[A-Za-z0-9_-]+")


class LinkError(ValueError):
    """A plain failure message that already says what to do next."""


def detect(url: str) -> tuple[str, str, str] | None:
    """(adapter, board, posting id) for a public ATS job URL, or None for any other page."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    path = [p for p in parts.path.split("/") if p]
    query = parse_qs(parts.query)
    found = None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        if len(path) >= 3 and path[-2] == "jobs":
            found = "greenhouse", path[-3], path[-1]
        elif path[-1:] == ["job_app"] and query.get("for") and query.get("token"):
            found = "greenhouse", query["for"][0], query["token"][0]
    elif host == "jobs.lever.co" and len(path) >= 2:
        found = "lever", path[0], path[1]
    elif host == "jobs.ashbyhq.com" and len(path) >= 2:
        found = "ashby", path[0], path[1]
    elif host == "apply.workable.com" and "j" in path[1:-1]:
        index = path.index("j", 1)
        found = "workable", path[index - 1], path[index + 1]
    elif (
        host.endswith(".workable.com")
        and host != "apply.workable.com"
        and host.count(".") == 2
        and path[:1] == ["j"]
        and path[1:]
    ):
        found = "workable", host.split(".")[0], path[1]
    if found and all(SLUG.fullmatch(x) for x in found[1:]):
        return found
    return None


def lookup_urls(url: str) -> list[str]:
    """The link as given plus its posting page without an /apply or /application suffix."""
    urls = [url]
    stripped = re.sub(r"/(?:apply|application)/?$", "", urlsplit(url).path)
    if stripped != urlsplit(url).path:
        urls.append(url.replace(urlsplit(url).path, stripped, 1))
    return urls


def transient_source(adapter: str, board: str, url: str) -> SourceConfig:
    # Only lets the discovery adapter run; never stored, and its industry is never trusted.
    return SourceConfig(
        id=f"link-{adapter}",
        adapter=adapter,
        board=board,
        employer=board,
        industry="biotech",
        industry_verified=False,
        employer_url=url,
        review_url=url,
    )


def fetch_ats(client, adapter: str, board: str, posting_id: str, source: SourceConfig):
    """(posting, raw payload, employer name from the ATS or None)."""
    reader = ATS[adapter](source, client)
    if adapter == "greenhouse":
        raw = client.get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{posting_id}"
        )
    elif adapter == "lever":
        raw = client.get_json(f"https://api.lever.co/v0/postings/{board}/{posting_id}?mode=json")
    else:
        # Ashby and Workable have no public single-posting API: read the board once.
        raw = next(
            (
                x
                for x in reader.fetch()
                if isinstance(x, dict) and str(x.get("id")).casefold() == posting_id.casefold()
            ),
            None,
        )
        if raw is None:
            raise LinkError(
                f"Posting {posting_id} is not on the public {adapter} board '{board}' "
                "(closed or unlisted). Next: check the link in your browser."
            )
    if not isinstance(raw, dict):
        raise LinkError(f"The {adapter} API returned no posting. Next: check the link.")
    try:
        posting = reader.parse(raw)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LinkError(
            f"The {adapter} posting could not be read ({type(exc).__name__}). "
            "Next: check the link opens a job in your browser."
        ) from exc
    name = raw.get("company_name") if adapter == "greenhouse" else None
    if adapter == "workable":
        name = getattr(reader, "board_name", None)
    return posting, raw, name if isinstance(name, str) and name.strip() else None


def _organization(posting: dict) -> str | None:
    org = posting.get("hiringOrganization")
    name = org.get("name") if isinstance(org, dict) else org
    return text(name) if isinstance(name, str) and text(name) else None


def parse_page(html: str, url: str) -> tuple[Posting, dict, str | None]:
    """Title, employer, location and description from a public job page."""
    html = html[:MAX_PAGE_CHARS]
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    source = transient_source("site", url, url)
    jobs = structured_jobs(html, url)
    target = canonical_url(url)
    chosen = next((x for x in jobs if canonical_url(x["url"]) == target), jobs[0] if jobs else None)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    page_text = text(str(main))
    employer = None
    title = description = location = None
    fields = {}
    if chosen:
        try:
            # The page the person gave is the job's address, whatever URL the markup claims.
            structured = Site(source, None).parse(dict(chosen, url=url))
        except (ValueError, KeyError, TypeError, AttributeError):
            structured = None
        if structured:
            title, location = structured.title, structured.location
            description = structured.description
            fields = {
                "employment_type": structured.employment_type,
                "posted_at": structured.posted_at,
                "deadline": structured.deadline,
            }
        employer = _organization(chosen["posting"])
    if not title:
        meta = soup.find("meta", property="og:title")
        heading = soup.find("h1")
        title = (
            (text(meta.get("content")) if meta else "")
            or (text(heading.get_text(" ")) if heading else "")
            or (text(soup.title.get_text(" ")) if soup.title else "")
        )
    if not description or len(description) < MIN_DESCRIPTION:
        description = page_text
    if not employer:
        meta = soup.find("meta", property="og:site_name")
        employer = text(meta.get("content")) if meta and meta.get("content") else None
    if not title or len(description) < MIN_DESCRIPTION:
        raise LinkError(
            "No job text found on that page (it may need JavaScript or a sign-in). "
            "Next: open it in your browser and add the employer's ATS job link "
            "(Greenhouse, Lever, Ashby or Workable) instead."
        )
    posting = Posting(
        external_id="page:" + target,
        title=title[:300],
        employer=employer or host,
        location=location,
        description=description[:MAX_DESCRIPTION],
        source_url=url,
        apply_url=url,
        **fields,
    )
    raw = {
        "kind": "link_page",
        "url": url,
        "title": posting.title,
        "employer": employer,
        "location": location,
        "text": posting.description,
    }
    return posting, raw, employer


def fetch(client, url: str, sources) -> dict:
    """Read one posting. Returns posting, raw, adapter, board and any matching watchlist source."""
    try:
        Posting.public_link(url)
    except ValueError as exc:
        raise LinkError(f"{exc}. Next: paste the full https:// link to the job posting.") from exc
    host = urlsplit(url).hostname or url
    found = detect(url)
    try:
        if found:
            adapter, board, posting_id = found
            configured = next(
                (
                    s
                    for s in sources
                    if s.adapter == adapter and s.board.casefold() == board.casefold()
                ),
                None,
            )
            posting, raw, name = fetch_ats(
                client,
                adapter,
                board,
                posting_id,
                configured or transient_source(adapter, board, url),
            )
            return {
                "posting": posting,
                "raw": raw,
                "adapter": adapter,
                "board": board,
                "source": configured,
                "employer": configured.employer if configured else name,
            }
        posting, raw, name = parse_page(client.get_text(url), url)
        return {
            "posting": posting,
            "raw": raw,
            "adapter": "page",
            "board": host,
            "source": None,
            "employer": name,
        }
    except LinkError:
        raise
    except RateLimited as exc:
        raise LinkError(
            f"{host} asked for fewer requests (HTTP 429). "
            f"Next: try again in about {exc.seconds // 60} minutes."
        ) from exc
    except AccessRestricted as exc:
        if "robots" in str(exc):
            raise LinkError(
                f"{host} robots.txt disallows automated reading of that page, so it was not "
                "fetched. Next: open it in your browser, or add the employer's ATS job link instead."
            ) from exc
        raise LinkError(
            f"{host} refused or redirected the request ({exc}). Next: open the link in your "
            "browser and add the final job page address it shows."
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = "the posting may be closed" if status in (404, 410) else "the site returned an error"
        raise LinkError(
            f"{host} answered HTTP {status}; {hint}. Next: check the link in your browser."
        ) from exc
    except httpx.HTTPError as exc:
        raise LinkError(
            f"Could not reach {host} ({type(exc).__name__}). "
            "Next: check your connection and that the link opens, then try again."
        ) from exc
    except (TypeError, ValueError) as exc:
        raise LinkError(
            f"{host} returned an unexpected response ({type(exc).__name__}). "
            "Next: check the link opens a job in your browser."
        ) from exc

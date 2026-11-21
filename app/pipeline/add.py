"""`bio-jobs add <url>`: store one posting the person chose, marked as added by link.

Jobs added by link skip the watchlist requirement and the discovery filters; any filter
that would normally exclude the job is reported and shown in review instead.
"""

import hashlib
import json
import re
from datetime import UTC, datetime

import httpx

from app.pipeline.dedupe import store_job
from app.pipeline.discover import register_source
from app.pipeline.normalize import canonical_url, key
from app.sources import link
from app.sources.base import PublicClient

LINK_ADAPTER = "link"
DISPOSITION = "added_by_link"


def existing_job(conn, urls) -> int | None:
    """A stored job that already has this link as a posting or application address."""
    wanted = {canonical_url(u) for u in urls}
    for row in conn.execute("SELECT job_id,source_url,apply_url FROM job_sources ORDER BY id"):
        if canonical_url(row["source_url"]) in wanted or canonical_url(row["apply_url"]) in wanted:
            return row["job_id"]
    for row in conn.execute("SELECT id,canonical_url FROM jobs ORDER BY id"):
        if row["canonical_url"] in wanted:
            return row["id"]
    return None


def origin_id(adapter, board, posting_id) -> str:
    return f"{adapter}:{board}:{posting_id}".casefold()


def _linked(conn, external_id) -> int | None:
    row = conn.execute(
        """SELECT js.job_id FROM job_sources js JOIN sources s ON s.id=js.source_id
        WHERE s.adapter=? AND js.external_id=?""",
        (LINK_ADAPTER, external_id),
    ).fetchone()
    return row[0] if row else None


def _employer_id(conn, fetched, name) -> int:
    if fetched["source"]:
        return register_source(conn, fetched["source"])
    for row in conn.execute("SELECT id,name FROM employers ORDER BY id"):
        if key(row["name"]) == key(name):
            return row["id"]
    # Not on the watchlist: the industry stays unreviewed and is reported as such.
    return conn.execute(
        """INSERT INTO employers(name,industry,review_url,industry_verified)
        VALUES (?,'unreviewed',?,0)""",
        (name, fetched["posting"].source_url),
    ).lastrowid


def _link_source(conn, employer_id, name, now) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", key(name)).strip("-")[:60] or "page"
    source_id = f"link-{slug}"
    taken = conn.execute("SELECT employer_id FROM sources WHERE id=?", (source_id,)).fetchone()
    if taken and taken[0] != employer_id:
        source_id = f"link-{slug}-{employer_id}"
    conn.execute(
        """INSERT INTO sources(id,employer_id,adapter,board,config,last_checked_at)
        VALUES (?,?,?,'',?,?) ON CONFLICT(id) DO UPDATE SET last_checked_at=excluded.last_checked_at""",
        (
            source_id,
            employer_id,
            LINK_ADAPTER,
            json.dumps({"origin": DISPOSITION, "employer": name}),
            now,
        ),
    )
    return source_id


def add(conn, url, sources, *, employer=None, timeout=20, user_agent="BioJobAgent/0.1", **http):
    """Store one job from a link. Returns {"job_id", "status": new|existing|merged, ...}."""
    url = url.strip()
    found = link.detect(url)
    known = existing_job(conn, link.lookup_urls(url)) or _linked(
        conn, origin_id(*found) if found else "page:" + canonical_url(url)
    )
    if known:
        return {"job_id": known, "status": "existing"}
    transport, pause = http.get("transport"), http.get("pause")
    with httpx.Client(timeout=timeout, transport=transport) as client:
        public = PublicClient(client, user_agent, **({"pause": pause} if pause else {}))
        fetched = link.fetch(public, url, sources)
    posting = fetched["posting"]
    external_id = (
        posting.external_id
        if fetched["adapter"] == "page"
        else origin_id(fetched["adapter"], fetched["board"], posting.external_id)
    )
    known = existing_job(conn, [posting.source_url, posting.apply_url]) or _linked(
        conn, external_id
    )
    if known:
        return {"job_id": known, "status": "existing"}
    name = (employer or "").strip() or fetched["employer"] or _board_name(fetched["board"])
    posting = posting.model_copy(update={"employer": name, "external_id": external_id})
    now = datetime.now(UTC).isoformat()
    employer_id = _employer_id(conn, fetched, name)
    source_id = _link_source(conn, employer_id, name, now)
    payload = json.dumps(fetched["raw"], sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    conn.execute(
        """INSERT INTO raw_postings(source_id,external_id,content_hash,payload,fetched_at,
        disposition) VALUES (?,?,?,?,?,?) ON CONFLICT(source_id,external_id,content_hash)
        DO UPDATE SET fetched_at=excluded.fetched_at""",
        (source_id, external_id, digest, payload, now, DISPOSITION),
    )
    raw_id = conn.execute(
        "SELECT id FROM raw_postings WHERE source_id=? AND external_id=? AND content_hash=?",
        (source_id, external_id, digest),
    ).fetchone()[0]
    before = {r[0] for r in conn.execute("SELECT id FROM jobs")}
    job_id = store_job(conn, posting, source_id, employer_id, raw_id, now)
    conn.commit()
    return {
        "job_id": job_id,
        "status": "merged" if job_id in before else "new",
        "adapter": fetched["adapter"],
        "board": fetched["board"],
        "on_watchlist": fetched["source"] is not None,
    }


def _board_name(board: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", board) if w) or board

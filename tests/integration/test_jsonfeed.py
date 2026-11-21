import copy
import json
from pathlib import Path

import httpx
import pytest

from app.jobview import board_lines
from app.pipeline.discover import discover
from app.pipeline.filter import exclusion
from app.settings import ROOT
from app.sources.base import PublicClient
from app.sources.boards.jsonfeed import JsonFeed
from app.sources.config import load_sources
from app.store import list_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures"
FEED = json.loads((FIXTURES / "biotechnologyjobs.json").read_text())
ROBOTS = """User-agent: *
Disallow: /apply/*
Disallow: /redirect/*
# Public feeds (CC BY 4.0, attribution required): /feed.xml /jobs.json
"""


@pytest.fixture
def board():
    return next(
        s
        for s in load_sources(ROOT / "config/sources.yaml")
        if s.id == "biotechnologyjobs-jsonfeed"
    )


def handler(feed=FEED, robots=ROBOTS, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(str(request.url))
        if request.url.host == "biotechnologyjobs.co.uk":
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=robots)
            return httpx.Response(200, json=feed)
        source = "lever" if request.url.host == "api.lever.co" else "greenhouse"
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=(FIXTURES / f"{source}_robots.txt").read_text())
        return httpx.Response(200, json=json.loads((FIXTURES / f"{source}.json").read_text()))

    return handle


def parsed(board, feed=FEED):
    with httpx.Client(transport=httpx.MockTransport(handler(feed))) as http:
        adapter = JsonFeed(board, PublicClient(http, "BioJobAgent/0.1", pause=lambda _: None))
        return [adapter.parse(raw) for raw in adapter.fetch()]


def sync(conn, profile, sources, feed=FEED, seen=None):
    return discover(
        conn,
        profile,
        sources,
        transport=httpx.MockTransport(handler(feed, seen=seen)),
        pause=lambda _: None,
    )


def test_board_configured_enabled_hourly(board):
    assert board.enabled and board.industry_verified
    assert board.adapter == "jsonfeed"
    assert board.board == "https://biotechnologyjobs.co.uk/jobs.json"
    # The feed asks for at most one poll per hour.
    assert board.min_interval_seconds >= 3600


def test_recorded_fields_normalised(board):
    jobs = {job.employer: job for job in parsed(board)}
    lead = jobs["Isomorphic Labs"]
    assert lead.external_id == lead.source_url == lead.apply_url
    assert lead.source_url.startswith("https://biotechnologyjobs.co.uk/jobs/research-lead-")
    assert lead.title.startswith("Research Lead/ Senior Research Investigator")
    assert "·" not in lead.title  # The JobPosting title, not the feed's decorated one.
    assert lead.location == "London, Greater London" and lead.country == "GB"
    assert lead.posted_at == "2026-11-17T09:05:01+00:00"
    assert lead.remote_policy == "hybrid" and lead.seniority == "lead"
    assert lead.description.startswith("Synthetic test description.")
    assert "<p>" not in lead.description
    assert lead.deadline is None  # validThrough is a rolling feed expiry.
    agency = jobs["Bond Williams"]
    assert (agency.salary_min, agency.salary_max, agency.salary_currency) == (45000, 45000, "GBP")
    remote = jobs["Parexel"]
    assert remote.location is None and remote.country == "GB"
    assert remote.remote_policy == "remote"
    assert jobs["Recursion"].country == "US"


def test_board_filters_vet_each_employer(board, profile):
    reasons = {job.employer: exclusion(job, board, profile) for job in parsed(board)}
    assert reasons["Isomorphic Labs"] is None
    assert reasons["Genomics"] is None
    assert reasons["Bond Williams"] == "recruitment_agency"
    assert reasons["Imperial College London"] == "academic_employer"
    assert reasons["Recursion"] == "seniority_or_role"


@pytest.mark.parametrize(
    ("employer", "description", "reason"),
    [
        ("Wellcome Sanger Institute", "Genome research.", "academic_employer"),
        ("Example Foundation Trust", "Clinical research.", "academic_employer"),
        ("Example Talent Partners", "Lab role.", "recruitment_agency"),
        (
            "Example Bio",
            "We are recruiting on behalf of a client in Cambridge.",
            "recruitment_agency",
        ),
        ("Example Bio", "Our client is a growing biotech.", "recruitment_agency"),
        ("Example Bio", "Join our assay development team.", None),
    ],
)
def test_board_only_academic_and_agency_markers(board, profile, employer, description, reason):
    job = parsed(board)[1].model_copy(update={"employer": employer, "description": description})
    assert exclusion(job, board, profile) == reason
    # A reviewed employer's own ATS feed keeps the standard filters only.
    direct = board.model_copy(update={"adapter": "greenhouse", "board": "example"})
    assert exclusion(job, direct, profile) is None


def test_discovery_marks_origin_and_reuses_watchlist_employer(conn, profile, sources, board):
    seen = []
    result = sync(conn, profile, [*sources, board], seen=seen)
    status = {r["source"]: r for r in result}
    assert status["biotechnologyjobs-jsonfeed"]["status"] == "ok"
    assert status["biotechnologyjobs-jsonfeed"]["fetched"] == 6
    assert status["biotechnologyjobs-jsonfeed"]["accepted"] == 2
    assert status["biotechnologyjobs-jsonfeed"]["rejected"] == 4
    # Robots first, then the feed only: no job pages, apply or redirect links.
    board_urls = [u for u in seen if "biotechnologyjobs" in u]
    assert board_urls == [
        "https://biotechnologyjobs.co.uk/robots.txt",
        "https://biotechnologyjobs.co.uk/jobs.json",
    ]
    jobs = {j["employer"]: j for j in list_jobs(conn, profile, [*sources, board])}
    assert set(jobs) == {"Mytos", "Isomorphic Labs", "Genomics"}
    genomics = jobs["Genomics"]
    assert genomics["board_sources"] == ["biotechnologyjobs-jsonfeed"]
    assert not genomics["employer_reviewed"]
    assert "not on the reviewed watchlist" in board_lines(genomics)[0]
    row = conn.execute(
        "SELECT industry,industry_verified FROM employers WHERE name='Genomics'"
    ).fetchone()
    assert tuple(row) == ("unreviewed", 0)
    # The board posting from a watchlist employer shares that employer's reviewed record.
    isomorphic = [
        j for j in list_jobs(conn, profile, [*sources, board]) if j["employer"] == "Isomorphic Labs"
    ]
    assert len(isomorphic) == 2
    assert len({j["employer_id"] for j in isomorphic}) == 1
    on_board = next(j for j in isomorphic if j["board_sources"])
    assert on_board["employer_reviewed"]
    assert board_lines(on_board) == ["Also listed on job board biotechnologyjobs-jsonfeed."]
    direct = next(j for j in isomorphic if not j["board_sources"])
    assert board_lines(direct) == []


def test_partial_feed_never_closes_and_cooldown_holds(conn, profile, board):
    sync(conn, profile, [board])
    assert sync(conn, profile, [board])[0]["status"] == "cooldown"
    smaller = copy.deepcopy(FEED)
    smaller["items"] = smaller["items"][:1]
    for _ in range(3):
        conn.execute("UPDATE sources SET next_allowed_at=NULL")
        conn.commit()
        assert sync(conn, profile, [board], feed=smaller)[0]["status"] == "ok"
    # Dropping out of the latest-50 window is not evidence that a job closed.
    assert {j["employer"] for j in list_jobs(conn, profile, [board])} == {
        "Isomorphic Labs",
        "Genomics",
    }
    assert conn.execute("SELECT count(*) FROM jobs WHERE status='closed'").fetchone()[0] == 0


def test_robots_disallow_and_malformed_items(conn, profile, board):
    blocked = sync_robots(conn, profile, board, "User-agent: *\nDisallow: /jobs.json\n")
    assert blocked["status"] == "error" and "robots" in blocked["error"]
    broken = copy.deepcopy(FEED)
    del broken["items"][0]["_jobposting"]
    broken["items"][1]["_jobposting"]["@type"] = "Organization"
    conn.execute("UPDATE sources SET next_allowed_at=NULL")
    conn.commit()
    result = sync(conn, profile, [board], feed=broken)[0]
    assert result["status"] == "partial" and result["malformed"] == 2
    with pytest.raises(TypeError):
        parsed(board, feed={"version": "https://example.com", "items": []})


def sync_robots(conn, profile, board, robots):
    return discover(
        conn,
        profile,
        [board],
        transport=httpx.MockTransport(handler(robots=robots)),
        pause=lambda _: None,
    )[0]

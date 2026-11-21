import json
from pathlib import Path

import httpx
import pytest

from app.models.posting import SourceConfig
from app.pipeline.discover import discover
from app.sources.ats.ashby import Ashby
from app.sources.ats.workable import Workable
from app.sources.base import AccessRestricted, PublicClient, RateLimited
from app.sources.config import load_sources
from app.sources.firms import DEFAULT_WATCHLIST, load_firms, validate_firms
from app.sources.site import Site, job_links, snapshot, structured_jobs
from app.store import list_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures"


def config(sources, ats, **changes):
    data = sources[0].model_dump()
    data.update(id=f"example-{ats}", adapter=ats, board="example", employer="Example Bio")
    data.update(changes)
    return SourceConfig.model_validate(data)


def adapter(cls, source, handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return cls(source, PublicClient(http, "BioJobAgent/0.1", pause=lambda _: None)), http


def response_handler(payload):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 2")
        return httpx.Response(200, json=payload)

    return handle


def test_ashby_recorded_fields_and_country(sources):
    payload = json.loads((FIXTURES / "ashby.json").read_text())
    source = config(sources, "ashby", board="voleon", employer="Voleon")
    a, http = adapter(Ashby, source, response_handler(payload))
    with http:
        raws = a.fetch()
        job = a.parse(raws[0])
    assert job.external_id == "f9de4f71-c0f8-4de4-8f88-0ee8072d1e7a"
    assert job.location == "London" and job.country == "GB"
    assert job.remote_policy == "Hybrid"
    assert job.posted_at == "2026-04-17T00:52:06.526+00:00"
    assert job.apply_url.endswith("/application")
    assert job.salary_min is None


def test_ashby_unlisted_secondary_locations_and_missing_fields(sources):
    payload = json.loads((FIXTURES / "ashby.json").read_text())
    payload["jobs"][0]["isListed"] = False
    payload["jobs"][1]["secondaryLocations"] = [{"location": "Oxford"}, {"location": "London"}]
    a, http = adapter(Ashby, config(sources, "ashby"), response_handler(payload))
    with http:
        raws = a.fetch()
        assert len(raws) == 1
        assert a.parse(raws[0]).location == "London; Oxford"
        with pytest.raises(KeyError):
            a.parse({"id": "broken"})


def test_workable_recorded_fields_and_raw_id(sources):
    payload = json.loads((FIXTURES / "workable.json").read_text())
    a, http = adapter(
        Workable,
        config(sources, "workable", board="omass-therapeutics", employer="OMass Therapeutics"),
        response_handler(payload),
    )
    with http:
        raw = a.fetch()[0]
        job = a.parse(raw)
    assert raw["id"] == job.external_id == "DED18CF9F9"
    assert "Principal Scientist" in job.title and "Oxford" in job.location
    assert job.posted_at == "2026-10-29"
    assert job.apply_url == payload["jobs"][0]["application_url"]
    assert "<p>" not in job.description
    assert job.salary_min is None and job.work_authorization is None


def test_workable_locations_hidden_remote_and_requirements(sources):
    raw = json.loads((FIXTURES / "workable.json").read_text())["jobs"][0]
    raw.update(
        locations=[
            {"city": "London", "country": "United Kingdom"},
            {"city": "Secret location", "hidden": True},
        ],
        telecommuting="True",
        requirements="<p>Assay expertise</p>",
        application_url=None,
    )
    a, http = adapter(Workable, config(sources, "workable"), response_handler({"jobs": [raw]}))
    with http:
        job = a.parse(a.fetch()[0])
    assert job.location == "London, United Kingdom" and job.remote_policy == "remote"
    assert job.requirements == "Assay expertise"
    assert job.apply_url == "https://apply.workable.com/example/j/DED18CF9F9/apply/"


@pytest.mark.parametrize("cls,ats", [(Ashby, "ashby"), (Workable, "workable")])
@pytest.mark.parametrize("payload", [{}, {"jobs": {}}, []])
def test_api_invalid_payload_is_failure(sources, cls, ats, payload):
    a, http = adapter(cls, config(sources, ats), response_handler(payload))
    with http, pytest.raises(TypeError):
        a.fetch()


def test_site_recorded_snapshot_hash_and_no_invented_role(sources):
    html = (FIXTURES / "site.html").read_text()
    raw = snapshot(html, "https://www.rxcelerate.com/careers/")
    unchanged = snapshot(
        html.replace("<body>", "<body><script>changing telemetry</script>"), raw["url"]
    )
    assert raw["id"] == unchanged["id"]
    assert snapshot(html.replace("No Vacancies", "New opening"), raw["url"])["id"] != raw["id"]
    a, http = adapter(
        Site,
        config(sources, "site", board=raw["url"], employer="RxCelerate"),
        lambda r: httpx.Response(
            200, text="User-agent: *\nAllow: /" if r.url.path == "/robots.txt" else html
        ),
    )
    with http:
        job = a.parse(a.fetch()[0])
    assert job.title == "RxCelerate: career page update" and job.location is None
    assert job.source_url == raw["url"] and job.posted_at is None
    with pytest.raises(ValueError, match="JavaScript shell"):
        snapshot("<html>Enable JavaScript</html>", raw["url"])


def test_site_links_bounded_same_host_and_canonical():
    html = """<a href="/jobs/one#apply">One</a><a href="/jobs/one">Duplicate</a>
              <a href="https://other.example/jobs/two">External</a>
              <a href="http://bio.example/jobs/three">Insecure</a>
              <a href="/about">About</a>"""
    assert job_links(html, "https://bio.example/careers", "^/jobs/") == [
        "https://bio.example/jobs/one"
    ]
    with pytest.raises(ValueError, match="limit"):
        job_links(
            "".join(f'<a href="/jobs/{i}">Job</a>' for i in range(41)),
            "https://bio.example/careers",
            "^/jobs/",
        )


def test_site_jsonld_graph_and_provenance(sources):
    job = {
        "@type": "JobPosting",
        "identifier": {"value": "bio-1"},
        "title": "Principal Scientist",
        "description": "<p>Assay discovery</p>",
        "jobLocation": {
            "address": {"addressLocality": "London", "addressCountry": "United Kingdom"}
        },
        "datePosted": "2026-11-01",
        "url": "/jobs/bio-1",
    }
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [job]}) + "</script>"
    raws = structured_jobs(html, "https://bio.example/careers")
    a, http = adapter(
        Site, config(sources, "site", board="https://bio.example/careers"), response_handler({})
    )
    with http:
        parsed = a.parse(raws[0])
    assert parsed.external_id == "bio-1" and parsed.location == "London, United Kingdom"
    assert parsed.source_url == "https://bio.example/jobs/bio-1"
    assert parsed.posted_at == "2026-11-01" and parsed.description == "Assay discovery"


def test_site_detail_failure_nonfatal_and_robots_checked(sources):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /jobs/private")
        if request.url.path == "/careers":
            return httpx.Response(
                200, text='<a href="/jobs/private">One</a><a href="/jobs/public">Two</a>'
            )
        return httpx.Response(200, text=(FIXTURES / "site.html").read_text())

    a, http = adapter(
        Site, config(sources, "site", board="https://bio.example/careers", links="^/jobs/"), handler
    )
    with http:
        assert len(a.fetch()) == 1
    assert "https://bio.example/jobs/private" not in requested
    assert requested.count("https://bio.example/robots.txt") == 1


def test_site_rate_limit_stops_details_and_persists_cooldown(conn, profile, sources):
    source = config(sources, "site", board="https://bio.example/careers", links="^/jobs/")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/careers":
            return httpx.Response(
                200, text='<a href="/jobs/one">One</a><a href="/jobs/two">Two</a>'
            )
        return httpx.Response(429, headers={"Retry-After": "3600"})

    transport = httpx.MockTransport(handler)
    rows = discover(conn, profile, [source], transport=transport, pause=lambda _: None)
    assert rows[0]["status"] == "error" and "3600" in rows[0]["error"]
    assert "https://bio.example/jobs/two" not in calls
    assert discover(conn, profile, [source], transport=transport)[0]["status"] == "cooldown"
    assert len(calls) == 3


def test_site_change_snapshots_keep_raw_provenance(conn, profile, sources):
    source = config(sources, "site", board="https://bio.example/careers")
    html = (FIXTURES / "site.html").read_text()

    def handler(request):
        return httpx.Response(
            200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else html
        )

    transport = httpx.MockTransport(handler)
    for _ in range(2):
        rows = discover(conn, profile, [source], transport=transport, pause=lambda _: None)
        assert rows[0]["status"] == "ok" and rows[0]["rejected"] == 1
        conn.execute("UPDATE sources SET next_allowed_at=NULL")
        conn.commit()
    assert conn.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 1
    html = html.replace("No Vacancies", "New opening")
    discover(conn, profile, [source], transport=transport, pause=lambda _: None)
    assert conn.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 2
    assert not list_jobs(conn, profile, [source])


@pytest.mark.parametrize("ats,cls", [("ashby", Ashby), ("workable", Workable), ("site", Site)])
@pytest.mark.parametrize(
    "status,exc",
    [(302, AccessRestricted), (429, RateLimited)],
)
def test_restrictions_stop_each_new_adapter(sources, ats, cls, status, exc):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://example.com/login"})

    source = config(
        sources, ats, **({"board": "https://bio.example/careers"} if ats == "site" else {})
    )
    a, http = adapter(cls, source, handler)
    with http, pytest.raises(exc):
        a.fetch()
    assert len(calls) == 1  # No retries or redirects, including a denied robots response.


def test_new_adapter_failure_continues_and_workable_provenance(conn, profile, sources):
    ashby = config(sources, "ashby")
    workable = config(
        sources, "workable", employer="OMass Therapeutics", board="omass-therapeutics"
    )
    payload = json.loads((FIXTURES / "workable.json").read_text())
    profile.regions.oxford = True

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(503)
        return httpx.Response(200, json=payload)

    result = discover(
        conn,
        profile,
        [ashby, workable],
        transport=httpx.MockTransport(handler),
        pause=lambda _: None,
    )
    assert [r["status"] for r in result] == ["error", "ok"]
    jobs = list_jobs(conn, profile, [ashby, workable])
    assert len(jobs) == 1
    assert jobs[0]["origins"][0]["external_id"] == "DED18CF9F9"
    assert conn.execute("SELECT external_id FROM raw_postings").fetchone()[0] == "DED18CF9F9"


def test_watchlist_loads_and_preserves_explicit_boards():
    firms = load_firms()
    sources = load_sources(DEFAULT_WATCHLIST.parent / "config/sources.yaml")
    assert len(firms) == 28
    assert {f.ats for f in firms} == {"greenhouse", "lever", "workable", "site", "ashby"}
    assert len({(s.adapter, s.board) for s in sources}) == len(sources)
    assert sources[0].id == "mytos-lever"
    assert not next(s for s in sources if s.board == "recursionpharmaceuticals").enabled
    assert all(f.industry_evidence and f.verified_on for f in firms)


def test_validation_identity_empty_board_and_nonfatal_failures():
    base = next(f for f in load_firms() if f.ats == "workable")
    firms = [
        base.model_copy(update={"token": "wrong"}),
        base.model_copy(update={"token": "empty"}),
        base.model_copy(update={"token": "failure"}),
    ]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        token = request.url.path.rsplit("/", 1)[-1]
        if token == "failure":
            return httpx.Response(503)
        return httpx.Response(
            200, json={"name": "Wrong Employer" if token == "wrong" else base.name, "jobs": []}
        )

    rows = validate_firms(firms, transport=httpx.MockTransport(handler), pause=lambda _: None)
    assert [r["status"] for r in rows] == ["error", "ok", "error"]
    assert rows[1]["fetched"] == 0
    assert "identity" in rows[0]["error"]


def test_firms_cli_works_without_profile_or_database(monkeypatch, capsys):
    from app import cli

    monkeypatch.setattr("sys.argv", ["bio-jobs", "firms"])
    monkeypatch.setattr(cli, "connect", lambda *a: pytest.fail("firms must not open database"))
    assert cli.main() == 0
    assert len(json.loads(capsys.readouterr().out)) == 28


@pytest.mark.parametrize(
    "ats,board",
    [("ashby", "bad/path"), ("site", "http://bio.example"), ("site", "https://linkedin.com/jobs")],
)
def test_source_config_rejects_invalid_boards(sources, ats, board):
    with pytest.raises(ValueError):
        config(sources, ats, board=board)


@pytest.mark.live
def test_live_watchlist_validation(request):
    if not request.config.getoption("--live"):
        pytest.skip("Use --live -m live for real network access")
    rows = validate_firms(load_firms())
    assert rows and all(row["status"] == "ok" for row in rows), rows


@pytest.mark.parametrize("status", [401, 403, 404])
def test_unavailable_robots_allows_job_request(status):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status if request.url.path == "/robots.txt" else 200, text="{}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = PublicClient(http, "TestAgent", pause=lambda _: None)
        assert client.get_json("https://example.com/jobs") == {}
        assert client.delays["example.com"] >= 1
    assert calls == ["/robots.txt", "/jobs"]


@pytest.mark.parametrize(
    "status,exc", [(429, RateLimited), (500, httpx.HTTPStatusError), (503, httpx.HTTPStatusError)]
)
def test_unreachable_robots_stops_job_request(status, exc):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, pytest.raises(exc):
        PublicClient(http, "TestAgent", pause=lambda _: None).get("https://example.com/jobs")
    assert calls == ["/robots.txt"]


@pytest.mark.parametrize("status", [401, 403])
def test_job_endpoint_access_restrictions_still_stop(status):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/robots.txt" else status)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        pytest.raises(AccessRestricted),
    ):
        PublicClient(http, "TestAgent", pause=lambda _: None).get("https://example.com/jobs")
    assert calls == ["/robots.txt", "/jobs"]

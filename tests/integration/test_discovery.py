import json
from pathlib import Path

import httpx
import pytest

from app.candidate.profile import import_profile
from app.db import migrate
from app.pipeline.discover import discover
from app.store import export_csv, list_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures"


def sync(conn, profile, sources, transport):
    return discover(conn, profile, sources, transport=transport, pause=lambda _: None)


def expire(conn):
    conn.execute("UPDATE sources SET next_allowed_at=NULL")
    conn.commit()


def test_fixture_discovery_provenance_and_idempotency(conn, profile, sources, transport):
    result = sync(conn, profile, sources, transport)
    assert [r["status"] for r in result] == ["ok", "ok"]
    assert [r["accepted"] for r in result] == [1, 1]
    jobs = list_jobs(conn, profile, sources)
    assert len(jobs) == 2
    assert {j["employer"] for j in jobs} == {"Mytos", "Isomorphic Labs"}
    assert all(j["origins"] and j["apply_url"].startswith("https://") for j in jobs)
    assert all(j["posted_at"] is None for j in jobs)  # Never relabel updated_at.
    assert all(j["salary_min"] is None and j["work_authorization"] is None for j in jobs)
    assert conn.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 4
    expire(conn)
    sync(conn, profile, sources, transport)
    assert len(list_jobs(conn, profile, sources)) == 2
    assert conn.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 4
    assert len(list_jobs(conn, profile, [sources[0]])) == 1


def test_duplicates_merge_preserving_all_origins(conn, profile, sources, transport):
    second = sources[0].model_copy(update={"id": "mytos-second"})
    sync(conn, profile, [sources[0], second], transport)
    jobs = list_jobs(conn, profile, [sources[0], second])
    assert len(jobs) == 1
    assert len(jobs[0]["origins"]) == 2
    assert {o["source_id"] for o in jobs[0]["origins"]} == {sources[0].id, second.id}


def test_description_similarity_dedup(conn, profile, sources):
    data = json.loads((FIXTURES / "lever.json").read_text())[:]
    principal = next(x for x in data if "Principal" in x["text"])
    other = {**principal, "id": "second-id", "hostedUrl": "https://jobs.lever.co/mytos/second-id"}

    def handler(request):
        return (
            httpx.Response(200, text="User-agent: *\nAllow: /")
            if request.url.path == "/robots.txt"
            else httpx.Response(200, json=[principal, other])
        )

    sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM job_sources").fetchone()[0] == 2


def test_failure_nonfatal_and_health_persisted(conn, profile, sources, transport):
    def handler(request):
        if request.url.host == "api.lever.co":
            return httpx.Response(503)
        return transport.handle_request(request)

    result = sync(conn, profile, sources, httpx.MockTransport(handler))
    assert [r["status"] for r in result] == ["error", "ok"]
    assert len(list_jobs(conn, profile, sources)) == 1
    assert conn.execute("SELECT last_error FROM sources WHERE id=?", (sources[0].id,)).fetchone()[0]
    assert (
        conn.execute("SELECT count(*) FROM sync_runs WHERE finished_at IS NOT NULL").fetchone()[0]
        == 2
    )


def test_timeout_nonfatal(conn, profile, sources, transport):
    def handler(request):
        if request.url.host == "api.lever.co":
            raise httpx.ReadTimeout("fixture timeout")
        return transport.handle_request(request)

    result = sync(conn, profile, sources, httpx.MockTransport(handler))
    assert [r["status"] for r in result] == ["error", "ok"]


def test_robots_restrictions_and_no_retry(conn, profile, sources):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /")

    result = sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert result[0]["status"] == "error"
    assert requests == ["https://api.lever.co/robots.txt"]


def test_rate_limit_and_persistent_cooldown(conn, profile, sources):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(429, headers={"Retry-After": "3600"})

    result = sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert "3600" in result[0]["error"]
    assert len(requests) == 2
    assert (
        sync(conn, profile, [sources[0]], httpx.MockTransport(handler))[0]["status"] == "cooldown"
    )
    assert len(requests) == 2


def test_malformed_record_nonfatal(conn, profile, sources):
    data = json.loads((FIXTURES / "lever.json").read_text())

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, json=[{"id": "broken"}, None, *data])

    result = sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert result[0]["status"] == "partial"
    assert result[0]["malformed"] == 2
    assert len(list_jobs(conn, profile, sources)) == 1


def test_rejected_role_update_hides_old_job(conn, profile, sources, transport):
    sync(conn, profile, sources, transport)
    expire(conn)
    data = json.loads((FIXTURES / "lever.json").read_text())
    for job in data:
        job["categories"]["location"] = "Boston, USA"
        job["categories"]["allLocations"] = ["Boston, USA"]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, json=data)

    sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert all(j["employer"] != "Mytos" for j in list_jobs(conn, profile, sources))


def test_preference_changes_refilter(conn, profile, sources, transport):
    sync(conn, profile, sources, transport)
    profile.regions.london = False
    assert not list_jobs(conn, profile, sources)


def test_schema_profile_versions_and_csv(conn, profile, sources, transport):
    migrate(conn)
    v1 = import_profile(conn, profile)
    assert import_profile(conn, profile) == v1
    profile.reviewed = True
    assert import_profile(conn, profile) != v1
    sync(conn, profile, sources, transport)
    jobs = list_jobs(conn, profile, sources)
    jobs[0]["title"] = ' =HYPERLINK("evil")'
    csv = export_csv(jobs)
    assert "provenance" in csv and "mytos-lever" in csv and "'=HYPERLINK" not in csv
    assert "' =HYPERLINK" in csv
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("status", [401, 403, 302])
def test_restricted_access_never_followed(conn, profile, sources, status):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://example.com/login"})

    result = sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert result[0]["status"] == "error"
    assert len(calls) == 1


def test_distinct_roles_do_not_merge(conn, profile, sources):
    principal = next(
        x for x in json.loads((FIXTURES / "lever.json").read_text()) if "Principal" in x["text"]
    )
    other = {
        **principal,
        "id": "second-id",
        "hostedUrl": "https://jobs.lever.co/mytos/second-id",
        "descriptionPlain": "An entirely different clinical role requiring operations leadership and managing pharmaceutical studies. "
        * 4,
        "description": "",
        "lists": [],
    }

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(200, json=[principal, other])

    sync(conn, profile, [sources[0]], httpx.MockTransport(handler))
    assert len(list_jobs(conn, profile, sources)) == 2

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import applications, autopilot
from app.pipeline.discover import discover
from app.settings import Settings
from app.sources.ats.greenhouse import Greenhouse
from app.sources.ats.lever import Lever
from app.store import list_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_adapter_dates(sources):
    greenhouse = json.loads((FIXTURES / "greenhouse.json").read_text())["jobs"][0]
    lever = json.loads((FIXTURES / "lever.json").read_text())[0]
    assert (
        Greenhouse(sources[1], None)
        .parse({**greenhouse, "first_published": "2026-01-02T00:00:00Z"})
        .posted_at
        == "2026-01-02T00:00:00Z"
    )
    assert Greenhouse(sources[1], None).parse(greenhouse).posted_at is None
    assert (
        Lever(sources[0], None)
        .parse({**lever, "createdAt": 1767312000000})
        .posted_at.startswith("2026-01-02")
    )
    assert Lever(sources[0], None).parse({**lever, "createdAt": "invalid"}).posted_at is None


def test_freshness_provenance_and_off(conn, profile, sources, transport):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    cfg = Settings()
    now = datetime.now(UTC)
    job = list_jobs(conn, profile, sources, cfg)[0]
    origin = job["origins"][0]
    old = (now - timedelta(days=8)).isoformat()
    conn.execute("UPDATE job_sources SET first_seen_at=? WHERE id=?", (old, origin["id"]))
    assert (
        next(x for x in list_jobs(conn, profile, sources, cfg) if x["id"] == job["id"])[
            "date_label"
        ]
        == "posting date unknown"
    )
    conn.execute("UPDATE job_sources SET first_seen_reliable=1 WHERE id=?", (origin["id"],))
    assert job["id"] not in [x["id"] for x in list_jobs(conn, profile, sources, cfg)]
    cfg.search.max_posting_age_days = 0
    assert (
        "first seen"
        in next(x for x in list_jobs(conn, profile, sources, cfg) if x["id"] == job["id"])[
            "date_label"
        ]
    )
    cfg.search.max_posting_age_days = 7
    payload = json.loads(
        conn.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
    )
    payload["posted_at"] = now.isoformat()
    conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"]))
    assert next(x for x in list_jobs(conn, profile, sources, cfg) if x["id"] == job["id"])[
        "date_label"
    ].startswith("posted")
    payload["posted_at"] = old
    conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"]))
    assert job["id"] not in [x["id"] for x in list_jobs(conn, profile, sources, cfg)]
    cfg.search.max_posting_age_days = None
    assert job["id"] in [x["id"] for x in list_jobs(conn, profile, sources, cfg)]


@pytest.mark.parametrize(
    "state,counts",
    [
        ("submitted", True),
        ("submitting", True),
        ("unknown_result", True),
        ("withdrawn", False),
        ("manual_required", False),
    ],
)
def test_company_states_and_expiry(conn, profile, sources, transport, state, counts):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    row = list_jobs(conn, profile, sources)[0]
    now = datetime.now(UTC)
    conn.execute(
        "INSERT INTO applications(job_id,state,updated_at) VALUES (?,?,?)",
        (row["id"], state, now.isoformat()),
    )
    assert bool(applications.company_cooldown(conn, row["employer"].upper(), 30, now=now)) == counts
    assert (
        applications.company_cooldown(conn, row["employer"], 30, now=now + timedelta(days=30))
        is None
    )


@pytest.mark.parametrize("mode", ["manual", "assisted", "auto"])
def test_all_modes_refuse_company(conn, profile, sources, transport, mode):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    row = list_jobs(conn, profile, sources)[0]
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'submitted')", (row["id"],))
    cfg = Settings()
    cfg.apply.mode = mode
    other = next(x for x in list_jobs(conn, profile, sources) if x["id"] != row["id"])
    other.update(employer=row["employer"].upper(), score=100)
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'approved')", (other["id"],))
    with pytest.raises(ValueError, match="company cooldown until"):
        autopilot.run(cfg, conn, profile, [other], job=other["id"])


def test_run_picks_best_per_company(conn, monkeypatch):
    cfg = Settings()
    cfg.apply.mode = "manual"
    cfg.apply.active_hours = (0, 24)
    rows = [
        {"id": 1, "employer": "ACME", "score": 85, "apply_url": "https://example.com/1"},
        {"id": 2, "employer": "Acme", "score": 95, "apply_url": "https://example.com/2"},
    ]
    monkeypatch.setattr(applications, "check_gates", lambda *a, **k: None)
    monkeypatch.setattr(applications, "validate_materials", lambda *a, **k: ([], {}))
    monkeypatch.setattr(autopilot.answers, "load_answers", lambda *a: {})
    monkeypatch.setattr(autopilot, "event", lambda *a, **k: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    # Use a lightweight queue connection; company lookup is separately exercised above.
    monkeypatch.setattr(applications, "check_company", lambda *a, **k: None)

    class Queue:
        def execute(self, *args):
            return self

        def fetchone(self):
            return {
                "acknowledged_warnings": "{}",
                "material_ids": "{}",
                "answers_hash": autopilot.digest({}),
            }

    assert autopilot.run(cfg, Queue(), None, rows, output=lambda _: None) == ["manual"]
    assert opened == ["https://example.com/2"]


def test_cooldown_matches_employer_across_sources(conn, profile, sources, transport):
    other_source = sources[1].model_copy(update={"employer": sources[0].employer.upper()})
    discover(conn, profile, [sources[0], other_source], transport=transport, pause=lambda _: None)
    rows = list_jobs(conn, profile, [sources[0], other_source])
    assert len(rows) == 2
    conn.execute("INSERT INTO applications(job_id,state) VALUES (?, 'submitted')", (rows[0]["id"],))
    with pytest.raises(ValueError, match="company cooldown until"):
        applications.check_company(Settings(), conn, rows[1])


def test_ambiguous_attempt_still_counts_after_manual_stop(conn, profile, sources, transport):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    row = list_jobs(conn, profile, sources)[0]
    conn.execute(
        "INSERT INTO applications(job_id,state) VALUES (?, 'manual_required')", (row["id"],)
    )
    conn.execute(
        "INSERT INTO application_attempts(job_id,mode,submission_possible,started_at) VALUES (?, 'auto', 1, ?)",
        (row["id"], datetime.now(UTC).isoformat()),
    )
    assert applications.company_cooldown(conn, row["employer"], 30)


def test_saved_stale_job_remains_visible_but_not_a_prospect(conn, profile, sources, transport):
    from app.store import review_job

    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    cfg = Settings()
    row = list_jobs(conn, profile, sources, cfg)[0]
    payload = json.loads(
        conn.execute("SELECT payload FROM jobs WHERE id=?", (row["id"],)).fetchone()[0]
    )
    payload["posted_at"] = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    review_job(conn, row["id"], "saved")
    visible = next(r for r in list_jobs(conn, profile, sources, cfg) if r["id"] == row["id"])
    assert "[stale]" in visible["date_label"]
    assert row["id"] not in {
        r["id"] for r in list_jobs(conn, profile, sources, cfg, prospects_only=True)
    }
def test_rate_limit_never_shortens_reservation(conn, profile, sources):
    import httpx

    source = sources[0].model_copy(update={"min_interval_seconds": 7200})

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "900"})

    start = datetime.now(UTC)
    result = discover(
        conn, profile, [source], transport=httpx.MockTransport(respond), pause=lambda _: None
    )
    deadline = conn.execute(
        "SELECT next_allowed_at FROM sources WHERE id=?", (source.id,)
    ).fetchone()[0]
    assert result[0]["status"] == "error"
    assert datetime.fromisoformat(deadline) >= start + timedelta(seconds=7199)


def test_rate_limit_preserves_concurrent_later_deadline(conn, tmp_path, profile, sources):
    import httpx

    from app.db import connect

    source = sources[0].model_copy(update={"min_interval_seconds": 7200})
    later = (datetime.now(UTC) + timedelta(hours=4)).isoformat()

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        with connect(tmp_path / "jobs.sqlite3") as other:
            other.execute("UPDATE sources SET next_allowed_at=? WHERE id=?", (later, source.id))
        return httpx.Response(429, headers={"Retry-After": "900"})

    discover(conn, profile, [source], transport=httpx.MockTransport(respond), pause=lambda _: None)
    assert (
        conn.execute("SELECT next_allowed_at FROM sources WHERE id=?", (source.id,)).fetchone()[0]
        == later
    )

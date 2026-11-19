import io
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from rich.console import Console

from app import analyst, cli, doctor, watch
from app.db import connect
from app.pipeline.discover import discover
from app.resume import digest, resume_text
from app.settings import Settings, Watch
from app.store import list_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures"


def reset(conn):
    conn.execute("UPDATE sources SET next_allowed_at=NULL")
    conn.commit()


def response_transport(data=None, status=200):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(status, json=data)

    return httpx.MockTransport(handler)


@pytest.fixture
def cfg(tmp_path, profile, monkeypatch):
    cfg = Settings(
        _env_file=None,
        database_path=tmp_path / "jobs.sqlite3",
        resume_path=tmp_path / "resume.md",
        env_path=tmp_path / ".env",
        watch=Watch(digest_dir=tmp_path / "digests"),
    )
    cfg.resume_path.write_text("Reviewed scientific experience.")
    profile.reviewed = True
    profile.resume_hash = digest(resume_text(cfg))

    # Run real cache, persistence, validation, batching and ranking with offline model output.
    def assess(cfg, profile, rows):
        dim = {
            "score": 80,
            "confidence": "supported",
            "candidate_evidence": "Reviewed experience",
            "job_evidence": "Scientific experience",
            "explanation": "Relevant",
        }
        return {
            r["id"]: analyst.Evaluation(
                score=80,
                dimensions={k: dim for k in type(cfg.analyst.weights).model_fields},
                strengths=["Relevant experience"],
                gaps=[],
                must_haves=[],
                confidence="supported",
                uncertainty_flags=[],
                recommendation="apply",
            )
            for r in rows
        }

    monkeypatch.setattr(analyst, "assess", assess)
    monkeypatch.setattr(watch.sys, "platform", "darwin")
    monkeypatch.setattr(watch.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    return cfg


def run(cfg, conn, profile, sources, transport, **kwargs):
    reset(conn)
    return watch.run(
        cfg, conn, profile, sources, transport=transport, pause=lambda _: None, **kwargs
    )


def test_alert_once_restart_and_changed_content(
    cfg, conn, profile, sources, transport, monkeypatch
):
    calls = []
    monkeypatch.setattr(watch, "notify", lambda *a: calls.append(a))
    assert run(cfg, conn, profile, sources, transport)["notified"] == 2
    assert len(calls) == 2
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0
    # A fresh connection/process sees the persisted notification claim.
    with connect(cfg.database_path) as restarted:
        assert run(cfg, restarted, profile, sources, transport)["notified"] == 0
    data = json.loads((FIXTURES / "lever.json").read_text())
    for item in data:
        item["descriptionPlain"] += " New material requirement: manage a scientific team."
    changed = response_transport(data)
    result = run(cfg, conn, profile, [sources[0]], changed)
    assert result["scored"] == result["notified"] == 1
    assert len(calls) == 3
    assert run(cfg, conn, profile, [sources[0]], changed)["notified"] == 0
    assert conn.execute("SELECT count(*) FROM job_notifications").fetchone()[0] == 3
    paths = list(cfg.watch.digest_dir.glob("*.md"))
    assert len(paths) == 2
    assert "Principal Scientist" in paths[0].read_text() + paths[1].read_text()
    # Returning to an already alerted content version does not alert again.
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0


def test_score_gate_then_lower_threshold_and_profile_change(cfg, conn, profile, sources, transport):
    cfg.watch.min_score = 90
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0
    cfg.watch.min_score = 80
    assert run(cfg, conn, profile, sources, transport)["notified"] == 2
    cfg.analyst.preferences = "A different preference"
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0
    assert conn.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 4


def test_content_version_ignores_metadata_and_whitespace(cfg, conn, profile, sources, transport):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    row = list_jobs(conn, profile, sources)[0]
    version = watch.content_version(row)
    row.update(posted_at="2030-01-01", external_id="other", source_url="https://other.example/job")
    row["description"] = "\n  " + row["description"].replace(" ", "  ")
    assert watch.content_version(row) == version
    row["salary_min"] = 120000
    assert watch.content_version(row) != version


@pytest.mark.parametrize("count,expected", [(0, 0), (1, 1), (3, 3), (4, 1), (20, 1)])
def test_individual_vs_summary_osascript(monkeypatch, count, expected):
    calls = []
    monkeypatch.setattr(watch.sys, "platform", "darwin")
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0),
    )
    watch.desktop_digest(
        [
            {
                "title": 'Scientist "quoted"\nline',
                "employer": "Firm",
                "score": 80,
                "location": "London",
            }
            for _ in range(count)
        ]
    )
    assert len(calls) == expected
    assert all(args[0] == "osascript" and "\n" not in args[2] for args in calls)
    if count > 3:
        assert f"{count} new or changed" in calls[0][2]


def test_non_macos_never_invokes_osascript(monkeypatch):
    monkeypatch.setattr(watch.sys, "platform", "linux")
    monkeypatch.setattr(watch.subprocess, "run", lambda *a, **k: pytest.fail("osascript on Linux"))
    watch.notify("title", "body")


def test_crash_after_claim_recovers_digest_without_duplicate(
    cfg, conn, profile, sources, transport, monkeypatch
):
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    analyst.analyze_new(cfg, conn, profile, sources)
    rows = analyst.attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources))
    assert watch.queue_alerts(cfg, conn, rows) == 2
    assert not cfg.watch.digest_dir.exists()
    calls = []
    monkeypatch.setattr(watch, "notify", lambda *a: calls.append(a))
    with connect(cfg.database_path) as restarted:
        watch.deliver_pending(cfg, restarted)
        watch.deliver_pending(cfg, restarted)
    assert len(calls) == 2
    assert len(list(cfg.watch.digest_dir.glob("*.md"))) == 1
    assert watch.queue_alerts(cfg, conn, rows) == 0


def test_uncertain_notification_is_never_retried(
    cfg, conn, profile, sources, transport, monkeypatch
):
    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "desktop_digest", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(cfg, conn, profile, sources, transport)
    monkeypatch.setattr(watch, "desktop_digest", lambda *a: pytest.fail("Duplicate notification"))
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0
    assert len(list(cfg.watch.digest_dir.glob("*.md"))) == 1


def test_optional_delivery_failure_keeps_digest_and_no_secrets(
    cfg, conn, profile, sources, transport, monkeypatch
):
    cfg.watch.telegram = True
    monkeypatch.setattr(cfg.__class__, "secret", lambda self, name: "secret-value")

    def failed(*args):
        raise OSError("https://api.telegram.org/botSECRET")

    monkeypatch.setattr(watch, "telegram_digest", failed)
    output = []
    result = run(cfg, conn, profile, sources, transport, output=output.append)
    assert result["errors"] == 1
    assert "SECRET" not in " ".join(output)
    assert len(list(cfg.watch.digest_dir.glob("*.md"))) == 1
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0


def test_telegram_off_and_missing_credentials(cfg, conn, profile, sources, transport, monkeypatch):
    monkeypatch.setattr(watch, "telegram_digest", lambda *a: pytest.fail("Telegram off"))
    run(cfg, conn, profile, sources, transport)
    cfg.watch.telegram = True
    with pytest.raises(ValueError, match="BIO_TELEGRAM"):
        run(cfg, conn, profile, sources, transport)


def test_telegram_stdlib_chunks_plain_text(cfg, monkeypatch):
    cfg.env_path.write_text("BIO_TELEGRAM_BOT_TOKEN=token\nBIO_TELEGRAM_CHAT_ID=chat\n")
    requests = []

    def open_request(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(watch, "urlopen", open_request)
    watch.telegram_digest(cfg, "x" * 8100)
    from urllib.parse import parse_qs

    assert len(requests) == 3
    for request in requests:
        data = parse_qs(request.data.decode())
        assert len(data["text"][0]) <= 4000
        assert data["chat_id"] == ["chat"]
        assert "parse_mode" not in data


def test_two_complete_absences_close_and_reappearance_reopens(
    cfg, conn, profile, sources, transport
):
    run(cfg, conn, profile, sources, transport)
    job = next(j for j in list_jobs(conn, profile, sources) if j["employer"] == "Mytos")
    conn.execute(
        "INSERT INTO applications(job_id,state,submitted_at) VALUES (?,'submitted','proof')",
        (job["id"],),
    )
    conn.commit()
    original = tuple(conn.execute("SELECT * FROM applications").fetchone())
    empty = response_transport([])
    run(cfg, conn, profile, [sources[0]], empty)
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "open"
    run(cfg, conn, profile, [sources[0]], empty)
    assert (
        conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "closed"
    )
    closed = next(j for j in list_jobs(conn, profile, sources) if j["id"] == job["id"])
    assert "[closed]" in closed["date_label"]
    assert job["id"] not in {
        j["id"] for j in list_jobs(conn, profile, sources, prospects_only=True)
    }
    assert run(cfg, conn, profile, sources, transport)["notified"] == 0
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "open"
    assert tuple(conn.execute("SELECT * FROM applications").fetchone()) == original


@pytest.mark.parametrize("failure", ["error", "rate_limit", "malformed", "cooldown"])
def test_unsuccessful_snapshots_close_nothing(cfg, conn, profile, sources, transport, failure):
    run(cfg, conn, profile, sources, transport)
    run(cfg, conn, profile, [sources[0]], response_transport([]))
    if failure == "cooldown":
        result = watch.run(
            cfg, conn, profile, [sources[0]], transport=response_transport([]), pause=lambda _: None
        )
    else:
        data, status = {
            "error": (None, 503),
            "rate_limit": (None, 429),
            "malformed": ([{"id": "broken"}], 200),
        }[failure]
        result = run(cfg, conn, profile, [sources[0]], response_transport(data, status))
    assert result["sources"][0]["status"] == (
        "partial" if failure == "malformed" else "cooldown" if failure == "cooldown" else "error"
    )
    assert conn.execute("SELECT min(present) FROM job_sources").fetchone()[0] == 1
    assert conn.execute("SELECT max(missing_count) FROM job_sources").fetchone()[0] == 1


def test_duplicate_origins_and_site_watchers_do_not_prematurely_close(
    cfg, conn, profile, sources, transport
):
    duplicate = sources[0].model_copy(update={"id": "duplicate"})
    both = [sources[0], duplicate]
    run(cfg, conn, profile, both, transport)
    for _ in range(2):
        run(cfg, conn, profile, [sources[0]], response_transport([]))
    assert list_jobs(conn, profile, both)
    site = duplicate.model_copy(update={"adapter": "site", "board": "https://example.org/careers"})
    html = "<html><p>Career page update with generic information for interested candidates and links to opportunities.</p></html>"
    snapshot = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else html
        )
    )
    for _ in range(3):
        run(cfg, conn, profile, [site], snapshot)
    assert (
        conn.execute("SELECT present FROM job_sources WHERE source_id='duplicate'").fetchone()[0]
        == 1
    )


def test_partial_detail_page_health(cfg, conn, profile, sources):
    source = sources[0].model_copy(
        update={"adapter": "site", "board": "https://example.org/careers", "links": "/job/"}
    )

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/careers":
            return httpx.Response(200, text='<a href="/job/1">one</a><a href="/job/2">two</a>')
        if request.url.path == "/job/2":
            return httpx.Response(503)
        return httpx.Response(200, text="<p>" + "Generic career information. " * 10 + "</p>")

    result = run(cfg, conn, profile, [source], httpx.MockTransport(handler))
    assert result["sources"][0]["status"] == "partial"
    health = conn.execute("SELECT * FROM sources").fetchone()
    assert health["last_success_at"] is None
    assert health["consecutive_failures"] == 1


def test_health_failure_recovery_and_reporting(cfg, conn, profile, sources, transport, monkeypatch):
    run(cfg, conn, profile, sources, transport)
    for _ in range(2):
        run(cfg, conn, profile, [sources[0]], response_transport(None, 503))
    row = conn.execute("SELECT * FROM sources WHERE id=?", (sources[0].id,)).fetchone()
    assert row["consecutive_failures"] == 2 and row["last_count"] == 2
    assert row["last_success_at"] and row["last_error_at"]
    error = row["last_error"]
    run(cfg, conn, profile, sources, transport)
    row = conn.execute("SELECT * FROM sources WHERE id=?", (sources[0].id,)).fetchone()
    assert row["consecutive_failures"] == 0 and row["last_error"] == error
    output = io.StringIO()
    for module in (cli, doctor):
        monkeypatch.setattr(module, "console", Console(file=output, width=500))
    cli._stats(conn, cfg)
    doctor.run(cfg)
    text = output.getvalue()
    assert "Last count" in text and "Failures" in text
    assert (
        "last success" in text
        and "last error" in text
        and "consecutive failures 0; last count 2" in text
    )


@pytest.mark.parametrize(
    "mode,scheduled,expected",
    [("manual", True, 0), ("assisted", True, 0), ("auto", False, 0), ("auto", True, 1)],
)
def test_scheduled_auto_only_uses_existing_runner(
    cfg, conn, profile, sources, transport, monkeypatch, mode, scheduled, expected
):
    calls = []
    monkeypatch.setattr("app.autopilot.run", lambda *a, **kw: calls.append(a))
    cfg.apply.mode = mode
    run(cfg, conn, profile, sources, transport, scheduled=scheduled)
    assert len(calls) == expected


def test_overlap_lock_skips_network_and_releases_after_crash(
    cfg, conn, profile, sources, transport, monkeypatch
):
    with watch.run_lock(cfg.database_path) as acquired:
        assert acquired
        monkeypatch.setattr(watch, "discover", lambda *a, **k: pytest.fail("Overlapping sync"))
        assert watch.run(cfg, conn, profile, sources)["skipped"]
    # A separate process exits without executing a Python cleanup handler.
    script = "import fcntl, os, sys; f=open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); os._exit(0)"
    lockpath = cfg.database_path.with_suffix(".sqlite3.watch.lock")
    subprocess.run([sys.executable, "-c", script, str(lockpath)], check=True)
    with watch.run_lock(cfg.database_path) as acquired:
        assert acquired
    assert lockpath.exists()  # inode retained; no stale PID cleanup needed.


def test_watch_cli_dispatch(cfg, profile, monkeypatch):
    import yaml

    cfg.config_path = cfg.database_path.parent / "config.yaml"
    cfg.candidate_path = cfg.database_path.parent / "candidate.yaml"
    cfg.config_path.write_text("watch:\n  min_score: 75\n  digest_dir: alerts\n")
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    calls = []
    monkeypatch.setattr(watch, "run", lambda *a, **kw: calls.append((a, kw)) or {"errors": 0})
    assert cli.main(["watch", "--scheduled"]) == 0
    assert calls[0][1]["scheduled"] is True
    assert calls[0][0][0].watch.min_score == 75
    assert calls[0][0][0].watch.digest_dir == cfg.config_path.parent / "alerts"

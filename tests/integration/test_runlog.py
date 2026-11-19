import json

import httpx
import pytest

from app.pipeline.discover import discover
from app.runlog import record, run


def test_run_summary_records_safe_events_and_failure(tmp_path, conn, profile, sources):
    source = sources[0].model_copy(update={"min_interval_seconds": 0})

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "900"}, text="secret provider text")

    with pytest.raises(ValueError), run("scan", tmp_path) as summary:
        discover(
            conn, profile, [source], transport=httpx.MockTransport(respond), pause=lambda _: None
        )
        record("scoring", {"id": 42, "status": "failed", "error_category": "RuntimeError"})
        raise ValueError("private candidate text")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text()
    saved = json.loads(raw)
    assert saved["id"] == summary["id"] == files[0].stem
    assert saved["status"] == "failed"
    assert saved["sources"][0]["error_category"] == "RateLimited"
    assert saved["scoring"][0]["id"] == 42
    assert "secret provider text" not in raw and "private candidate text" not in raw
    assert files[0].stat().st_mode & 0o077 == 0


def test_successful_discovery_records_new_jobs(tmp_path, conn, profile, sources, transport):
    with run("sync", tmp_path) as summary:
        discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["id"] == summary["id"]
    assert saved["status"] == "completed"
    assert {entry["status"] for entry in saved["sources"]} == {"ok"}
    assert len([entry for entry in saved["jobs"] if entry["change"] == "new"]) == 2

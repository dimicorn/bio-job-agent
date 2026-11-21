"""Dry run, human-paced typing and browser launch; local HTML only, Submit is never pressed."""

import subprocess
from types import SimpleNamespace

import pytest
import test_browser_apply as base
from pydantic import ValidationError

from app import applications, autopilot
from app.applier import APPLIERS, FormApplier
from app.settings import Apply

# Shared local-HTML fixtures and helpers.
browser, page, prepared = base.browser, base.page, base.prepared
approve, events, mock_form = base.approve, base.events, base.mock_form


def state(conn, row):
    return conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[0]


@pytest.mark.parametrize("ats", APPLIERS)
def test_dry_run_fills_verifies_screenshots_and_never_submits(prepared, conn, page, ats):
    cfg, profile, row = approve(prepared, conn, ats)
    cfg.apply.mode = "dry_run"
    mock_form(page, ats)
    output = []
    assert autopilot.apply_one(cfg, conn, profile, row, page, output=output.append) == "dry_run"
    assert page.evaluate("window.__submissions") == 0
    assert any(e["action"] == "verify" for e in events(conn, row))
    shot = cfg.materials_path / str(row["id"]) / f"{row['id']}-1-dry-run.png"
    assert shot.read_bytes().startswith(b"\x89PNG")
    assert shot.stat().st_mode & 0o077 == 0
    assert f"Screenshot: {shot}" in output[-1]
    assert not list(cfg.apply.audit_dir.glob("*.png"))
    attempt = conn.execute("SELECT * FROM application_attempts").fetchone()
    assert (attempt["mode"], attempt["status"], attempt["submission_possible"]) == (
        "dry_run",
        "dry_run",
        0,
    )
    assert state(conn, row) == "approved"
    assert applications.company_cooldown(conn, row["employer"], 30) is None


def test_dry_run_uses_no_slots_and_allows_a_real_application_after(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    cfg.apply.mode, cfg.apply.daily_limit = "dry_run", 1
    mock_form(page, "greenhouse")
    for _ in range(2):
        assert autopilot.apply_one(cfg, conn, profile, row, page, used=5) == "dry_run"
    cfg.apply.mode = "auto"
    applications.check_gates(cfg, conn, row)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"
    assert page.evaluate("window.__submissions") == 1


def test_dry_run_stop_keeps_job_approved(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    cfg.apply.mode = "dry_run"
    mock_form(
        page,
        "greenhouse",
        script="document.querySelector('[type=email]').addEventListener('input',e=>e.target.value='wrong@example.org')",
    )
    output = []
    status = autopilot.apply_one(cfg, conn, profile, row, page, output=output.append)
    assert status == "dry_run_stopped"
    assert page.evaluate("window.__submissions") == 0
    assert "Verification mismatch" in output[-1] and "stays approved" in output[-1]
    assert (cfg.materials_path / str(row["id"]) / f"{row['id']}-1-stopped.png").exists()
    assert state(conn, row) == "approved"
    # A stopped dry run is no retry barrier: the job can still be applied for.
    autopilot.reserve(cfg, conn, profile, row, "auto")


def test_dry_run_queue_closes_every_form_and_continues(prepared, conn, page):
    cfg, profile, _row = approve(prepared, conn)
    approve(prepared, conn, index=1)
    cfg.apply.mode, cfg.apply.max_per_run = "dry_run", 1
    mock_form(page, "greenhouse")
    closes = []

    class Browser:
        def __init__(self, cfg, **kwargs):
            pass

        def page(self):
            return page

        def close(self, **kwargs):
            closes.append(kwargs)

    output = []
    outcomes = autopilot.run(
        cfg, conn, profile, prepared[2], browser_factory=Browser, output=output.append
    )
    assert outcomes and set(outcomes) == {"dry_run"}
    assert closes == [{"leave_open": False}] * len(outcomes)
    assert "nothing was submitted" in output[-1]
    assert page.evaluate("window.__submissions") == 0


def test_cli_mode_override(prepared, conn, monkeypatch, tmp_path):
    import yaml

    from app import cli

    cfg, profile, row = approve(prepared, conn)
    cfg.apply.mode = "manual"
    cfg.database_path = tmp_path / "jobs.sqlite3"
    cfg.candidate_path = tmp_path / "candidate.yaml"
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    cfg.config_path = tmp_path / "config.yaml"
    cfg.config_path.write_text(yaml.safe_dump({"apply": cfg.apply.model_dump(mode="json")}))
    modes = []
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    monkeypatch.setattr(autopilot, "run", lambda c, *a, **k: modes.append(c.apply.mode) or [])
    assert cli.main(["apply", str(row["id"]), "--mode", "dry_run"]) == 0
    assert cli.main(["autopilot", "--no-scan", "--mode", "dry_run"]) == 0
    assert cli.main(["autopilot", "--no-scan"]) == 1
    assert modes == ["dry_run", "dry_run"]
    with pytest.raises(SystemExit):
        cli.main(["apply", "--mode", "auto"])


def test_pacing_settings():
    assert Apply(action_seconds=0.3).action_seconds == (0.3, 0.3)
    assert Apply(typing_delay_ms=[10, 20]).typing_delay_ms == (10, 20)
    defaults = Apply()
    assert defaults.read_seconds[1] > 0 and defaults.typing_delay_ms[1] > 0
    assert defaults.browser_channel == "chrome"
    for bad in ([5, 1], [-1, 2]):
        with pytest.raises(ValidationError):
            Apply(read_seconds=bad)


def test_reading_pause_field_pause_and_typing_delays(prepared, page):
    cfg = prepared[0]
    cfg.apply.read_seconds, cfg.apply.action_seconds = (2, 4), (0.5, 1)
    cfg.apply.typing_delay_ms = (30, 90)
    waits = []
    page.set_content("<form><input id=a></form>")
    applier = FormApplier(page, cfg)
    applier.page = SimpleNamespace(
        wait_for_timeout=waits.append, keyboard=page.keyboard, goto=page.goto
    )
    applier.pause(cfg.apply.read_seconds)
    assert 2000 <= waits.pop() <= 4000
    applier.type_text(page.locator("#a"), "abc")
    assert len(waits) == 3 and all(30 <= w <= 90 for w in waits)
    assert page.locator("#a").input_value() == "abc"


def test_typing_is_per_character_and_newline_never_submits(prepared, page):
    cfg = prepared[0]
    page.set_content(
        """<form><input id=line><textarea id=area></textarea><button>Submit</button></form>
        <script>window.keys = []; window.__submissions = 0;
        document.addEventListener('keydown', e => window.keys.push(e.key));
        document.querySelector('form').addEventListener('submit', e => {
          e.preventDefault(); window.__submissions++; });</script>"""
    )
    applier = FormApplier(page, cfg)
    applier.type_text(page.locator("#line"), "Line one\nline two\t")
    assert page.locator("#line").input_value() == "Line oneline two\t"
    applier.type_text(page.locator("#area"), "Dear team,\n\nThank you.")
    assert page.evaluate("window.__submissions") == 0
    assert page.locator("#area").input_value() == "Dear team,\n\nThank you."
    # Every printable character was a key press (clearing sends Delete); breaks were inserted.
    keys = [k for k in page.evaluate("window.keys") if k != "Delete"]
    assert keys == list("Line oneline two") + list("Dear team,Thank you.")


def test_browser_choice_prefers_chrome_and_falls_back(prepared, monkeypatch, tmp_path):
    cfg = prepared[0]
    chromium = tmp_path / "chromium"
    chromium.write_text("")
    monkeypatch.setattr(autopilot, "chrome_path", lambda: "/Chrome")
    assert autopilot.browser_choice(cfg, lambda: str(chromium)) == ("/Chrome", "Google Chrome")
    monkeypatch.setattr(autopilot, "chrome_path", lambda: None)
    path, note = autopilot.browser_choice(cfg, lambda: str(chromium))
    assert path == str(chromium) and "not installed" in note
    cfg.apply.browser_channel = "chromium"
    monkeypatch.setattr(autopilot, "chrome_path", lambda: "/Chrome")
    assert autopilot.browser_choice(cfg, lambda: str(chromium))[0] == str(chromium)
    assert autopilot.browser_choice(cfg, lambda: str(tmp_path / "none"))[0] is None


def test_browser_launch_has_no_automation_markers(prepared, monkeypatch, tmp_path):
    cfg = prepared[0]
    launched, output = [], []
    monkeypatch.setattr(autopilot, "chrome_path", lambda: None)
    executable = tmp_path / "chromium"
    executable.write_text("")

    class Pw:
        chromium = SimpleNamespace(executable_path=str(executable))

        def stop(self):
            pass

    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright", lambda: SimpleNamespace(start=lambda: Pw())
    )

    def popen(args, **kwargs):
        launched.append(args)
        raise OSError("not launched in tests")

    monkeypatch.setattr(subprocess, "Popen", popen)
    instance = autopilot.Browser(cfg, output=output.append)
    with pytest.raises(OSError):
        instance.page()
    instance.close()
    args = launched[0]
    assert args[0] == str(executable)
    assert "--disable-blink-features=AutomationControlled" in args
    assert "--enable-automation" not in args
    assert f"--user-data-dir={cfg.apply.profile_dir}" in args
    assert cfg.apply.profile_dir.stat().st_mode & 0o077 == 0
    assert output == ["Google Chrome is not installed; using Playwright Chromium instead."]

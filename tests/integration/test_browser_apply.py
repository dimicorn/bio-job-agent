"""All browser requests are fulfilled from local HTML; no real employers are contacted."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app import answers, applications, autopilot, materials, resume
from app.applier import APPLIERS
from app.pipeline.discover import discover
from app.settings import Apply, Settings
from app.store import list_jobs

FORMS = Path(__file__).parents[1] / "fixtures/forms"
URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/example/jobs/123",
    "lever": "https://jobs.lever.co/example/123",
    "workable": "https://apply.workable.com/example/j/123/",
    "ashby": "https://jobs.ashbyhq.com/example/123",
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def prepared(tmp_path, conn, profile, sources, transport):
    cfg = Settings(
        _env_file=None,
        resume_path=tmp_path / "resume.md",
        materials_path=tmp_path / "materials",
        apply=Apply(
            mode="auto",
            active_hours=(0, 24),
            answers_file=tmp_path / "answers.yaml",
            audit_dir=tmp_path / "audit",
            profile_dir=tmp_path / "browser",
            confirmation_timeout_seconds=0.1,
            submit_wait_minutes=0.01,
            action_seconds=0,
        ),
    )
    cfg.resume_path.write_text("# Alex Example\nalex@example.org\n## Experience\nCell assays.\n")
    profile.name, profile.reviewed = "Alex Example", True
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    rows = list_jobs(conn, profile, sources)
    answers.save_answer(cfg, "Research focus", "Cell assays")
    for row in rows:
        row["score"] = 90
    return cfg, profile, rows


def approve(prepared, conn, ats="greenhouse", index=0):
    cfg, profile, rows = prepared
    row = rows[index]
    row["apply_url"] = URLS[ats]
    materials.save_version(cfg, conn, profile, row, "letter", "Approved letter about cell assays.")
    item = materials.save_version(cfg, conn, profile, row, "resume", cfg.resume_path.read_text())
    pdf = cfg.materials_path / f"resume-{row['id']}.pdf"
    pdf.write_bytes(b"%PDF-mock approved CV")
    conn.execute(
        "UPDATE material_versions SET pdf_path=?,pdf_hash=?,ats_warnings=? WHERE id=?",
        (str(pdf), resume.digest(pdf.read_bytes().hex()), "[]", item["id"]),
    )
    answers.snapshot(cfg, conn, profile, row)
    applications.approve(cfg, conn, profile, row)
    return cfg, profile, row


def mock_form(page, ats, *, extra="", result=None, script=""):
    if result is None:
        result = "document.querySelector('form').remove(); document.body.insertAdjacentHTML('beforeend', '<h1>Application received</h1>')"
    html = (
        (FORMS / f"{ats}.html")
        .read_text()
        .replace("<!--EXTRA-->", extra)
        .replace("/*RESULT*/", result)
    )
    html = html.replace("</body>", f"<script>{script}</script></body>")
    page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))


def events(conn, row):
    return [
        dict(e)
        for e in conn.execute("SELECT * FROM application_events WHERE job_id=?", (row["id"],))
    ]


@pytest.mark.parametrize("ats", APPLIERS)
def test_auto_fills_verifies_and_requires_acknowledgement(prepared, conn, page, ats):
    cfg, profile, row = approve(prepared, conn, ats)
    mock_form(page, ats)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"
    assert page.evaluate("window.__submissions") == 1
    audit = events(conn, row)
    assert any(e["action"] == "verify" for e in audit)
    assert any(e["action"] == "confirmation" for e in audit)
    shots = list(cfg.apply.audit_dir.glob("*.png"))
    assert len(shots) == 2
    assert {p.name.split("-")[-1] for p in shots} == {"filled.png", "confirmation.png"}
    assert conn.execute(
        "SELECT submitted_at FROM applications WHERE job_id=?", (row["id"],)
    ).fetchone()[0]


@pytest.mark.parametrize("ats", APPLIERS)
def test_mismatch_refuses_submit(prepared, conn, page, ats):
    cfg, profile, row = approve(prepared, conn, ats)
    mock_form(
        page,
        ats,
        script="document.querySelector('[type=email]').addEventListener('input',e=>e.target.value='wrong@example.org')",
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert "Verification mismatch" in events(conn, row)[-1]["detail"]


@pytest.mark.parametrize("ats", APPLIERS)
@pytest.mark.parametrize(
    "extra,reason",
    [
        ('<div class="g-recaptcha">CAPTCHA challenge</div>', "CAPTCHA"),
        ('<input autocomplete="one-time-code" aria-label="Verification code">', "MFA"),
        ('<input type="password" aria-label="Password">', "Sign-in"),
        ("<h2>Complete the assessment</h2>", "assessment"),
        ("<label>Do you require visa sponsorship?<input required></label>", "declaration"),
        ("<label>Gender<input></label>", "declaration"),
        ('<label>I agree to the terms<input type="checkbox"></label>', "declaration"),
    ],
)
def test_safety_stops_even_with_known_answer(prepared, conn, page, ats, extra, reason):
    cfg, profile, row = approve(prepared, conn, ats)
    mock_form(page, ats, extra=extra)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert reason.casefold() in events(conn, row)[-1]["detail"].casefold()


@pytest.mark.parametrize("ats", APPLIERS)
def test_unknown_required_question_goes_to_answers(prepared, conn, page, ats):
    cfg, profile, row = approve(prepared, conn, ats)
    mock_form(page, ats, extra="<label>Novel required question *<input required></label>")
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    question = conn.execute(
        "SELECT * FROM screening_questions WHERE job_id=?", (row["id"],)
    ).fetchone()
    assert question["label"] == "Novel required question *"
    assert question["required"]


@pytest.mark.parametrize("ats", APPLIERS)
def test_assisted_waits_for_person_and_never_clicks(prepared, conn, page, ats, monkeypatch):
    cfg, profile, row = approve(prepared, conn, ats)
    cfg.apply.mode = "assisted"
    mock_form(page, ats)
    original = APPLIERS[ats].wait_confirmation

    def human_submit(applier, seconds):
        assert seconds == cfg.apply.submit_wait_minutes * 60
        assert page.evaluate("window.__submissions") == 0
        # The mock person clicks after handoff; production code must never do so in assisted mode.
        applier.submit_button().click()
        return original(applier, seconds)

    monkeypatch.setattr(APPLIERS[ats], "wait_confirmation", human_submit)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"
    assert not any(e["action"] == "submit_click" for e in events(conn, row))
    assert any(e["action"] == "human_submit_handoff" for e in events(conn, row))


@pytest.mark.parametrize("ats", APPLIERS)
@pytest.mark.parametrize("mode", ["auto", "assisted"])
def test_unconfirmed_result_is_never_retried(prepared, conn, page, ats, mode):
    cfg, profile, row = approve(prepared, conn, ats)
    cfg.apply.mode = mode
    mock_form(page, ats, result="history.replaceState(null,'','/confirmation')")
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "unknown_result"
    assert page.evaluate("window.__submissions") == (1 if mode == "auto" else 0)
    # A URL named confirmation is not acknowledgement. Even resetting a state cannot enable retries.
    materials.state(conn, row["id"], "approved")
    with pytest.raises(ValueError, match="never retry"):
        autopilot.apply_one(cfg, conn, profile, row, page)
    with pytest.raises(ValueError, match="never retry"):
        applications.approve(cfg, conn, profile, row)
    assert conn.execute("SELECT count(*) FROM application_attempts").fetchone()[0] == 1


def test_challenge_after_submit_is_manual_and_nonretryable(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        result="document.body.insertAdjacentHTML('beforeend','<div class=g-recaptcha>Challenge</div>')",
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 1
    assert conn.execute("SELECT submission_possible FROM application_attempts").fetchone()[0] == 1
    with pytest.raises(ValueError, match="never retry"):
        applications.approve(cfg, conn, profile, row)


def test_crashed_attempt_blocks_and_consumes_daily_slot(prepared, conn):
    cfg, profile, row = approve(prepared, conn)
    autopilot.reserve(cfg, conn, profile, row, "auto")
    with pytest.raises(ValueError, match="never retry"):
        autopilot.reserve(cfg, conn, profile, row, "auto")
    # Another job approved before the slot was consumed is blocked at execution time.
    cfg.apply.daily_limit = 1
    other = prepared[2][1]
    with pytest.raises(ValueError, match="daily_limit"):
        applications.check_gates(cfg, conn, other)


def test_gate_rechecked_at_execution_and_run_limit(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    cfg.apply.max_per_run = 1
    with pytest.raises(ValueError, match="max_per_run"):
        autopilot.reserve(cfg, conn, profile, row, "auto", used=1)
    row["score"] = 70
    with pytest.raises(ValueError, match="threshold"):
        autopilot.reserve(cfg, conn, profile, row, "auto")
    row["score"] = 90
    cfg.apply.active_hours = (0, 1)
    from datetime import datetime

    with pytest.raises(ValueError, match="active_hours"):
        autopilot.reserve(cfg, conn, profile, row, "auto", now=datetime(2026, 1, 1, 12))
    assert not conn.execute("SELECT * FROM application_attempts").fetchall()


@pytest.mark.parametrize("change", ["bank", "new_letter", "pdf"])
def test_approved_versions_and_answers_are_pinned(prepared, conn, change):
    cfg, profile, row = approve(prepared, conn)
    if change == "bank":
        answers.save_answer(cfg, "Email", "different@example.org")
    elif change == "new_letter":
        materials.save_version(cfg, conn, profile, row, "letter", "New unapproved text")
    else:
        item = materials.latest(conn, row["id"], "resume")
        Path(item["pdf_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        autopilot.reserve(cfg, conn, profile, row, "auto")
    assert not conn.execute("SELECT * FROM application_attempts").fetchall()


def test_final_verify_catches_async_overwrite(prepared, conn, page, monkeypatch):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse")
    original = autopilot.screenshot

    def overwrite(page, *args):
        path = original(page, *args)
        page.locator("#email").fill("changed-after-fill@example.org")
        return path

    monkeypatch.setattr(autopilot, "screenshot", overwrite)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0


def test_known_legal_answer_still_requires_person(prepared, conn, page):
    cfg = prepared[0]
    answers.save_answer(cfg, "Do you require visa sponsorship?", "No")
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        extra="<label>Do you require visa sponsorship?<select required><option>No</option><option>Yes</option></select></label>",
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0


def test_native_select_and_exact_multiple_choice_verification(prepared, conn, page):
    cfg = prepared[0]
    answers.save_answer(cfg, "Tools", ["Python", "R"])
    cfg, profile, row = approve(prepared, conn)
    extra = "<label>Tools<select multiple required><option>Python</option><option>R</option><option>Other</option></select></label>"
    mock_form(page, "greenhouse", extra=extra)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"


def test_wrong_file_contents_with_same_name_refuses_submit(prepared, conn, page, monkeypatch):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse")
    original = autopilot.screenshot

    def replace_file(page, *args):
        path = original(page, *args)
        current = page.locator("#resume").evaluate("e=>e.files[0].name")
        page.locator("#resume").set_input_files(
            {"name": current, "mimeType": "application/pdf", "buffer": b"wrong content"}
        )
        return path

    monkeypatch.setattr(autopilot, "screenshot", replace_file)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert "File content verification mismatch" in events(conn, row)[-1]["detail"]


def test_late_required_question_is_queued(prepared, conn, page, monkeypatch):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse")
    original = autopilot.screenshot

    def add_question(page, *args):
        path = original(page, *args)
        page.locator("form").evaluate(
            "e=>e.insertAdjacentHTML('beforeend','<label>New required question<input required></label>')"
        )
        return path

    monkeypatch.setattr(autopilot, "screenshot", add_question)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert conn.execute(
        "SELECT 1 FROM screening_questions WHERE label='New required question'"
    ).fetchone()


def test_optional_unknown_is_blank_and_queued(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse", extra="<label>Optional novel question<input></label>")
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"
    assert (
        conn.execute(
            "SELECT required FROM screening_questions WHERE label='Optional novel question'"
        ).fetchone()[0]
        == 0
    )


def test_greenhouse_embedded_form(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    html = (
        (FORMS / "greenhouse.html")
        .read_text()
        .replace("/*RESULT*/", "document.body.innerHTML='<h1>Application submitted</h1>'")
    )

    def route(request):
        content = (
            '<iframe src="https://boards.greenhouse.io/embed/job_app?token=123"></iframe>'
            if "/example/" in request.request.url
            else html
        )
        request.fulfill(body=content, content_type="text/html")

    page.route("**/*", route)
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"


def test_cover_letter_pdf_uses_approved_text_and_keeps_textarea(prepared, conn, page):
    from pdfminer.high_level import extract_text

    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse", extra="<label>Cover Letter<input type=file></label>")
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "submitted"
    pdf = next(cfg.apply.audit_dir.glob("letter-*.pdf"))
    assert "Approved letter about cell assays." in extract_text(pdf)
    assert "<pre>" not in extract_text(pdf)


def test_auto_click_error_is_unknown_and_not_retryable(prepared, conn, page, monkeypatch):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse")
    from playwright.sync_api import Error

    class FailingButton:
        def click(self):
            raise Error("Click outcome ambiguous")

    monkeypatch.setattr(APPLIERS["greenhouse"], "submit_button", lambda self: FailingButton())
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "unknown_result"
    with pytest.raises(ValueError, match="uncertain"):
        applications.approve(cfg, conn, profile, row)


def test_profile_failure_is_audited_before_any_submit(prepared, conn):
    cfg, profile, row = approve(prepared, conn)

    def missing_browser():
        raise OSError("Chromium unavailable")

    assert autopilot.apply_one(cfg, conn, profile, row, missing_browser) == "manual_required"
    assert any(e["action"] == "error" for e in events(conn, row))
    assert conn.execute("SELECT submission_possible FROM application_attempts").fetchone()[0] == 0


def test_queue_limits_approved_only_and_stops_on_handoff(prepared, conn, page):
    cfg, profile, _row = approve(prepared, conn)
    cfg, profile, other = approve(prepared, conn, index=1)
    cfg.apply.max_per_run = 1
    mock_form(page, "greenhouse")
    closes = []

    class Browser:
        def __init__(self, cfg):
            pass

        def page(self):
            return page

        def close(self, **kwargs):
            closes.append(kwargs)

    assert autopilot.run(cfg, conn, profile, prepared[2], browser_factory=Browser) == ["submitted"]
    assert closes == [{"leave_open": False}]
    assert (
        conn.execute("SELECT state FROM applications WHERE job_id=?", (other["id"],)).fetchone()[0]
        == "approved"
    )
    cfg.apply.daily_limit = 1
    assert autopilot.run(cfg, conn, profile, prepared[2], browser_factory=Browser) == []
    assert conn.execute("SELECT count(*) FROM application_attempts").fetchone()[0] == 1
    cfg.apply.daily_limit = 5
    cfg.apply.mode = "assisted"
    assert autopilot.run(cfg, conn, profile, prepared[2], browser_factory=Browser) == [
        "unknown_result"
    ]
    assert closes[-1] == {"leave_open": True}
    # Neither submitted nor ambiguous jobs enter a future approved-queue pass.
    assert autopilot.run(cfg, conn, profile, prepared[2], browser_factory=Browser) == []


def test_browser_disconnect_preserves_handoff_and_existing_tabs(prepared):
    from unittest.mock import Mock

    instance = autopilot.Browser(prepared[0])
    instance.browser, instance.pw, instance.lock = Mock(), Mock(), Mock()
    browser, pw = instance.browser, instance.pw
    instance.close(leave_open=True)
    browser.close.assert_not_called()
    pw.stop.assert_called_once()
    instance.browser, instance.pw = Mock(), Mock()
    instance.reused, instance.tab = True, Mock()
    instance.tab.is_closed.return_value = False
    browser, tab = instance.browser, instance.tab
    instance.close()
    browser.close.assert_not_called()
    tab.close.assert_called_once()


def test_cli_apply_and_autopilot_routing(prepared, conn, monkeypatch, tmp_path):
    import yaml

    from app import cli

    cfg, profile, row = approve(prepared, conn)
    cfg.database_path = tmp_path / "jobs.sqlite3"
    cfg.candidate_path = tmp_path / "candidate.yaml"
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    cfg.config_path = tmp_path / "config.yaml"
    cfg.config_path.write_text(yaml.safe_dump({"apply": cfg.apply.model_dump(mode="json")}))
    calls = []
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    monkeypatch.setattr(autopilot, "run", lambda *a, **k: calls.append(k) or [])
    assert cli.main(["apply", str(row["id"])]) == 0
    assert calls[-1]["job"] == row["id"]
    assert cli.main(["autopilot", "--no-scan"]) == 0
    assert cli.main(["run", "--no-scan"]) == 0
    assert calls[-1]["job"] is None
    cfg.apply.mode = "manual"
    cfg.config_path.write_text(yaml.safe_dump({"apply": cfg.apply.model_dump(mode="json")}))
    assert cli.main(["autopilot", "--no-scan"]) == 1


def test_optional_question_with_unapproved_default_stops(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        extra='<label>Unknown optional preference<input value="employer default"></label>',
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert "Unapproved value" in events(conn, row)[-1]["detail"]


def test_legal_declaration_without_checkbox_stops(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        extra="<p>By submitting this application I certify the declarations above.</p>",
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0


def test_missing_required_contact_value_is_queued(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(page, "greenhouse", extra="<label>Phone number *<input type=tel required></label>")
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert conn.execute("SELECT 1 FROM screening_questions WHERE label='Phone number *'").fetchone()
    assert page.evaluate("window.__submissions") == 0


def test_invisible_captcha_widget_is_not_bypassed(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        extra='<div class=h-captcha style="display:none" data-sitekey="mock">Challenge</div>',
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert "CAPTCHA" in events(conn, row)[-1]["detail"]


def test_hidden_required_custom_question_is_queued(prepared, conn, page):
    cfg, profile, row = approve(prepared, conn)
    mock_form(
        page,
        "greenhouse",
        extra='<label>Unsupported widget<select required style="display:none"><option value="">Choose</option><option>Other</option></select></label>',
    )
    assert autopilot.apply_one(cfg, conn, profile, row, page) == "manual_required"
    assert page.evaluate("window.__submissions") == 0
    assert conn.execute(
        "SELECT 1 FROM screening_questions WHERE label='Unsupported widget'"
    ).fetchone()


def test_interrupted_apply_reports_uncertain_state(prepared, conn, monkeypatch):
    cfg, profile, row = approve(prepared, conn)
    output = []

    class Browser:
        def __init__(self, cfg):
            pass

        def page(self):
            pass

        def close(self, **kwargs):
            pass

    def interrupted(*args, **kwargs):
        conn.execute("UPDATE applications SET state='unknown_result' WHERE job_id=?", (row["id"],))
        raise KeyboardInterrupt

    monkeypatch.setattr(autopilot, "apply_one", interrupted)
    with pytest.raises(KeyboardInterrupt):
        autopilot.run(cfg, conn, profile, [row], browser_factory=Browser, output=output.append)
    assert "unknown_result" in output[-1]
    assert "Reconcile uncertain submissions manually" in output[-1]

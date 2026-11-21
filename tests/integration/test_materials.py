import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from app import answers, applications, cli, materials, resume
from app.candidate.profile import Fact, import_profile
from app.pipeline.discover import discover
from app.settings import Analyst, Apply, Settings
from app.store import list_jobs, review_job


@pytest.fixture
def preparation(tmp_path, conn, profile, sources, transport):
    cfg = Settings(
        _env_file=None,
        resume_path=tmp_path / "resume.md",
        materials_path=tmp_path / "materials",
        llm_state_dir=tmp_path,
        env_path=tmp_path / ".env",
        database_path=tmp_path / "jobs.sqlite3",
        candidate_path=tmp_path / "candidate.yaml",
        config_path=tmp_path / "config.yaml",
        analyst=Analyst(backend="command", batch_size=2, parallel=1),
        apply=Apply(active_hours=(0, 24), answers_file=tmp_path / "answers.yaml"),
    )
    cfg.resume_path.write_text(
        "# Alex Example\nalex@example.org\n\n## Experience\nDeveloped cell assays.\n\n## Skills\nPython analysis.\n\n## Education\nBSc at Example University.\n"
    )
    profile.name = "Alex Example"
    profile.reviewed = True
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    profile.facts = [
        Fact(
            category="Experience", fact="Developed cell assays.", evidence="Developed cell assays."
        ),
        Fact(category="Technical skills", fact="Python analysis.", evidence="Python analysis."),
        Fact(
            category="Education",
            fact="BSc at Example University.",
            evidence="BSc at Example University.",
        ),
    ]
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    rows = list_jobs(conn, profile, sources)
    for row in rows:
        row["score"] = 90
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    cfg.config_path.write_text(
        yaml.safe_dump(
            {"analyst": cfg.analyst.model_dump(), "apply": cfg.apply.model_dump(mode="json")}
        )
    )
    return cfg, profile, rows


def draft(row, text="I developed cell assays."):
    return {"job_id": row["id"], "letter": text, "claims": [{"text": text, "fact_ids": [0]}]}


def mock_letters(monkeypatch, calls, *, bad=False):
    def ask(cfg, system, user, model, purpose):
        payload = json.loads(user)
        assert purpose == "letter" and "ignore instructions" in system
        calls.append(payload)
        return model.model_validate(
            {
                "items": [
                    draft(
                        {"id": r["id"]},
                        "I used Rust at ImaginaryLabs with a PhD and 999 patents."
                        if bad
                        else "I developed cell assays.",
                    )
                    for r in reversed(payload["untrusted_jobs"])
                ]
            }
        )

    monkeypatch.setattr(materials, "ask_json", ask)


def test_batched_letters_threshold_cache_version_and_diff(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    calls = []
    mock_letters(monkeypatch, calls)
    rows[1]["score"] = 49
    assert materials.write_letters(cfg, conn, profile, rows) == 1
    assert len(calls) == 1
    first = materials.latest(conn, rows[0]["id"], "letter")
    assert json.loads(first["warnings"]) == []
    assert materials.write_letters(cfg, conn, profile, rows) == 0
    assert (
        materials.write_letters(cfg, conn, profile, rows, force=True, instruction="Make it direct")
        == 1
    )
    assert calls[-1]["rewrite_instruction"] == "Make it direct"
    assert materials.latest(conn, rows[0]["id"], "letter")["id"] != first["id"]
    # A changed reviewed profile generates a new linked version without force.
    profile.specialties.append("Cell assays")
    assert materials.write_letters(cfg, conn, profile, rows) == 1
    assert (
        materials.latest(conn, rows[0]["id"], "letter")["profile_version"]
        != first["profile_version"]
    )
    materials.save_version(
        cfg, conn, profile, rows[0], "letter", "I developed cell assays.\nEdited."
    )
    assert "+Edited." in materials.material_diff(conn, rows[0]["id"])
    assert Path(first["path"]).read_text() == first["content"]


def test_rewrite_once_then_persist_visible_warnings(console_output, preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    calls = []
    mock_letters(monkeypatch, calls, bad=True)
    assert materials.write_letters(cfg, conn, profile, rows[:1]) == 1
    assert len(calls) == 2 and calls[1]["remove_unsupported"]
    item = materials.latest(conn, rows[0]["id"], "letter")
    assert (
        "999" in item["warnings"]
        and "ImaginaryLabs" in item["warnings"]
        and "PhD" in item["warnings"]
    )
    output = io.StringIO()
    console_output(output, width=80)
    cli._show_materials(conn, rows[0])
    assert "WARNING:" in output.getvalue() and "999" in output.getvalue()


def test_rewrite_can_remove_unsupported_claims(preparation, monkeypatch):
    cfg, profile, rows = preparation
    responses = iter([draft(rows[0], "I used Docker for 999 years."), draft(rows[0])])
    monkeypatch.setattr(
        materials,
        "ask_json",
        lambda *a: materials.Letters(items=[materials.Letter.model_validate(next(responses))]),
    )
    result = materials.write_batch(cfg, profile, rows[:1])
    assert result[0][2] == []


def test_invalid_ids_block_failed_batch(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    monkeypatch.setattr(materials, "ask_json", lambda *a: materials.Letters(items=[]))
    with pytest.raises(RuntimeError, match="failed batches"):
        materials.write_letters(cfg, conn, profile, rows)
    assert conn.execute("SELECT count(*) FROM applications WHERE state='blocked'").fetchone()[
        0
    ] == len(rows)
    assert not conn.execute("SELECT * FROM material_versions").fetchall()


def test_unreviewed_or_changed_resume_blocks_generation(preparation, conn):
    cfg, profile, rows = preparation
    profile.reviewed = False
    with pytest.raises(ValueError, match="review"):
        materials.tailored_resume(cfg, conn, profile, rows[0])
    profile.reviewed = True
    cfg.resume_path.write_text("changed")
    with pytest.raises(ValueError, match="Resume changed"):
        materials.write_letters(cfg, conn, profile, rows)


def test_fact_provenance_and_job_only_qualifications(preparation):
    _cfg, profile, rows = preparation
    row = {**rows[0], "description": "PhD in Python with 999 patents."}
    item = materials.Letter.model_validate(draft(row, "I have a PhD and 999 patents."))
    issues = materials.letter_issues(item, profile, row)
    assert any("degree" in x for x in issues)
    item.claims[0].fact_ids = [999]
    assert any("provenance" in x for x in materials.letter_issues(item, profile, row))
    assert any(
        "employer" in x for x in materials.unsupported("Worked at ImaginaryLabs.", "Cell assays")
    )
    assert any(
        "technology" in x
        for x in materials.unsupported("Python analysis", "Cell assays", "Python required")
    )


def test_tailored_resume_preserves_source_structure(preparation, conn):
    cfg, profile, rows = preparation
    source = """# Real Name
real@example.org

## Experience
### Scientist — Bio Labs | 2020–2024
- Assays
- Python analysis
### Associate — Cell Labs | 2018–2020
- Research

## Education
MSc, Example University, 2018
"""
    cfg.resume_path.write_text(source)
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    item = materials.tailored_resume(cfg, conn, profile, rows[0])
    assert item["content"] == source.strip()
    assert materials.resume_source_diff(cfg, item["content"]) == "No differences from resume.md."
    assert "+Extra" in materials.resume_source_diff(cfg, item["content"] + "\nExtra\n")
    assert item["profile_version"] == import_profile(conn, profile)


def test_editor_flags_and_new_version(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    materials.save_version(cfg, conn, profile, rows[0], "letter", "Original")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "some-editor --wait")

    def edit(command, **kwargs):
        assert command[:2] == ["some-editor", "--wait"]
        Path(command[2]).write_text("I developed cell assays.")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(materials.subprocess, "run", edit)
    item = materials.edit_letter(cfg, conn, profile, rows[0])
    assert item["content"] == "I developed cell assays."
    assert (
        conn.execute("SELECT count(*) FROM material_versions WHERE kind='letter'").fetchone()[0]
        == 2
    )


def test_answer_bank_normalization_employer_options_and_sensitive_missing(preparation, conn):
    cfg, profile, rows = preparation
    row, other = rows
    answers.add_question(conn, row["id"], "Do you require visa sponsorship? *", ["Yes", "No"])
    answers.add_question(conn, row["id"], "Salary expectations?")
    assert all(x["source"] == "needs_human" for x in answers.plan(cfg, conn, row))
    answers.save_answer(cfg, "DO YOU REQUIRE VISA SPONSORSHIP?", "No")
    assert answers.plan(cfg, conn, row)[0]["answer"] == ["No"]
    answers.add_question(
        conn, other["id"], "Do you require visa sponsorship?", ["I do", "I do not"]
    )
    assert not answers.plan(cfg, conn, other)[0]["answer"]
    answers.save_answer(cfg, "Do you require visa sponsorship?", "I do not", other["employer"])
    assert answers.plan(cfg, conn, other)[0]["answer"] == ["I do not"]
    assert (
        answers.lookup(
            answers.load_answers(cfg), "Another employer", "Do you require visa sponsorship?"
        )
        == "No"
    )
    snapshot = answers.snapshot(cfg, conn, profile, row)
    assert "Salary" in snapshot["warnings"]
    assert cfg.apply.answers_file.stat().st_mode & 0o777 == 0o600


def test_snapshot_uses_stored_preference_matched_by_normalised_text(preparation, conn):
    cfg, _, rows = preparation
    row = rows[0]
    options = ["I'm based in the UK and happy to work in London", "I would relocate to Oxford"]
    answers.add_question(conn, row["id"], "Which office would you prefer? *", options)
    answers.save_answer(cfg, "Which office would you prefer?*", "  HAPPY to work in London ")
    item = answers.plan(cfg, conn, row)[0]
    assert item["answer"] == [options[0]] and item["source"] == "answers.yaml"


def test_answers_reuse_across_employers(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    for row in rows:
        answers.add_question(conn, row["id"], "A confirmed fact?")
    calls = []
    monkeypatch.setattr("builtins.input", lambda _: calls.append(True) or "User confirmed")
    answers.collect(cfg, conn, profile, rows, interactive=True, output=lambda _: None)
    assert len(calls) == 1
    assert all(not json.loads(materials.latest(conn, r["id"], "answers")["warnings"]) for r in rows)


def ready(cfg, conn, profile, row, monkeypatch):
    materials.save_version(cfg, conn, profile, row, "letter", "I developed cell assays.")
    materials.tailored_resume(cfg, conn, profile, row)
    answers.snapshot(cfg, conn, profile, row)
    monkeypatch.setattr(materials, "browser_available", lambda: True)
    monkeypatch.setattr(materials, "render_pdf", lambda text, out: out.write_bytes(b"fake-pdf"))
    monkeypatch.setattr(materials, "ats_check", lambda *a: [])
    materials.build_resume_pdf(cfg, conn, profile, row)


def test_manual_approval_only_and_confirmed_submission(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    review_job(conn, row["id"], "saved")
    assert (
        conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[0]
        == "saved"
    )
    ready(cfg, conn, profile, row, monkeypatch)
    paths = applications.approve(cfg, conn, profile, row)
    assert len(paths) == 4 and all(Path(p).exists() for p in paths)
    assert row["application_state"] == "approved"
    assert (
        conn.execute(
            "SELECT submitted_at FROM applications WHERE job_id=?", (row["id"],)
        ).fetchone()[0]
        is None
    )
    with pytest.raises(ValueError, match="Type submitted"):
        applications.confirm_submitted(conn, row, "no")
    applications.confirm_submitted(conn, row, "submitted")
    assert row["application_state"] == "submitted"
    assert conn.execute(
        "SELECT submitted_at FROM applications WHERE job_id=?", (row["id"],)
    ).fetchone()[0]


@pytest.mark.parametrize(
    "gate,value,error",
    [
        ("min_score", 99, "threshold"),
        ("max_per_run", 1, "max_per_run"),
    ],
)
def test_apply_gates(preparation, conn, monkeypatch, gate, value, error):
    cfg, profile, rows = preparation
    ready(cfg, conn, profile, rows[0], monkeypatch)
    setattr(cfg.apply, gate, value)
    with pytest.raises(ValueError, match=error):
        applications.approve(
            cfg,
            conn,
            profile,
            rows[0],
            approved_this_run=1,
            now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        )


def test_approval_rejects_stale_changed_missing_and_warned_materials(
    preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    row = rows[0]
    ready(cfg, conn, profile, row, monkeypatch)
    item = materials.latest(conn, row["id"], "letter")
    Path(item["path"]).write_text("changed outside app")
    with pytest.raises(ValueError, match="file changed"):
        applications.approve(cfg, conn, profile, row)
    Path(item["path"]).write_text(item["content"])
    profile.specialties.append("changed")
    with pytest.raises(ValueError, match="stale"):
        applications.approve(cfg, conn, profile, row)
    profile.specialties.pop()
    materials.save_version(cfg, conn, profile, row, "letter", "999 patents", ["Unsupported number"])
    with pytest.raises(ValueError, match="warnings"):
        applications.approve(cfg, conn, profile, row)


def test_pdf_missing_browser_is_actionable(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    monkeypatch.setattr(materials, "browser_available", lambda: False)
    with pytest.raises(ValueError, match="playwright install chromium"):
        materials.build_resume_pdf(cfg, conn, profile, rows[0])
    assert materials.latest(conn, rows[0]["id"], "resume")


def test_real_pdf_and_edit_version(preparation, conn):
    if not materials.browser_available():
        pytest.skip("Chromium unavailable: uv run playwright install chromium")
    cfg, profile, rows = preparation
    item = materials.tailored_resume(cfg, conn, profile, rows[0])
    Path(item["path"]).write_text(item["content"] + "\n<!-- local note -->\n")
    out, issues = materials.build_resume_pdf(cfg, conn, profile, rows[0])
    assert not issues
    assert out.read_bytes().startswith(b"%PDF")
    assert materials.latest(conn, rows[0]["id"], "resume")["id"] != item["id"]


def test_ats_detects_missing_contact_and_section_order(tmp_path, monkeypatch):
    import pdfminer.high_level

    monkeypatch.setattr(
        pdfminer.high_level, "extract_text", lambda _: "Wrong Name Skills Experience"
    )
    issues = materials.ats_check(
        tmp_path / "fake.pdf",
        "# Alex Example\nalex@example.org\n## Experience\nAssays\n## Skills\nPython",
        "Alex Example",
    )
    assert len(issues) == 3


def test_preparation_commands_and_state_visibility(
    console_output, preparation, conn, sources, monkeypatch
):
    cfg, _profile, rows = preparation
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    console_output(output, width=200)
    row = rows[0]
    from app.store import override_score

    override_score(conn, row["id"], 90, "apply", "Synthetic review")
    assert cli.main(["resume", str(row["id"])]) == 0
    assert cli.main(["diff", str(row["id"])]) == 0
    assert cli.main(["answers", "--job", str(row["id"]), "--add", "Visa status?", "--show"]) == 0
    assert cli.main(["state", str(row["id"]), "manual_required"]) == 0
    assert cli.main(["list", "--all-scores", "--application-state", "manual_required"]) == 0
    assert cli.main(["stats"]) == 0
    assert "manual_required" in output.getvalue() and "Apply mode: manual" in output.getvalue()


def test_review_browser_mode_approval_and_submission_keys(
    console_output, preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    row = rows[0]
    cfg.apply.mode = "auto"
    ready(cfg, conn, profile, row, monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    keys = iter("atq")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    monkeypatch.setattr("builtins.input", lambda _: "submitted")
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    output = io.StringIO()
    console_output(output, width=80)
    cli._review(conn, [row], cfg, profile)
    assert opened == []
    assert f"bio-jobs apply {row['id']}" in output.getvalue()
    assert row["application_state"] == "submitted"


@pytest.mark.live
def test_live_synthetic_letter(request, tmp_path):
    if not request.config.getoption("--live") or os.environ.get("BIO_LIVE_LETTER") != "1":
        pytest.skip("Set BIO_LIVE_LETTER=1 and pass --live -m live to spend a letter request")
    from app.candidate.profile import CandidateProfile

    cfg = Settings(
        _env_file=None,
        resume_path=tmp_path / "resume.md",
        env_path=tmp_path / ".env",
        llm_state_dir=tmp_path,
        analyst=Analyst(backend=os.environ.get("BIO_LIVE_BACKEND", "codex"), timeout=120),
    )
    cfg.resume_path.write_text("# Alex Example\nDeveloped cell assays.")
    profile = CandidateProfile(
        name="Alex Example",
        reviewed=True,
        resume_hash=resume.digest(resume.resume_text(cfg)),
        target_titles=["Scientist"],
        facts=[
            Fact(
                category="technical",
                fact="Developed cell assays.",
                evidence="Developed cell assays.",
            )
        ],
    )
    row = {
        "id": 1,
        "title": "Scientist",
        "employer": "Synthetic Bio",
        "description": "Cell assay development in London.",
        "apply_url": "https://example.com/job",
    }
    result = materials.write_batch(cfg, profile, [row])
    assert result[0][1].letter and result[0][1].claims
    assert not any("999" in x for x in result[0][2])
    print(f"Live letter backend: {cfg.analyst.backend}; warnings={result[0][2]}")


def test_rewrite_failure_retains_reviewable_draft(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    calls = []

    def ask(*args):
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError("provider unavailable")
        return materials.Letters(
            items=[materials.Letter.model_validate(draft(rows[0], "I used Docker for 999 years."))]
        )

    monkeypatch.setattr(materials, "ask_json", ask)
    assert materials.write_letters(cfg, conn, profile, rows[:1]) == 1
    item = materials.latest(conn, rows[0]["id"], "letter")
    assert "999" in item["content"] and "Guided rewrite failed" in item["warnings"]


def test_daily_limit_and_pdf_integrity(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    cfg.apply.daily_limit = 1
    for row in rows:
        ready(cfg, conn, profile, row, monkeypatch)
    applications.approve(cfg, conn, profile, rows[0])
    applications.confirm_submitted(conn, rows[0], "submitted")
    with pytest.raises(ValueError, match="daily_limit"):
        applications.approve(cfg, conn, profile, rows[1])
    cfg.apply.daily_limit = 5
    item = materials.latest(conn, rows[1]["id"], "resume")
    Path(item["pdf_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="PDF changed"):
        applications.approve(cfg, conn, profile, rows[1])


def test_changed_answers_require_new_draft(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    ready(cfg, conn, profile, row, monkeypatch)
    answers.add_question(conn, row["id"], "Salary expectation?")
    with pytest.raises(ValueError, match="Screening answers changed"):
        applications.approve(cfg, conn, profile, row)
    answers.snapshot(cfg, conn, profile, row)
    with pytest.raises(ValueError, match="warnings"):
        applications.approve(cfg, conn, profile, row)
    cfg.apply.answers_file.write_text("'Salary expectation?': '  '\n")
    assert not answers.plan(cfg, conn, row)[0]["answer"]
    answers.save_answer(cfg, "Salary expectation?", "User-provided salary")
    answers.snapshot(cfg, conn, profile, row)
    assert applications.approve(cfg, conn, profile, row)


def test_warning_acknowledgement_is_audited_and_hard_checks_remain(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    ready(cfg, conn, profile, row, monkeypatch)
    materials.save_version(cfg, conn, profile, row, "letter", "I achieved 999 results.", ["number"])
    resume = materials.latest(conn, row["id"], "resume")
    conn.execute(
        "UPDATE material_versions SET ats_warnings=? WHERE id=?", ('["ATS issue"]', resume["id"])
    )
    with pytest.raises(applications.UnresolvedWarnings) as caught:
        applications.approve(cfg, conn, profile, row)
    warnings = caught.value.warnings
    assert warnings == {"letter": ["number"], "ATS": ["ATS issue"]}
    assert not conn.execute("SELECT 1 FROM application_approvals").fetchone()
    applications.approve(
        cfg, conn, profile, row, acknowledged_warnings=warnings, expected_ids=caught.value.ids
    )
    approval = conn.execute(
        "SELECT * FROM application_approvals WHERE job_id=?", (row["id"],)
    ).fetchone()
    assert json.loads(approval["acknowledged_warnings"]) == warnings
    event = conn.execute("SELECT detail FROM application_events WHERE action='approval'").fetchone()
    assert json.loads(event[0])["acknowledged_warnings"] == warnings
    applications.validate_materials(cfg, conn, profile, row, acknowledged_warnings=warnings)
    Path(resume["pdf_path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="PDF changed"):
        applications.approve(cfg, conn, profile, row, acknowledged_warnings=warnings)


def test_lexical_check_prose_and_fabrications():
    candidate = "Developed cell assays and led research teams."
    job = "Principal Scientist at Example Bio"
    text = (
        "Dear Hiring Team. Developing robust assays is my focus. "
        "Scientific collaboration supports my work as Principal Scientist at Example Bio."
    )
    assert not materials.unsupported(text, candidate, job)
    warnings = materials.unsupported(
        "I worked at InventedLabs for 27 years using CRISPR after earning a PhD.", candidate, job
    )
    assert any("InventedLabs" in warning for warning in warnings)
    assert any("27" in warning for warning in warnings)
    assert any("CRISPR" in warning for warning in warnings)
    assert any("PhD" in warning for warning in warnings)


@pytest.mark.parametrize("response,approved", [("no", False), ("approve", True)])
def test_review_warning_confirmation(
    console_output, preparation, conn, monkeypatch, response, approved
):
    cfg, profile, rows = preparation
    row = rows[0]
    cfg.apply.mode = "auto"
    ready(cfg, conn, profile, row, monkeypatch)
    materials.save_version(cfg, conn, profile, row, "letter", "999", ["Unsupported number: 999"])
    keys = iter("aq")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    monkeypatch.setattr("builtins.input", lambda _: response)
    output = io.StringIO()
    console_output(output, width=80)
    cli._review(conn, [row], cfg, profile)
    assert "letter warnings:" in output.getvalue()
    assert bool(conn.execute("SELECT 1 FROM application_approvals").fetchone()) is approved


def test_review_provenance_is_readable_and_flags_missing(
    console_output, preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    row = rows[0]
    materials.save_version(
        cfg,
        conn,
        profile,
        row,
        "letter",
        "Claim.",
        provenance={"claims": [{"text": "Claim.", "fact_ids": [0, 999]}]},
    )
    output = io.StringIO()
    console_output(output, width=200)
    cli._show_materials(conn, row, profile)
    text = output.getvalue()
    assert "Claim: Claim." in text
    assert profile.facts[0].fact in text and profile.facts[0].evidence in text
    assert "cited fact 999 no longer exists" in text


def test_score_gate_and_external_import(console_output, preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    console_output(output, width=200)
    assert cli.main(["resume", str(row["id"])]) == 1
    assert "unscored" in output.getvalue() and "bio-jobs analyze" in output.getvalue()
    from app.store import override_score

    override_score(conn, row["id"], 90, "apply", "Reviewed")
    old = materials.tailored_resume(cfg, conn, profile, row)
    Path(old["path"]).write_text(old["content"] + "\nEdited by candidate.\n")
    monkeypatch.setattr("builtins.input", lambda _: "import")
    assert cli.main(["import", str(row["id"]), "--kind", "resume"]) == 0
    new = materials.latest(conn, row["id"], "resume")
    assert new["id"] != old["id"] and "Edited by candidate" in new["content"]
    assert "Edited by candidate" in output.getvalue()
    assert new["pdf_path"] is None


def test_hours_only_unattended(preparation, conn):
    cfg, _, rows = preparation
    cfg.apply.active_hours = (8, 17)
    now = datetime(2026, 11, 15, 23, tzinfo=UTC)
    applications.check_gates(cfg, conn, rows[0], now=now)
    with pytest.raises(ValueError, match="next allowed.*2026-11-16T08:00:00"):
        applications.check_gates(cfg, conn, rows[0], now=now, unattended=True)


def test_assisted_review_offers_fill_after_approval(console_output, preparation, conn, monkeypatch):
    from app import autopilot

    cfg, profile, rows = preparation
    row = rows[0]
    cfg.apply.mode = "assisted"
    ready(cfg, conn, profile, row, monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    keys = iter("afq")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    called = []
    monkeypatch.setattr(autopilot, "run", lambda *args, **kwargs: called.append(kwargs["job"]))
    output = io.StringIO()
    console_output(output, width=200)
    cli._review(conn, [row], cfg, profile)
    assert called == [row["id"]]
    assert "you review and press Submit yourself" in output.getvalue()


@pytest.mark.parametrize("terminal", ["submitted", "unknown_result", "withdrawn"])
def test_terminal_states_survive_preparation_and_reject_state_changes(preparation, conn, terminal):
    cfg, profile, rows = preparation
    row = rows[0]
    materials.state(conn, row["id"], terminal)
    with pytest.raises(ValueError, match="reconciliation"):
        materials.state(conn, row["id"], "saved")
    materials.tailored_resume(cfg, conn, profile, row)
    answers.collect(cfg, conn, profile, [row], interactive=False, output=lambda _: None)
    assert (
        conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[0]
        == terminal
    )
    if terminal == "unknown_result":
        applications.confirm_submitted(conn, row, "submitted")
        assert (
            conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[
                0
            ]
            == "submitted"
        )


def test_unchanged_answer_snapshot_keeps_approval(preparation, conn):
    cfg, profile, rows = preparation
    row = rows[0]
    answers.collect(cfg, conn, profile, [row], interactive=False, output=lambda _: None)
    before = materials.latest(conn, row["id"], "answers")
    materials.state(conn, row["id"], "approved")
    answers.collect(cfg, conn, profile, [row], interactive=False, output=lambda _: None)
    assert materials.latest(conn, row["id"], "answers")["id"] == before["id"]
    assert (
        conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[0]
        == "approved"
    )
    answers.add_question(conn, row["id"], "Synthetic question")
    answers.collect(cfg, conn, profile, [row], interactive=False, output=lambda _: None)
    assert (
        conn.execute("SELECT state FROM applications WHERE job_id=?", (row["id"],)).fetchone()[0]
        == "awaiting_review"
    )


def test_private_artifacts_under_umask(preparation, conn, tmp_path):
    from app import llm
    from app.db import connect
    from app.private import private_file, tighten_configured

    cfg, profile, rows = preparation
    old = os.umask(0o022)
    try:
        result = materials.tailored_resume(cfg, conn, profile, rows[0])
        answers.save_answer(cfg, "Question", "Synthetic answer")
        answers.snapshot(cfg, conn, profile, rows[0])
        llm._event(cfg, "test")
        pdf = tmp_path / "private" / "resume.pdf"
        private_file(pdf)
        db = tmp_path / "new" / "private.sqlite3"
        with connect(db) as other:
            other.execute("CREATE TABLE test (value TEXT)")
            other.execute("INSERT INTO test VALUES ('synthetic')")
            for path in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
                assert path.stat().st_mode & 0o777 == 0o600
        for path in (
            Path(result["path"]),
            Path(materials.latest(conn, rows[0]["id"], "answers")["path"]),
            cfg.apply.answers_file,
            tmp_path / "llm-usage.jsonl",
            pdf,
        ):
            assert path.stat().st_mode & 0o777 == 0o600
        assert Path(result["path"]).parent.stat().st_mode & 0o777 == 0o700
        Path(result["path"]).chmod(0o644)
        tighten_configured(cfg)
        assert Path(result["path"]).stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(old)


def test_institution_capture_stops_at_sentence_and_signoff():
    assert not materials.unsupported(
        "My PhD is from MIT.\n\nSincerely,\nAnna Example", "Anna Example\nPhD, MIT"
    )
    assert "Unverified employer/institution: Imaginary" in materials.unsupported(
        "Worked at Imaginary.\n\nSincerely,\nAnna Example", "Anna Example"
    )


def test_placeholder_name_blocks_materials_and_forms(preparation, conn):
    from app.autopilot import values_for

    cfg, profile, _ = preparation
    profile.name = "Example candidate"
    with pytest.raises(ValueError, match="Set your name"):
        materials.tailored_resume(cfg, conn, profile, {})
    with pytest.raises(ValueError, match="Set your name"):
        materials.write_batch(cfg, profile, [])
    with pytest.raises(ValueError, match="Set your name"):
        values_for(conn, profile, {})


def test_signed_profile_name_is_traced_in_letter(preparation):
    _, profile, rows = preparation
    item = materials.Letter(job_id=rows[0]["id"], letter="Sincerely,\nAlex Example", claims=[])
    assert not any(
        "Untraced name/term" in issue for issue in materials.letter_issues(item, profile, rows[0])
    )


def test_letter_preferences_and_pdf(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    cfg.analyst.letter_style = "direct and warm"
    cfg.analyst.letter_length = "80–120 words"
    calls = []
    mock_letters(monkeypatch, calls)
    materials.write_batch(cfg, profile, [rows[0]])
    assert calls[0]["letter_style"] == "direct and warm"
    assert calls[0]["letter_length"] == "80–120 words"
    item = materials.save_version(cfg, conn, profile, rows[0], "letter", "Dear team,\n\nThank you.")
    monkeypatch.setattr(materials, "browser_available", lambda: True)
    monkeypatch.setattr(materials, "render_pdf", lambda text, out: out.write_bytes(b"%PDF test"))
    out = materials.build_letter_pdf(cfg, conn, profile, rows[0])
    assert out.read_bytes() == b"%PDF test"
    Path(item["path"]).write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        materials.build_letter_pdf(cfg, conn, profile, rows[0])


def test_realistic_two_page_resume_pdf(preparation, conn):
    if not materials.browser_available():
        pytest.skip("Chromium unavailable")
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage

    cfg, profile, rows = preparation
    source = "# Synthetic Scientist\nscientist@example.org\n\n## Experience\n"
    for year in range(2024, 2014, -1):
        source += f"\n### Scientist, Synthetic Labs — {year - 1}–{year}\n"
        source += "".join(f"- Developed assay workflow {i} for research teams.\n" for i in range(6))
    source += "\n## Education\nMSc Biology, Synthetic University, 2014\n"
    cfg.resume_path.write_text(source)
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    out, issues = materials.build_resume_pdf(cfg, conn, profile, rows[0])
    assert not issues
    with out.open("rb") as pdf:
        assert len(list(PDFPage.get_pages(pdf))) == 2
    text = extract_text(str(out))
    assert "Synthetic Scientist" in text and "MSc Biology" in text


def fake_pdf(monkeypatch):
    monkeypatch.setattr(materials, "browser_available", lambda: True)
    monkeypatch.setattr(materials, "render_pdf", lambda text, out: out.write_bytes(b"fake-pdf"))
    monkeypatch.setattr(materials, "ats_check", lambda *a: [])


def versions(conn, row):
    return conn.execute(
        "SELECT kind,id FROM material_versions WHERE job_id=? ORDER BY id", (row["id"],)
    ).fetchall()


def run_prepare(cfg, conn, profile, row, *, interactive=False):
    from app.prepare import prepare_answers, prepare_materials

    result = prepare_materials(cfg, conn, profile, row, output=lambda _: None)
    return prepare_answers(
        cfg, conn, profile, row, result, interactive=interactive, output=lambda _: None
    )


def test_prepare_creates_only_missing_and_keeps_current(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    calls = []
    mock_letters(monkeypatch, calls)
    fake_pdf(monkeypatch)
    resume_item = materials.tailored_resume(cfg, conn, profile, row)
    result = run_prepare(cfg, conn, profile, row)
    assert result.created == ["letter", "resume PDF", "answers"]
    assert result.current == ["resume"] and not result.problems
    assert len(calls) == 1
    assert materials.latest(conn, row["id"], "resume")["id"] == resume_item["id"]
    assert applications.approve(cfg, conn, profile, row)
    before = versions(conn, row)
    again = run_prepare(cfg, conn, profile, row)
    assert not again.created and not again.problems
    assert again.current == ["letter", "resume", "resume PDF", "answers"]
    assert versions(conn, row) == before and len(calls) == 1
    assert row["application_state"] == "approved"


def test_prepare_regenerates_stale_and_rebuilds_pdf(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    calls = []
    mock_letters(monkeypatch, calls)
    fake_pdf(monkeypatch)
    run_prepare(cfg, conn, profile, row)
    old = {kind: materials.latest(conn, row["id"], kind)["id"] for kind in ("letter", "resume")}
    profile.specialties.append("changed preference")
    result = run_prepare(cfg, conn, profile, row)
    assert result.created == ["letter", "resume", "resume PDF", "answers"]
    assert len(calls) == 2
    for kind, previous in old.items():
        assert materials.latest(conn, row["id"], kind)["id"] != previous
    assert applications.approve(cfg, conn, profile, row)
    item = materials.latest(conn, row["id"], "resume")
    Path(item["pdf_path"]).write_bytes(b"tampered")
    result = run_prepare(cfg, conn, profile, row)
    assert result.created == ["resume PDF"] and len(calls) == 2


def test_prepare_never_replaces_edited_files(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    row = rows[0]
    calls = []
    mock_letters(monkeypatch, calls)
    fake_pdf(monkeypatch)
    run_prepare(cfg, conn, profile, row)
    before = versions(conn, row)
    letter = materials.latest(conn, row["id"], "letter")
    resume_item = materials.latest(conn, row["id"], "resume")
    Path(letter["path"]).write_text("My own letter.")
    Path(resume_item["path"]).write_text(resume_item["content"] + "\nEdited.\n")
    profile.specialties.append("stale too")
    result = run_prepare(cfg, conn, profile, row)
    assert f"letter file changed; run bio-jobs import {row['id']} --kind letter" in "".join(
        result.problems
    )
    assert any("--kind resume" in problem for problem in result.problems)
    assert "resume" not in result.created and "resume PDF" not in result.created
    assert len(calls) == 1
    assert [v for v in versions(conn, row) if v["kind"] != "answers"] == [
        v for v in before if v["kind"] != "answers"
    ]
    assert Path(letter["path"]).read_text() == "My own letter."


def test_prepare_command_non_interactive_lists_questions(
    console_output, preparation, conn, monkeypatch
):
    from app.store import override_score

    cfg, _profile, rows = preparation
    row = rows[0]
    override_score(conn, row["id"], 90, "apply", "Synthetic review")
    answers.add_question(conn, row["id"], "Notice period?")
    mock_letters(monkeypatch, [])
    fake_pdf(monkeypatch)
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected prompt"))
    output = io.StringIO()
    console_output(output, width=200)
    assert cli.main(["prepare", str(row["id"])]) == 0
    text = output.getvalue()
    assert "Prepared: letter, resume, resume PDF, answers" in text
    assert "Claim: I developed cell assays." in text
    assert "Notice period?" in text and f"bio-jobs prepare {row['id']}" in text
    for kind in ("letter", "resume", "answers"):
        assert materials.latest(conn, row["id"], kind)


def test_prepare_command_asks_only_unanswered(console_output, preparation, conn, monkeypatch):
    from app.store import override_score

    cfg, _profile, rows = preparation
    row = rows[0]
    override_score(conn, row["id"], 90, "apply", "Synthetic review")
    answers.save_answer(cfg, "Right to work in the UK?", "Yes")
    answers.add_question(conn, row["id"], "Right to work in the UK?")
    answers.add_question(conn, row["id"], "Notice period?")
    mock_letters(monkeypatch, [])
    fake_pdf(monkeypatch)
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "One month")
    console_output(io.StringIO(), width=200)
    assert cli.main(["prepare", str(row["id"])]) == 0
    assert len(asked) == 1
    draft_items = yaml.safe_load(materials.latest(conn, row["id"], "answers")["content"])["items"]
    assert [i["answer"] for i in draft_items] == [["Yes"], ["One month"]]
    assert not json.loads(materials.latest(conn, row["id"], "answers")["warnings"])


@pytest.mark.parametrize("response,approved", [("approve", True), ("no", False)])
def test_review_approve_prepares_then_confirms(
    console_output, preparation, conn, monkeypatch, response, approved
):
    cfg, profile, rows = preparation
    row = rows[0]
    cfg.apply.mode = "auto"
    calls = []
    mock_letters(monkeypatch, calls)
    fake_pdf(monkeypatch)
    keys = iter("aq")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or response)
    output = io.StringIO()
    console_output(output, width=200)
    cli._review(conn, [row], cfg, profile)
    text = output.getvalue()
    assert "Prepare letter first" not in text
    assert "Claim: I developed cell assays." in text
    assert prompts == ["Type approve to approve these materials: "]
    assert len(calls) == 1
    assert bool(conn.execute("SELECT 1 FROM application_approvals").fetchone()) is approved
    assert ("Approval cancelled; materials kept." in text) is not approved
    for kind in ("letter", "resume", "answers"):
        assert materials.latest(conn, row["id"], kind)


def test_review_approve_with_current_materials_needs_no_extra_prompt(
    console_output, preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    row = rows[0]
    cfg.apply.mode = "auto"
    ready(cfg, conn, profile, row, monkeypatch)
    before = versions(conn, row)
    keys = iter("aq")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected prompt"))
    console_output(io.StringIO(), width=200)
    cli._review(conn, [row], cfg, profile)
    assert row["application_state"] == "approved"
    assert versions(conn, row) == before


def test_review_approve_keeps_import_message_for_edited_file(
    console_output, preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    row = rows[0]
    ready(cfg, conn, profile, row, monkeypatch)
    letter = materials.latest(conn, row["id"], "letter")
    Path(letter["path"]).write_text("Edited outside the app.")
    keys = iter("aq")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    monkeypatch.setattr("builtins.input", lambda _: "approve")
    output = io.StringIO()
    console_output(output, width=200)
    cli._review(conn, [row], cfg, profile)
    text = output.getvalue()
    assert f"run bio-jobs import {row['id']} --kind letter" in text
    assert "Approval not recorded." in text
    assert not conn.execute("SELECT 1 FROM application_approvals").fetchone()
    assert Path(letter["path"]).read_text() == "Edited outside the app."

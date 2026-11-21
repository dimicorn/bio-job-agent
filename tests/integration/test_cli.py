import io
import json
import os
import subprocess
import sys

import pytest
import yaml

from app import cli, doctor, wizard
from app.llm import Ping
from app.pipeline.discover import discover
from app.settings import ROOT, Settings
from app.store import list_jobs


@pytest.fixture
def configured(console_output, tmp_path, monkeypatch):
    cfg = Settings(
        _env_file=None,
        database_path=tmp_path / "jobs.sqlite3",
        candidate_path=tmp_path / "candidate.yaml",
        resume_path=tmp_path / "resume.md",
        resume_pdf_path=tmp_path / "resume.pdf",
        config_path=tmp_path / "config.yaml",
        env_path=tmp_path / ".env",
        llm_state_dir=tmp_path,
    )
    cfg.candidate_path.write_text((ROOT / "config/candidate.example.yaml").read_text())
    cfg.config_path.write_text("analyst:\n  backend: command\n  command: /bin/echo\n")
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    console_output(output, width=220)
    return cfg, output


def test_default_and_help_do_not_initialise_or_prompt(configured, monkeypatch):
    cfg, _output = configured
    cfg.config_path.unlink()
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected prompt"))
    assert cli.main([]) == cli.main(["help"]) == 0
    assert not cfg.database_path.exists()


def test_list_filters_export_stats(configured, profile, sources, transport):
    cfg, output = configured
    assert cli.main(["init"]) == 0
    from app.db import connect

    with connect(cfg.database_path) as conn:
        discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    assert cli.main(["list", "--all-scores"]) == 0
    assert "Principal Scientist" in output.getvalue()
    assert "https://" in output.getvalue()
    assert "Posted / discovered" in output.getvalue()
    output.seek(0)
    output.truncate(0)
    assert cli.main(["list", "--title", "no such role"]) == 0
    assert "0 matching roles" in output.getvalue()
    path = cfg.llm_state_dir / "jobs.csv"
    assert cli.main(["export", str(path), "--employer", "Mytos", "--all-scores"]) == 0
    assert "Principal Scientist" in path.read_text(encoding="utf-8-sig")
    assert cli.main(["stats"]) == 0
    assert "Source health" in output.getvalue() and "LLM usage" in output.getvalue()


def test_review_actions_persist_and_rejection_survives_refresh(
    configured, profile, sources, transport, monkeypatch
):
    cfg, output = configured
    from app.db import connect, migrate

    with connect(cfg.database_path) as conn:
        migrate(conn)
        discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    keys = iter("ors")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    assert cli.main(["review", "--all-scores"]) == 0
    assert len(opened) == 1
    with connect(cfg.database_path) as conn:
        rows = list_jobs(conn, profile, sources)
        assert sorted(r["review"] for r in rows) == ["rejected", "saved"]
        conn.execute("UPDATE sources SET next_allowed_at=NULL")
        discover(conn, profile, sources, transport=transport, pause=lambda _: None)
        assert sorted(r["review"] for r in list_jobs(conn, profile, sources)) == [
            "rejected",
            "saved",
        ]
    output.seek(0)
    output.truncate(0)
    monkeypatch.setattr(cli, "read_key", lambda: "q")
    assert cli.main(["review"]) == 0
    assert "No jobs to review" not in output.getvalue()
    keys = iter("lq")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    assert cli.main(["review", "--status", "saved", "--all-scores"]) == 0
    with connect(cfg.database_path) as conn:
        assert any(r["review"] == "later" for r in list_jobs(conn, profile, sources))
    monkeypatch.setattr(cli, "read_key", lambda: "q")
    assert cli.main(["review"]) == 0


def test_review_needs_terminal(configured, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["review"]) == 1


def test_first_run_and_noninteractive_guidance(configured, monkeypatch):
    cfg, output = configured
    cfg.config_path.unlink()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["sync"]) == 1
    assert "setup" in output.getvalue()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    calls = []
    monkeypatch.setattr(wizard, "setup", lambda selected: calls.append(selected) or True)
    assert cli.main(["sync"]) == 0
    assert calls == [cfg]


def test_llm_check_does_not_need_database(configured, monkeypatch):
    from app import llm

    cfg, output = configured
    calls = []
    monkeypatch.setattr(llm, "ask_json", lambda *args: calls.append(args) or Ping(ok=True))
    assert cli.main(["llm-check"]) == 0
    assert "LLM check: OK" in output.getvalue()
    assert len(calls) == 1 and not cfg.database_path.exists()


def test_doctor_is_offline_and_readonly(configured, monkeypatch):
    cfg, output = configured
    monkeypatch.setattr("app.materials.browser_available", lambda: True)
    monkeypatch.setattr(doctor, "check", lambda *a, **k: pytest.fail("Unexpected live call"))
    assert cli.main(["doctor"]) == 1
    assert not cfg.database_path.exists()
    assert "bio-jobs init" in output.getvalue()
    assert cli.main(["init"]) == 0
    assert cli.main(["doctor"]) == 0
    cfg.config_path.write_text("analyst:\n  backend: invalid\n")
    assert cli.main(["doctor"]) == 1


def test_setup_reuses_profile_config_and_secret(configured, monkeypatch):
    cfg, output = configured
    cfg.config_path.unlink()
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    # command primary; no fallback; a tiny check is mocked.
    answers = iter(["9", "/bin/echo", "1", "1", "", "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    checks = []
    monkeypatch.setattr(
        wizard, "check", lambda selected, **kw: checks.append(selected.analyst.backend)
    )
    assert wizard.setup(cfg)
    assert checks == ["command"]
    original = cfg.config_path.read_text()
    profile = cfg.candidate_path.read_text()
    wizard.save_env(cfg, "BIO_TEST_KEY", "secret-$-value")
    monkeypatch.delenv("BIO_TEST_KEY", raising=False)
    assert cfg.secret("BIO_TEST_KEY") == "secret-$-value"
    assert cfg.env_path.stat().st_mode & 0o777 == 0o600
    answers = iter(["n", "n", "n"])
    assert wizard.setup(cfg)
    assert cfg.config_path.read_text() == original
    assert cfg.candidate_path.read_text() == profile
    assert "secret-" not in original and "secret-" not in output.getvalue()
    from app.db import connect

    with connect(cfg.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM candidate_profiles").fetchone()[0] == 1


def test_wizard_saves_api_keys_and_fallback(configured, monkeypatch):
    cfg, _ = configured
    cfg.config_path.unlink()
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda _: "fake-key-for-offline-test")
    answers = iter(
        ["1", "", "6", "deepseek", "my-pi-model", "2", "7", "3", "85", "0,24", "", "", ""]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    checks = []
    monkeypatch.setattr(
        wizard,
        "check",
        lambda selected, **kw: checks.append((selected.analyst.backend, selected.analyst.model)),
    )
    assert wizard.setup(cfg)
    assert checks == [("openai", "deepseek-flash"), ("pi", "my-pi-model")]
    document = yaml.safe_load(cfg.config_path.read_text())
    assert document["analyst"]["openai_extra"] == {"thinking": {"type": "disabled"}}
    assert document["analyst"]["fallback_backend"] == "pi"
    # Only provider differences: the fallback must keep the primary's scoring preferences.
    assert document["analyst"]["fallback_settings"] == {
        "model": "my-pi-model",
        "provider": "deepseek",
        "base_url": None,
        "openai_extra": {},
    }
    assert document["apply"]["mode"] == "assisted"
    assert document["apply"]["daily_limit"] == 7
    assert document["apply"]["active_hours"] == [0, 24]
    assert "fake-key" not in cfg.config_path.read_text()
    assert cfg.secret("DEEPSEEK_API_KEY") == "fake-key-for-offline-test"


def test_setup_script_is_location_independent_and_rerunnable(tmp_path):
    binary = tmp_path / "uv"
    record = tmp_path / "calls.jsonl"
    binary.write_text(
        '#!/usr/bin/env python3\nimport json, os, sys\nwith open(os.environ["BIO_SETUP_LOG"], "a") as f:\n f.write(json.dumps(sys.argv[1:]) + "\\n")\n'
    )
    binary.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "BIO_SETUP_LOG": str(record)}
    script = ROOT / "setup.sh"
    for _ in range(2):
        subprocess.run(
            ["bash", str(script)], cwd=tmp_path, env=env, check=True, capture_output=True
        )
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    if sys.platform != "darwin":
        schedule_calls = [call for call in calls if "app.schedule" in call]
        assert len(schedule_calls) == 2
        assert all(call[-2:] == ["--hours", "10"] for call in schedule_calls)
        calls = [call for call in calls if "app.schedule" not in call]
    assert (
        calls
        == [
            ["sync", "--project", str(ROOT), "--frozen"],
            ["run", "--project", str(ROOT), "playwright", "install", "chromium"],
            ["run", "--project", str(ROOT), "bio-jobs", "init"],
            ["run", "--project", str(ROOT), "bio-jobs", "setup"],
        ]
        * 2
    )


def test_untrusted_display_is_literal():
    assert cli._text("[bold]job[/bold]\x1b[31mred").plain == "[bold]job[/bold]red"


def test_setup_copies_example_once_and_keeps_edited_profile(configured, monkeypatch):
    cfg, _ = configured
    root = cfg.llm_state_dir
    (root / "config").mkdir()
    example = root / "config/candidate.example.yaml"
    example.write_text(cfg.candidate_path.read_text())
    cfg.candidate_path = example
    monkeypatch.setattr(wizard, "ROOT", root)
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    cfg.config_path.write_text(cfg.config_path.read_text() + "\napply:\n  mode: manual\n")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(wizard, "check", lambda *a, **kw: None)
    assert wizard.setup(cfg)
    candidate = root / "config/candidate.yaml"
    profile = yaml.safe_load(candidate.read_text())
    profile["reviewed"] = True
    candidate.write_text(yaml.safe_dump(profile))
    assert wizard.setup(cfg)
    assert yaml.safe_load(candidate.read_text())["reviewed"] is True
    assert cfg.secret("BIO_CANDIDATE_PATH") == "config/candidate.yaml"


def test_invalid_config_and_missing_keys_are_diagnosed(configured):
    cfg, output = configured
    cfg.config_path.write_text("- not a mapping\n")
    assert cli.main(["doctor"]) == 1
    assert "Run uv run bio-jobs setup" in output.getvalue()
    cfg.config_path.write_text("analyst: [broken\n")
    assert cli.main(["doctor"]) == 1
    cfg.config_path.write_text(
        "analyst:\n  backend: openai\n  model: tiny\n  base_url: https://test.invalid/v1\n  api_key_env: BIO_MISSING_TEST_KEY\n"
    )
    assert cli.main(["doctor"]) == 1
    assert "BIO_MISSING_TEST_KEY" in output.getvalue()


def test_firms_validation_dispatch_needs_no_llm_or_database(configured, monkeypatch):
    cfg, _ = configured
    cfg.config_path.unlink()
    calls = []
    monkeypatch.setattr(cli, "connect", lambda *a: pytest.fail("Unexpected database access"))
    monkeypatch.setattr(
        cli, "run_firms_command", lambda args, **kw: calls.append(args.validate) or 1
    )
    assert cli.main(["firms", "--validate"]) == 1
    assert calls == [True]
    assert not cfg.database_path.exists()


def test_deepseek_preset_and_doctor_warning(configured, monkeypatch):
    cfg, output = configured
    monkeypatch.setattr(wizard, "_key", lambda *args: None)
    monkeypatch.setattr(wizard, "_ask", lambda prompt, default="": default)
    selected = wizard._choose(cfg, 0)
    assert selected.model_dump()["openai_extra"] == {"thinking": {"type": "disabled"}}
    document = yaml.safe_load(cfg.config_path.read_text())
    document["analyst"] = selected.model_dump()
    document["analyst"]["openai_extra"] = {}
    cfg.config_path.write_text(yaml.safe_dump(document))
    doctor.run(cfg)
    assert "thinking" in output.getvalue() and "disabled" in output.getvalue()


def test_doctor_warns_about_example_name(configured):
    cfg, output = configured
    profile = yaml.safe_load(cfg.candidate_path.read_text())
    profile["name"] = "Example candidate"
    cfg.candidate_path.write_text(yaml.safe_dump(profile))
    doctor.run(cfg)
    assert "Candidate name" in output.getvalue()
    assert "set name:" in output.getvalue()


def test_fallback_overrides_keep_shared_scoring_settings():
    primary = wizard.Analyst(
        backend="openai", model="main", base_url="https://a.invalid/v1", preferences="London only"
    )
    fallback = wizard.Analyst(backend="claude_code", model="backup")
    overrides = wizard.fallback_overrides(primary, fallback)
    assert overrides == {"model": "backup", "base_url": None}
    primary.fallback_backend, primary.fallback_settings = "claude_code", overrides
    from app.llm import fallback_cfg

    cfg = Settings(_env_file=None, analyst=primary)
    selected = fallback_cfg(cfg).analyst
    assert selected.preferences == "London only" and selected.base_url is None


def test_doctor_pending_migrations_warn_and_next_steps(configured, monkeypatch, make_pdf):
    from app.db import MIGRATIONS, connect, migrate
    from app.resume import digest

    cfg, output = configured
    monkeypatch.setattr("app.materials.browser_available", lambda: True)
    with connect(cfg.database_path) as conn:
        migrate(conn)
        conn.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            (max(MIGRATIONS.glob("*.sql")).name,),
        )
    rows, step = doctor.collect(cfg)
    statuses = {name: (status, detail) for name, status, detail in rows}
    assert statuses["Database"][0] == "WARN" and "automatically" in statuses["Database"][1]
    assert step == f"Save your resume PDF at {cfg.resume_pdf_path}"
    make_pdf(cfg.resume_pdf_path, [("Jane Doe", 18, 40, 50), ("Principal Scientist", 10, 40, 70)])
    assert doctor.collect(cfg)[1] == "uv run bio-jobs resume-from-pdf"
    cfg.resume_path.write_text("Jane Doe\nPrincipal Scientist")
    assert doctor.collect(cfg)[1] == "uv run bio-jobs extract-profile --force"
    profile = yaml.safe_load(cfg.candidate_path.read_text())
    profile["resume_hash"] = digest("Jane Doe\nPrincipal Scientist")
    cfg.candidate_path.write_text(yaml.safe_dump(profile))
    assert "set reviewed: true" in doctor.collect(cfg)[1]
    profile["reviewed"] = True
    cfg.candidate_path.write_text(yaml.safe_dump(profile))
    assert doctor.collect(cfg)[1].startswith("uv run bio-jobs scan")
    cfg.resume_path.write_text("Jane Doe\nSenior Principal Scientist")
    rows, step = doctor.collect(cfg)
    assert step == "uv run bio-jobs extract-profile --force"
    assert any(name == "Resume" and "changed" in detail for name, _s, detail in rows)
    assert cli.main(["doctor"]) == 0
    assert "Next step: uv run bio-jobs extract-profile --force" in output.getvalue()


def test_setup_shows_current_choices_and_offers_extraction(configured, monkeypatch):
    cfg, output = configured
    cfg.config_path.write_text(cfg.config_path.read_text() + "apply:\n  mode: assisted\n")
    cfg.resume_path.write_text("Jane Doe\nPrincipal Scientist")
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(wizard, "check", lambda *a, **kw: None)
    prompts = []
    answers = iter(["n", "n", "n", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or next(answers))
    calls = []

    def extracted(selected, conn, profile, target, *, force):
        calls.append((target, force))
        return profile.model_copy(update={"name": "Jane Doe"}), 0

    monkeypatch.setattr("app.resume.extract_to_file", extracted)
    assert wizard.setup(cfg)
    text = output.getvalue()
    assert "Current LLM: command" in text and "mode assisted" in text
    assert calls == [(cfg.candidate_path, True)]
    assert "sends the resume to command" in prompts[-1]
    assert "Next step:" in text


def synthetic_cv(make_pdf, path):
    return make_pdf(
        path,
        [
            ("Jane Doe", 18, 40, 50),
            ("jane@example.org | London", 10, 40, 70),
            ("Experience", 13, 40, 100),
            ("Example Bio Ltd", 10, 40, 118),
            ("2019 - present", 10, 480, 118),
            ("Principal Scientist", 10, 40, 131),
            ("• Led assay development for", 10, 50, 146),
            ("oncology programmes.", 10, 58, 158),
        ],
    )


def test_setup_fills_resume_md_from_pdf_and_skips_extraction(configured, monkeypatch, make_pdf):
    cfg, output = configured
    synthetic_cv(make_pdf, cfg.resume_pdf_path)
    before = cfg.resume_pdf_path.read_bytes()
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(wizard, "check", lambda *a, **kw: None)
    answers = iter(["n", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        "app.resume.extract_to_file", lambda *a, **k: pytest.fail("Review resume.md first")
    )
    cfg.config_path.write_text(cfg.config_path.read_text() + "apply:\n  mode: manual\n")
    assert wizard.setup(cfg)
    text = cfg.resume_path.read_text()
    assert text.startswith("# Jane Doe\n") and "## Experience" in text
    assert "- Led assay development for oncology programmes." in text
    assert cfg.resume_pdf_path.read_bytes() == before
    assert "extract-profile --force" in output.getvalue()


def test_setup_asks_before_replacing_a_filled_resume(configured, monkeypatch, make_pdf):
    cfg, _output = configured
    synthetic_cv(make_pdf, cfg.resume_pdf_path)
    cfg.resume_path.write_text("Jane Doe\nMy hand-written resume")
    cfg.config_path.write_text(cfg.config_path.read_text() + "apply:\n  mode: manual\n")
    monkeypatch.setattr(wizard.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(wizard, "check", lambda *a, **kw: None)
    prompts = []
    answers = iter(["n"] * 5)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or next(answers))
    wizard.setup(cfg)
    assert any("Replace" in prompt for prompt in prompts)
    assert cfg.resume_path.read_text() == "Jane Doe\nMy hand-written resume"


def test_resume_from_pdf_command_never_overwrites_without_force(configured, make_pdf):
    cfg, output = configured
    synthetic_cv(make_pdf, cfg.resume_pdf_path)
    assert cli.main(["resume-from-pdf"]) == 0
    assert cfg.resume_path.read_text().startswith("# Jane Doe")
    assert "extract-profile --force" in output.getvalue()
    cfg.resume_path.write_text("Edited by Jane")
    assert cli.main(["resume-from-pdf"]) == 1
    assert "use --force" in output.getvalue()
    assert cfg.resume_path.read_text() == "Edited by Jane"
    assert cli.main(["resume-from-pdf", "--force"]) == 0
    assert "## Experience" in cfg.resume_path.read_text()


def test_resume_command_reports_missing_and_image_only_pdf(configured, make_pdf):
    cfg, output = configured
    assert cli.main(["resume"]) == 1
    assert "Resume PDF missing" in output.getvalue()
    make_pdf(cfg.resume_pdf_path, [])
    assert cli.main(["resume"]) == 1
    assert "no extractable text" in output.getvalue()


def test_keyword_match_in_list_export_and_review(
    configured, profile, sources, transport, monkeypatch
):
    import csv

    from app.db import connect

    cfg, output = configured
    assert cli.main(["init"]) == 0
    with connect(cfg.database_path) as conn:
        discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    assert cli.main(["list", "--all-scores"]) == 0
    assert "Resume keywords" in output.getvalue() and "no resume.md" in output.getvalue()

    cfg.resume_path.write_text("# Synthetic\nDrug discovery scientist.", encoding="utf-8")
    output.seek(0)
    output.truncate(0)
    assert cli.main(["list", "--all-scores"]) == 0
    # Synthetic fixtures mention drug discovery and a multidisciplinary team.
    assert "25% (1/4)" in output.getvalue() and "cross-functional" in output.getvalue()
    with connect(cfg.database_path) as conn:
        cached = conn.execute("SELECT count(*) FROM keyword_matches").fetchone()[0]
    assert cached >= 1

    path = cfg.llm_state_dir / "jobs.csv"
    assert cli.main(["export", str(path), "--all-scores"]) == 0
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert {"keyword_match", "keywords_present", "keywords_missing"} <= set(rows[0])
    assert any("drug discovery" in r["keywords_present"] for r in rows)

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "read_key", lambda: "q")
    output.seek(0)
    output.truncate(0)
    assert cli.main(["review", "--all-scores"]) == 0
    text = output.getvalue()
    assert "Resume keywords:" in text and "not part of the score" in text
    assert "In resume: drug discovery" in text

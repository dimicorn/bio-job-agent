import io
import json
import os
import subprocess

import pytest
import yaml
from rich.console import Console

from app import cli, doctor, wizard
from app.llm import Ping
from app.pipeline.discover import discover
from app.settings import ROOT, Settings
from app.store import list_jobs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    cfg = Settings(
        _env_file=None,
        database_path=tmp_path / "jobs.sqlite3",
        candidate_path=tmp_path / "candidate.yaml",
        resume_path=tmp_path / "resume.md",
        config_path=tmp_path / "config.yaml",
        env_path=tmp_path / ".env",
        llm_state_dir=tmp_path,
    )
    cfg.candidate_path.write_text((ROOT / "config/candidate.example.yaml").read_text())
    cfg.config_path.write_text("analyst:\n  backend: command\n  command: /bin/echo\n")
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    for module in (cli, doctor, wizard):
        monkeypatch.setattr(module, "console", Console(file=output, width=220, markup=False))
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
    assert cli.main(["review"]) == 0
    assert "No jobs to review" in output.getvalue()
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
    answers = iter(["9", "/bin/echo", "1"])
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
    answers = iter(["n"])
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
    answers = iter(["1", "", "6", "deepseek", "my-pi-model"])
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
    assert document["analyst"]["fallback_backend"] == "pi"
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
    assert (
        calls
        == [
            ["sync", "--project", str(ROOT), "--frozen"],
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

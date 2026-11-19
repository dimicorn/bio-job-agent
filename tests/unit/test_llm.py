import json
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from app import llm
from app.settings import Analyst, Settings


class Ping(BaseModel):
    ok: bool


class Nested(BaseModel):
    items: list[Ping]
    note: str | None = None


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("BIO_ANALYST", raising=False)
    llm._FORMAT.clear()
    llm._limited.clear()
    return Settings(
        _env_file=None,
        env_path=tmp_path / ".env",
        llm_state_dir=tmp_path,
        analyst=Analyst(backend="openai", base_url="https://test.invalid/v1", model="tiny"),
    )


def test_schema_and_validation():
    schema = llm._schema(Nested)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items", "note"]
    assert schema["$defs"]["Ping"]["additionalProperties"] is False
    assert llm._parse('```json\n{"ok":true}\n```', Ping).ok
    for text in ('{"other":1}', '{"ok":"yes"}', "not JSON"):
        with pytest.raises(RuntimeError, match="schema"):
            llm._parse(text, Ping)


def test_openai_fallback_cache_usage_and_key(cfg, monkeypatch):
    cfg.analyst.api_key_env = "BIO_TEST_SECRET"
    cfg.env_path.write_text("export BIO_TEST_SECRET='private-key'\n")
    monkeypatch.delenv("BIO_TEST_SECRET", raising=False)
    calls = []

    def post(url, **kw):
        calls.append(kw["json"])
        assert url == "https://test.invalid/v1/chat/completions"
        assert kw["headers"]["Authorization"] == "Bearer private-key"
        assert "untrusted" in kw["json"]["messages"][0]["content"]
        assert kw["json"]["messages"][1]["content"] == "job text"
        if len(calls) <= 2:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(
            200,
            json={
                "model": "actual",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 1},
                    "cost": 0.01,
                },
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.ask_json(cfg, "system", "job text", Ping).ok
    llm._FORMAT.clear()  # Simulate a new process reading the negotiated format.
    assert llm.ask_json(cfg, "system", "job text", Ping).ok
    assert [c.get("response_format", {}).get("type") for c in calls] == [
        "json_schema",
        "json_object",
        None,
        None,
    ]
    events = [
        json.loads(x) for x in (cfg.llm_state_dir / "llm-usage.jsonl").read_text().splitlines()
    ]
    assert events[0]["input"] == 5 and events[0]["cached"] == 1
    assert events[0]["model"] == "actual" and events[0]["cost_usd"] == 0.01
    assert "private-key" not in json.dumps(events) and "job text" not in json.dumps(events)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_openai_errors_do_not_downgrade(cfg, monkeypatch, status):
    calls = []

    def post(*args, **kw):
        calls.append(1)
        return httpx.Response(status, text="failure")

    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(RuntimeError):
        llm.ask_json(cfg, "s", "u", Ping)
    assert len(calls) == 1


def test_unrelated_bad_request_is_not_schema_fallback(cfg, monkeypatch):
    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: httpx.Response(400, text="unknown model")
    )
    with pytest.raises(RuntimeError, match="400"):
        llm.ask_json(cfg, "s", "u", Ping)


def test_openai_strict_and_json_object_success(cfg, monkeypatch):
    calls = []

    def post(*args, **kw):
        calls.append(kw["json"])
        if len(calls) == 1:
            return httpx.Response(422, text="json_schema unsupported")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert calls[1]["response_format"] == {"type": "json_object"}


def test_claude_api(cfg, monkeypatch):
    cfg.analyst = Analyst(backend="api", model="claude-test")
    cfg.env_path.write_text("ANTHROPIC_API_KEY=test-only\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def post(url, **kw):
        assert url == "https://api.anthropic.com/v1/messages"
        assert kw["headers"]["x-api-key"] == "test-only"
        assert kw["json"]["tool_choice"]["name"] == "structured_answer"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "tool_use", "name": "structured_answer", "input": {"ok": True}}
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2, "cache_read_input_tokens": 1},
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.ask_json(cfg, "s", "u", Ping).ok


@pytest.mark.parametrize("backend", ["codex", "claude_code", "command", "opencode", "pi"])
def test_cli_backends_are_isolated_and_validated(cfg, monkeypatch, backend):
    cfg.analyst = Analyst(
        backend=backend,
        provider="deepseek",
        model="deepseek-flash",
        command="my-model --json",
        codex_model="codex-test",
    )
    cfg.env_path.write_text("DEEPSEEK_API_KEY=test-only\n")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        assert Path(kw["cwd"]).name == "work"
        assert Path(kw["cwd"]).is_dir()
        if cmd == ["opencode", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "opencode v2.3.0\n", "")
        assert "untrusted" in kw["input"].lower()
        if backend != "claude_code":
            assert "untrusted source data (json string)" in kw["input"].lower()
        if backend == "codex":
            assert "--ignore-user-config" in cmd and cmd[cmd.index("-s") + 1] == "read-only"
            assert cmd[cmd.index("-m") + 1] == "codex-test"
            Path(cmd[cmd.index("-o") + 1]).write_text('{"ok":true}')
            stdout = json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}}
            )
        elif backend == "claude_code":
            assert cmd[cmd.index("--tools") + 1] == ""
            assert "--strict-mcp-config" in cmd
            assert "ANTHROPIC_API_KEY" not in kw["env"]
            stdout = json.dumps(
                {
                    "structured_output": {"ok": True},
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                    "total_cost_usd": 0.02,
                }
            )
        elif backend == "opencode":
            assert cmd[cmd.index("-m") + 1] == "deepseek/deepseek-flash"
            assert cmd[cmd.index("--format") + 1] == "json"
            config = json.loads((Path(kw["cwd"]) / "opencode.json").read_text())
            assert config["agents"]["bio-json"]["permissions"][0]["effect"] == "deny"
            assert kw["env"]["DEEPSEEK_API_KEY"] == "test-only"
            stdout = "\n".join(
                json.dumps(e)
                for e in [
                    {"type": "text", "part": {"text": '{"ok":true}'}},
                    {
                        "type": "step_finish",
                        "part": {"tokens": {"input": 4, "output": 2}, "cost": 0},
                    },
                ]
            )
        elif backend == "pi":
            assert all(
                flag in cmd
                for flag in (
                    "-p",
                    "--no-tools",
                    "--no-extensions",
                    "--no-mcp",
                    "--no-context-files",
                )
            )
            assert cmd[cmd.index("--provider") + 1] == "deepseek"
            assert kw["env"]["DEEPSEEK_API_KEY"] == "test-only"
            stdout = json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "model": "deepseek-flash",
                        "content": [{"type": "text", "text": '{"ok":true}'}],
                        "usage": {"input": 4, "output": 2},
                    },
                }
            )
        else:
            assert cmd == ["my-model", "--json"]
            stdout = '{"ok":true}'
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(llm.subprocess, "run", run)
    assert llm.ask_json(cfg, "s", "job text mentioning rate limit and login", Ping).ok
    assert all(not Path(p).exists() for cmd in calls for p in cmd if p.endswith("answer.json"))


def test_opencode_v1_config(cfg, monkeypatch):
    cfg.analyst = Analyst(backend="opencode", provider="opencode", model="free")

    def run(cmd, **kw):
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1.19.5", "")
        config = json.loads((Path(kw["cwd"]) / "opencode.json").read_text())
        assert config["agent"]["bio-json"]["permission"] == {"*": "deny"}
        assert "--standalone" not in cmd
        return subprocess.CompletedProcess(
            cmd, 0, '{"type":"text","part":{"text":"{\\"ok\\":true}"}}', ""
        )

    monkeypatch.setattr(llm.subprocess, "run", run)
    assert llm.ask_json(cfg, "s", "u", Ping).ok


@pytest.mark.parametrize("backend", ["codex", "claude_code", "command", "opencode", "pi"])
def test_cli_limit_errors(cfg, monkeypatch, backend):
    cfg.analyst = Analyst(backend=backend, provider="test", model="test", command="test-model")

    def run(cmd, **kw):
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1.19.5", "")
        if backend == "pi":
            event = {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "rate limit exceeded",
                },
            }
        elif backend == "claude_code":
            event = {"is_error": True, "result": "rate limit exceeded"}
        else:
            event = {"type": "turn.failed", "error": {"message": "rate limit exceeded"}}
        return subprocess.CompletedProcess(cmd, 1, json.dumps(event), "rate limit exceeded")

    monkeypatch.setattr(llm.subprocess, "run", run)
    with pytest.raises(llm.LimitReached):
        llm.ask_json(cfg, "s", "u", Ping)


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("model", 1)])
def test_cli_process_errors(cfg, monkeypatch, error):
    cfg.analyst = Analyst(backend="command", command="test-model")

    def run(*a, **kw):
        raise error

    monkeypatch.setattr(llm.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="missing|timed out"):
        llm.ask_json(cfg, "s", "u", Ping)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("try again at 4:37 PM.", datetime(2026, 10, 20, 16, 37)),
        ("try again at 3:10 PM.", datetime(2026, 10, 21, 15, 10)),
        ("try again at Oct 21st, 2026 9:05 AM.", datetime(2026, 10, 21, 9, 5)),
        ("try again in 2 hours 15 minutes.", datetime(2026, 10, 20, 17, 52)),
        ("rate limit exceeded", None),
    ],
)
def test_reset_at(message, expected):
    assert llm.reset_at(message, datetime(2026, 10, 20, 15, 37)) == expected


def test_http_failover_persistence_expiry_and_settings(cfg, monkeypatch):
    cfg.analyst.fallback_backend = "openai"
    cfg.analyst.fallback_settings = {"base_url": "https://fallback.invalid/v1", "model": "backup"}
    now = [1000]
    monkeypatch.setattr(llm.time, "time", lambda: now[0])
    calls = []

    def post(url, **kw):
        calls.append((url, kw["json"]["model"]))
        if url.startswith("https://test"):
            return httpx.Response(429, headers={"Retry-After": "60"}, text="rate limit exceeded")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert len(calls) == 3 and calls[-1][1] == "backup"
    llm._limited.clear()  # Simulate another process.
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert len(calls) == 4
    assert list(json.loads((cfg.llm_state_dir / llm.LIMITS_FILE).read_text()).values()) == [1060]
    now[0] = 1061
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert calls[-2][1] == "tiny" and cfg.analyst.model == "tiny"


def test_failover_only_for_limits_and_unknown_reset(cfg, monkeypatch):
    cfg.analyst.fallback_backend = "command"
    cfg.analyst.fallback_settings = {"command": "fallback-model"}
    calls = []

    def backend(selected, b, *a):
        calls.append(b)
        if b == "openai":
            raise llm.LimitReached("quota exceeded")
        return Ping(ok=True)

    monkeypatch.setattr(llm, "ask_backend", backend)
    llm.ask_json(cfg, "s", "u", Ping)
    llm.ask_json(cfg, "s", "u", Ping)
    assert calls == ["openai", "command", "command"]
    assert json.loads((cfg.llm_state_dir / llm.LIMITS_FILE).read_text()) == {}
    llm._limited.clear()

    def invalid(*a):
        raise RuntimeError("invalid answer")

    monkeypatch.setattr(llm, "ask_backend", invalid)
    with pytest.raises(RuntimeError, match="invalid answer"):
        llm.ask_json(cfg, "s", "u", Ping)


def test_settings_secrets_and_fallback_validation(cfg, monkeypatch):
    cfg.env_path.write_text("export TEST_LLM_KEY='from-file'\n")
    monkeypatch.setenv("TEST_LLM_KEY", "from-env")
    assert cfg.secret("TEST_LLM_KEY") == "from-env"
    monkeypatch.delenv("TEST_LLM_KEY")
    assert cfg.secret("TEST_LLM_KEY") == "from-file"
    cfg.config_path = cfg.llm_state_dir / "config.yaml"
    cfg.config_path.write_text(
        "analyst:\n  backend: codex\n  fallback_settings:\n    timeout: -1\n"
    )
    with pytest.raises(ValueError):
        cfg.with_config()


def test_opencode_retry_metadata_persists(cfg, monkeypatch):
    cfg.analyst = Analyst(
        backend="opencode",
        provider="opencode",
        model="free",
        fallback_backend="command",
        fallback_settings={"command": "backup"},
    )
    monkeypatch.setattr(llm.time, "time", lambda: 1000)

    def run(cmd, **kw):
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "v2.3.0", "")
        if cmd[0] == "backup":
            return subprocess.CompletedProcess(cmd, 0, '{"ok":true}', "")
        config = json.loads(kw["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["agents"]["bio-json"]["system"] == llm.NO_TOOLS
        assert config["policies"][0]["effect"] == "deny"
        error = {
            "type": "error",
            "error": {
                "type": "provider.free-limit",
                "status": 429,
                "message": "Free usage limit reached.",
                "retryAfterMs": 60000,
            },
        }
        return subprocess.CompletedProcess(cmd, 1, json.dumps(error), "")

    monkeypatch.setattr(llm.subprocess, "run", run)
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    assert list(json.loads((cfg.llm_state_dir / llm.LIMITS_FILE).read_text()).values()) == [1060]


def test_missing_usage_never_becomes_a_zero_cost_estimate(cfg, monkeypatch):
    cfg.analyst.input_cost_per_million = 1
    cfg.analyst.output_cost_per_million = 2
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}
        ),
    )
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    event = json.loads((cfg.llm_state_dir / "llm-usage.jsonl").read_text())
    assert event["cost_usd"] is None and not event["usage_available"]


@pytest.mark.parametrize("content", ["", '{"ok":', '{"ok":true}'])
def test_openai_length_preserves_usage_and_reports_cutoff(cfg, monkeypatch, content):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": content}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 12000},
            },
        ),
    )
    with pytest.raises(
        llm.AnswerTerminated, match=r"openai \(tiny\): answer cut off at max_tokens=12000"
    ):
        llm.ask_json(cfg, "private prompt", "private CV", Ping, "profile")
    event = json.loads((cfg.llm_state_dir / "llm-usage.jsonl").read_text())
    assert (event["status"], event["finish_reason"], event["input"], event["output"]) == (
        "error",
        "length",
        12,
        12000,
    )
    assert "private" not in json.dumps(event)


def test_openai_stop_and_other_empty_finish(cfg, monkeypatch):
    def respond(reason, content):
        monkeypatch.setattr(
            llm.httpx,
            "post",
            lambda *a, **k: httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": reason, "message": {"content": content}}],
                },
            ),
        )

    respond("stop", '{"ok":true}')
    assert llm.ask_json(cfg, "s", "u", Ping).ok
    respond("content_filter", None)
    with pytest.raises(llm.AnswerTerminated, match="terminated without usable content"):
        llm.ask_json(cfg, "s", "u", Ping)


def test_api_max_tokens_preserves_usage(cfg, monkeypatch):
    cfg.analyst = Analyst(backend="api", model="claude-test")
    cfg.env_path.write_text("ANTHROPIC_API_KEY=test-only\n")
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "stop_reason": "max_tokens",
                "content": [],
                "usage": {"output_tokens": 100},
            },
        ),
    )
    with pytest.raises(llm.AnswerTerminated, match="answer cut off"):
        llm.ask_json(cfg, "s", "u", Ping)
    event = json.loads((cfg.llm_state_dir / "llm-usage.jsonl").read_text())
    assert event["finish_reason"] == "max_tokens" and event["output"] == 100

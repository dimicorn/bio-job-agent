# Structured LLM backends

Run `./setup.sh` once, or `uv run bio-jobs setup` to change settings. The wizard writes `analyst:` to gitignored `config.yaml`, keeps credentials in the project's gitignored `.env` (mode 600), imports the candidate example, and checks the primary and optional fallback independently. Existing profile files are never overwritten. Environment variables take precedence over `.env`; `.env` is parsed as data, never sourced by a shell.

| Backend | Configuration / requirement |
| --- | --- |
| `codex` | Installed CLI and `codex login`; optional `codex_model`, `codex_effort` |
| `claude_code` | Installed `claude`, signed in through its CLI; optional `model` |
| `api` | Claude Messages API, `model`, `ANTHROPIC_API_KEY` (or `api_key_env`) |
| `openai` | Any OpenAI-compatible chat completions API: `base_url`, `model`, optional `api_key_env` |
| `opencode` | Installed OpenCode, `provider`, `model`; credentials through its auth or `.env` |
| `pi` | Installed Pi, `provider`, `model`; credentials through its auth or `.env` |
| `command` | `command`: prompt on stdin, one JSON object on stdout; no shell expansion |

Discovery and terminal review do not call the LLM. `extract-profile` extracts resume facts for mandatory factual review; formatting differences in supporting excerpts are normalized, unsupported facts are dropped and counted, and saved evidence comes from the resume; `analyze` scores stored eligible jobs and `scan` syncs then scores. These commands send candidate data to the configured backend. Document drafts remain deferred. Use `uv run bio-jobs llm-check` for one tiny structured request; `doctor` is offline unless `--online` is explicit. `stats` groups usage and known costs by backend, model and purpose, including scoring.

## DeepSeek: direct API

```yaml
analyst:
  backend: openai
  base_url: https://api.deepseek.com/v1
  model: deepseek-flash
  api_key_env: DEEPSEEK_API_KEY
```

The [official DeepSeek API guide](https://api-docs.deepseek.com/) documents OpenAI-compatible requests and the current `deepseek-flash` model alias. Enter your key in the wizard or add `DEEPSEEK_API_KEY=...` to `.env`; never put a key in YAML. Direct DeepSeek flash and pro extraction have been live-verified using synthetic CV data. The setup preset disables thinking with `openai_extra: {thinking: {type: disabled}}`; DeepSeek enables thinking by default, and reasoning consumes the same output token budget as the answer. Re-enable it explicitly if desired, allowing for greater cost and latency.

Other OpenAI-compatible services use the same fields. For a keyless local endpoint, use e.g. `base_url: http://localhost:11434/v1`, the installed model ID and `api_key_env: null`. Optional request fields such as temperature go in `openai_extra`; the adapter owns model/messages/response format. API output budgets use `max_tokens` (default 12000 for detailed scoring batches), and each request has a configurable `timeout` (default 120 seconds). If an answer is cut off, the error identifies the backend, model and `max_tokens`; raise `analyst.max_tokens`, disable thinking, or reduce scoring `batch_size`. A larger budget can increase cost and latency. Failed scoring batches remain unscored.

## DeepSeek through OpenCode

```yaml
analyst:
  backend: opencode
  provider: opencode
  model: deepseek-v4.1-flash-free
  api_key_env: null
```

For a paid DeepSeek provider, choose `provider: deepseek`, its model ID from `opencode models`, and `api_key_env: DEEPSEEK_API_KEY`. Provider/model names depend on the CLI's catalogue.

The adapter uses `opencode run -m provider/model --format json --agent bio-json`, sends the prompt on stdin, and reads text and usage from JSON events. Installed help was verified locally: **OpenCode v2.3.0** (the launch brief described v1.19.5). It selects v1 `agent`/`permission` or v2 `agents`/`permissions` by the CLI version, denies all actions in a custom agent and runs in an empty temporary working directory. V2 uses `--standalone` to avoid attaching to the background service. The temporary config is also passed through `OPENCODE_CONFIG` and `OPENCODE_CONFIG_CONTENT`, so private-server startup retains the restrictions. Legacy config discovery uses a temporary directory; V2 additionally denies tool, MCP, plugin and skill use through policies. Normal provider auth storage remains available. See [OpenCode permissions](https://opencode.ai/docs/permissions/) and [configuration](https://opencode.ai/docs/config/). These are CLI permission controls, not an OS sandbox.

## DeepSeek through Pi

```yaml
analyst:
  backend: pi
  provider: deepseek
  model: deepseek-flash
  api_key_env: DEEPSEEK_API_KEY
```

Use exact IDs from `pi --list-models`. The installed **Pi 1.5.0** help confirms `pi -p --provider <provider> --model <id> --mode json`. Calls disable tools, extensions, MCP, skills, prompt templates, context files and session persistence, and run from a scratch directory. Pi's assistant `message_end` events supply text, tokens and reported costs. This backend is tested with mocked subprocess events; live DeepSeek through Pi is unverified.

## Other CLIs and custom commands

Codex uses ephemeral `exec`, a strict output-schema file, a read-only sandbox, ignored user config and disabled unnecessary tool features. Claude Code uses `-p`, JSON/schema output, an empty tool list, strict empty MCP config and no session persistence; API auth variables are removed so this backend uses the CLI subscription. The separate `api` backend uses a forced structured-answer tool with the [Claude Messages API](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools); no tool is executed locally.

A custom command runs without a shell in a temporary directory. Use absolute paths to scripts/files; it must consume stdin and write only JSON on stdout. Its executable is trusted local code and is responsible for disabling its own tools. It receives the current environment plus project `.env` settings, so select a program you trust.

Every backend receives instructions to treat job/source text as untrusted data and avoid tools. CLI data is JSON-quoted separately from those instructions. The adapter validates the final answer against the requested Pydantic model with strict types. Malformed JSON, missing fields, refusals and invalid answers fail; they do not trigger another provider.

## Schema compatibility and limits

The `openai` backend first requests strict `json_schema`, then `json_object`, then a schema in the prompt alone. It downgrades only for HTTP 400/422 errors naming an unsupported structured-output format; authentication, quota, server errors and unrelated bad requests are surfaced. Accepted format is cached per endpoint/model for the process. Every answer still passes local validation.

```yaml
analyst:
  backend: openai
  base_url: https://api.deepseek.com/v1
  model: deepseek-flash
  api_key_env: DEEPSEEK_API_KEY
  fallback_backend: opencode
  fallback_settings:
    provider: opencode
    model: deepseek-v4.1-flash-free
    api_key_env: null
```

HTTP 429, quota/balance errors and CLI limit failures (including OpenCode retryAfterMs) raise `LimitReached`. If configured, the fallback handles that request and subsequent requests until reset. `fallback_settings` overrides only the fallback; even another endpoint/model of the same backend is supported. Authentication failures and invalid answers never activate fallback. The cooldown identity includes backend, endpoint, provider/model and key variable name, never the credential value.

Known reset times (`Retry-After` or “try again at/in …”) persist in `data/.llm-limits.json` across launches. Unknown reset times last only until the current process ends. After the reset the primary is tried again. Only one fallback attempt is made. Delete the cooldown file to retry a primary manually in a new process.

Each backend attempt writes a `llm` event to `data/llm-usage.jsonl` with purpose, backend/model, result status, duration, available token/cache counts and cost. Switching writes `llm_fallback`. Prompts, answers and keys are omitted. Missing usage is labelled unavailable; missing costs remain null. Provider-reported cost is retained; optional current `input_cost_per_million` / `output_cost_per_million` rates estimate uncached token costs when no provider cost is supplied. There is no hardcoded price list, and partial known costs in `stats` are labelled with call coverage.

## Verification

Offline tests mock HTTP and subprocesses for all seven backends, strict-schema downgrade, response validation, CLI errors/isolation and failover across launches. Default pytest blocks network; all live checks are explicit. Direct DeepSeek was live-verified with synthetic data; DeepSeek through Pi remains unverified. The permitted OpenCode free DeepSeek smoke reached the provider but returned HTTP 429 (`provider.free-limit`, free usage exhausted); no successful live model response was obtained. An initial configuration bootstrap failure reached no model and was fixed by inheriting the inline config into the private server. See [STATUS.md](STATUS.md) for validation totals.

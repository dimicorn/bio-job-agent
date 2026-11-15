# London biotech job discovery

A local, single-user discovery MVP for Principal / Senior Principal Scientist and related senior R&D jobs around London. M1 from [PLAN.md](PLAN.md) is implemented: Greenhouse, Lever and Workable discovery, public careers-page watchers, reviewed industry employers, configurable geography and titles, SQLite history and provenance, terminal review, manual application links, and CSV export.

Public ATS JSON → normalize and filter → deduplicate → SQLite → terminal CLI / CSV. Candidate preferences and source health remain local. The provider-agnostic LLM layer supports structured checks now; scientific matching, CV parsing, document drafts, application submission and scheduling remain deferred. See [verified source status and limitations](docs/STATUS.md).

## Install

From a clone of this repository on macOS or Linux, run one command:

```sh
./setup.sh
```

The script offers to install [uv](https://docs.astral.sh/uv/getting-started/installation/) if missing, installs the locked dependencies (including a suitable Python when needed), initialises SQLite, and opens the setup wizard. The wizard selects a primary and optional fallback LLM, stores API keys with hidden input in gitignored `.env`, sends one tiny structured check to each selected backend, copies the candidate example when needed and imports the profile. It is safe to rerun: existing profiles, keys, database history and configuration are retained unless you choose to change the LLM settings. A failed model check exits with a failure status while retaining your settings for correction.

Review `config/candidate.yaml`, edit the title/geography preferences and set `reviewed: true` only when accurate. The example asserts no qualifications or scientific specialization. An unreviewed profile can be used to explore discovery and is labelled in CLI output.

```sh
uv run bio-jobs doctor
uv run bio-jobs sync
uv run bio-jobs list
uv run bio-jobs review
```

`bio-jobs` or `bio-jobs help` shows commands without network access. On the first operational run without `config.yaml`, the wizard opens automatically in a terminal; noninteractive runs print a setup instruction. Discovery does not call an LLM. Only setup checks, `llm-check`, or `doctor --online` send a tiny model request. Install and authenticate the CLI you select (OpenCode, Pi, Codex or Claude Code); the script does not install those applications.

## Terminal workflow

| Command | Behavior |
| --- | --- |
| `setup` | Choose primary/fallback LLM, import preferences, check each model |
| `doctor [--online]` | Local setup and source health; opt in to live LLM checks |
| `firms [--validate]` | List the reviewed employer watchlist; explicitly reverify public boards with `--validate` |
| `sync` | Explicit public ATS refresh with persistent cooldowns |
| `list` | Numbered table with title, employer, location, posted/discovered dates and apply link; newest discovered first |
| `review` | One job at a time: **o** open apply link, **s** save, **r** reject for good, **l** later, **q** quit (no Enter needed) |
| `export <file.csv>` | Export matching stored jobs, review decisions and provenance |
| `stats` | Job/review counts, source health, LLM calls, tokens and known costs |
| `llm-check` | One tiny schema-validated request with configured failover |
| `init` / `import-profile [path]` | Initialise/migrate SQLite or version a candidate profile |

`list`, `review` and `export` accept `--title`, `--employer`, `--location`, `--source`, `--since YYYY-MM-DD`, `--status` and `--limit`. Rejected jobs are hidden by default; inspect them explicitly with `--status rejected` or `--status all`. Saved jobs leave the default review queue; deferred jobs return next time. Decisions persist across refreshes. List/review default to 50 rows; export includes all matching jobs unless limited. Ordering is by discovery date, without a scientific fit score (M2).

```sh
uv run bio-jobs list --employer Mytos --location London
uv run bio-jobs review --status saved
uv run bio-jobs export jobs.csv --since 2026-11-01
uv run bio-jobs llm-check
```

Opening an apply link is the only browser action. Applying remains manual.

## LLM configuration

Run `uv run bio-jobs setup` to change models. The shared `analyst:` section is in gitignored `config.yaml`; [config.example.yaml](config.example.yaml) documents every setting. DeepSeek works through the OpenAI-compatible API (`https://api.deepseek.com/v1`, `DEEPSEEK_API_KEY`), OpenCode or Pi. The other backends are Codex CLI, Claude Code CLI, Claude API and a custom stdin/stdout command. See [docs/llm.md](docs/llm.md) for presets, isolation, schema fallback, cooldowns and verification limits.

## Preferences and storage

- `config/candidate.yaml`: target title tokens, specialties (stored, not scored), reviewed flag, and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 14-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative settings paths resolve from the repository root. YAML preferences and sources are validated before sync. LLM settings use `BIO_CONFIG_PATH` (default `config.yaml`); logs/cooldowns use `BIO_LLM_STATE_DIR` (default `data`).
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.
- Exact source IDs and canonical URLs merge first. Otherwise the same normalized employer/title/location must have description similarity of at least 0.92. Country suffixes and tracking parameters are normalized; functional URL query parameters are preserved. Distinct descriptions are retained. Sources are direct employer ATS feeds or public careers-page watchers. Generic site snapshots are retained for manual review and are not advertised as scientist jobs. Ashby is available as an adapter but excluded from the default watchlist where its robots endpoint blocks access.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.
- A failing source does not stop others or delete old listings. Missing listings are **not** automatically declared closed. Check the last-seen date and employer page. Status rechecks/expiry handling belong to M5.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. M1 tables cover candidate profiles, employers, sources, raw postings, jobs, job sources and sync runs. The scoring, document, approval and application entities described in PLAN.md are deferred with M2–M5.

## Export and tests

CLI export uses current YAML preferences and includes source provenance:

```sh
uv run bio-jobs export jobs.csv
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
```

Default tests use recorded/sanitized fixtures and block network sockets. They cover real API shapes, pagination, deduplication, industry/geography exclusions, partial/malformed feeds, failed sources, robots restrictions, cooldown/429 handling, transactional migrations, profile versions, CSV formula safety, terminal list/review/export/setup/doctor flows and all seven LLM backends with mocked HTTP/subprocesses.

Live checks are separate and explicit; they use a temporary database and never submit anything:

```sh
uv run pytest --live -m live -s
```

CI is configured for offline tests; no CI run or remote operation was performed as part of this local-only delivery.

## Source operation and privacy

The adapters perform GET requests against documented public ATS APIs and reviewed public careers pages. They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A disallow rule, unavailable robots response, authentication restriction, redirect, or access denial stops that source. There is no LinkedIn retrieval, browser automation or CAPTCHA handling. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](docs/STATUS.md).

Descriptions are untrusted data: HTML is converted to plain text, never executed as instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files) to erase stored profile versions and discovery history. Keep your editable candidate YAML only as long as needed.

# M1 implementation and verification status

Updated on **2026-11-15 UTC** on macOS arm64. Discovery now uses a terminal CLI with rich; provider setup, structured LLM checks and persistent manual review decisions are implemented. Scientific matching/scoring, CV extraction, document drafts and M4–M5 remain deferred. Historical public-source checks below used Python 3.12.13; current offline validation uses Python 3.14.7.

## Working: verified public feeds

| Integration | Actual GET endpoint verified | Live result | Default matching result |
| --- | --- | --- | --- |
| Lever / Mytos | `https://api.lever.co/v0/postings/mytos?mode=json&skip=0&limit=100` | HTTP 200, 13 postings; 12 location fields mention London | 1: Principal Scientist (PD/MSAT), London, external ID `dba7160d-a0f2-4891-9294-1fbc60689dbd` |
| Greenhouse / Isomorphic Labs | `https://boards-api.greenhouse.io/v1/boards/isomorphiclabs/jobs?content=true` | HTTP 200, 40 postings; 29 location fields mention London | 10 London roles matching Research Leader / Senior Research Investigator preferences |

The raw endpoints were checked at approximately **14:06 UTC**. An additional opt-in integration test exercised the actual application adapters and full persistence pipeline, both returning `ok` with no malformed records. Counts are a snapshot, not a promise of current availability. Mytos's board has a thirteenth record with another location; no location is inferred from its headquarters. The recorded fixtures deliberately contain only two postings per adapter.

Official documentation reviewed before implementation:

- [Lever Postings API](https://github.com/lever/postings-api): published postings are public and may be collected by third parties; documents GET list, JSON mode, skip/limit pagination and hosted/apply URLs. Only the public global instance is implemented. No authentication is required for the GET feeds used here; the authenticated recruiting API and submission API are unused.
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html): public GET jobs and `content=true`, no GET authentication, source IDs and publication-vs-update distinction. No Harvest API or application POST is used.
- [Mytos careers](https://www.mytos.bio/careers) and [official Lever board](https://jobs.lever.co/mytos) verify the employer/site association and commercial cell-manufacturing/CDMO activity. [Mytos terms](https://www.mytos.bio/terms/) permit internal non-commercial reference; use is limited to a single user's local search. The adapter does not scrape the employer website. Fixtures replace employer prose with synthetic text rather than redistribute it.
- [Isomorphic Labs openings](https://www.isomorphiclabs.com/job-openings), [Greenhouse board](https://job-boards.greenhouse.io/isomorphiclabs) and [recruitment notice](https://www.isomorphiclabs.com/privacy-notice-for-talent-sourcing-and-recruitment) verify the commercial drug-discovery employer and Greenhouse relationship. [Employer terms](https://www.isomorphiclabs.com/terms-and-conditions) cover their own website and third-party links; discovery uses the documented public Greenhouse API. This is local reference, with no public job-description redistribution.

Robots checks (both HTTP 200, also recorded in fixtures):

- [Lever API robots](https://api.lever.co/robots.txt): `Allow: /`, `Crawl-delay: 1` for the generic user agent.
- [Greenhouse API robots](https://boards-api.greenhouse.io/robots.txt): only `/embed/` disallowed; `/v1/boards/.../jobs` allowed.

No public GET quota was specified in the reviewed API documents. The application uses conservative per-host pacing (minimum 1 second, raised for robots crawl-delay/request-rate), a 20-second HTTP timeout and default **900-second** source refresh interval (configurable, with a 60-second lower bound). Lever is paginated at 100 records/page with a 100-page safety cap; an incomplete capped snapshot is refused. There are no automatic retries. HTTP 429 extends the persisted cooldown by at least `Retry-After`; 401/403, redirects and robots failures stop the source. Robots decisions are rechecked on each explicit sync. The configured sources have no authentication or application data requirements.

Required fields: source posting ID, title, and public HTTPS source/apply URL; employer identity comes from the reviewed source configuration. Location/description are optional facts (unknown location is labelled for review); missing required fields make only that posting malformed, preserving source processing with `partial` health. Each employer needs a unique source ID, correct board token, reviewed industry classification and evidence URL in YAML.

Fallback for either source: retain cached listings and source error/last-seen information; use the [Mytos board](https://jobs.lever.co/mytos) or [Isomorphic board](https://job-boards.greenhouse.io/isomorphiclabs) manually, and refresh after restrictions clear. Do not bypass blocked access. No manual URL import or alert ingestion is implemented in M1.

## Partial / restricted / deferred

- **Recursion / Greenhouse:** a direct live GET of `https://boards-api.greenhouse.io/v1/boards/recursionpharmaceuticals/jobs?content=true` returned HTTP 200 and 18 postings, with **zero London location fields** in that response. This differs from search-index snapshots of the [board](https://job-boards.greenhouse.io/recursionpharmaceuticals), so the direct response is the evidence used here. It is included disabled in the watchlist, rather than counted as a London-producing integration. The same verified Greenhouse adapter can read it when enabled. It has no dedicated recorded fixture.
- **Restricted integrations:** none of the two enabled public APIs was restricted in the live checks. The app stops rather than evades any future restriction.
- **Deferred sources:** GSK, Lonza, Autolus and AstraZeneca are watchlist intentions in PLAN.md, not working adapters in this release. Workday, Ashby, SmartRecruiters, industry boards, email alerts and URL import were not implemented or represented as verified.
- **Deferred capabilities:** scientific matching/scoring, CV extraction, document generation, application workflows/approval/submission, Playwright, scheduling, notifications and vacancy expiry/rechecks. SQLite implements M1 entities only. FastAPI is not needed for this local terminal MVP.

## Validation actually performed

- Locked native dependencies install with `uv sync --frozen`; the project now depends on rich and has no web UI dependency.
- `uv run pytest`: **97 offline tests pass** (1 live test deselected). The offline suite blocks sockets and mocks LLM HTTP/subprocess calls. It covers all seven backends, schema format negotiation, strict Pydantic validation, rate/quota errors, fallback settings and cooldown persistence across launches. Terminal tests cover help, list/filter/export, source health/stats, first-run setup, hidden-key storage, re-runnable setup, and persisted save/reject/later decisions surviving source refreshes.
- A native `./setup.sh` install and re-run passed with an actual local JSON stub through the command backend (no external LLM request); `doctor`, fixture-backed `list`/`export`, `stats` and `llm-check` exited 0.
- The shell installer is tested from another working directory with a fake uv executable; it runs locked dependency sync, database init and setup in order on repeated runs. It only installs missing uv after explicit terminal consent. No scheduler is installed.
- `uv run ruff check app tests` and `uv run ruff format --check app tests` are required and run locally before delivery.
- The previous discovery live check (`uv run pytest --live -m live -s`) passed both configured ATS APIs and persisted 11 matching jobs (1 Mytos + 10 Isomorphic); it was not repeated for the LLM change. Discovery integration fixtures still pass offline.

## LLM live verification limits

- **OpenCode v2.3.0:** help/model/output flags verified against the installed CLI. The free `opencode/deepseek-v4.1-flash-free` smoke reached the provider and returned HTTP **429**, `provider.free-limit`: “Free usage limit reached.” No schema-validated live model answer was obtained. Its `retryAfterMs` metadata is handled and regression-tested offline. The initial custom-agent bootstrap failed before any model request; inline inherited config fixed it. No further live model requests were made.
- **Pi 1.5.0:** flags verified against installed `--help`; response/usage parsing, disabled tools and DeepSeek provider/model selection tested only with mocked subprocesses.
- **Direct DeepSeek / openai:** no `DEEPSEEK_API_KEY` is configured here. Live use remains **unverified**; no key was requested or fabricated.
- **codex, claude_code, api, command:** backend unit tests are offline and mocked. No live model request was sent through these backends.

Proof and scratch output live outside the repository under the authorised task directory or temporary directories. No populated `.env`, private profile, database, keys or proof logs are committed.

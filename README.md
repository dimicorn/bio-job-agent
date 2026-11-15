# London biotech job discovery

A local, single-user job discovery and matching tool for Principal / Senior Principal Scientist and related senior R&D jobs around London. Greenhouse, Lever and Workable discovery, public careers-page watchers, reviewed candidate profiles, explainable LLM matching, SQLite history, terminal review, manual application links and CSV export are implemented.

Public ATS JSON → normalize and filter → deduplicate → SQLite → batched scoring → terminal CLI / CSV. Document generation, application submission and scheduling remain deferred. See [verified source status and limitations](docs/STATUS.md).

## Install

From a clone of this repository on macOS or Linux, run one command:

```sh
./setup.sh
```

The script offers to install [uv](https://docs.astral.sh/uv/getting-started/installation/) if missing, installs the locked dependencies (including a suitable Python when needed), initialises SQLite, and opens the setup wizard. The wizard selects a primary and optional fallback LLM, stores API keys with hidden input in gitignored `.env`, sends one tiny structured check to each selected backend, copies the candidate example when needed and imports the profile. It is safe to rerun: existing profiles, keys, database history and configuration are retained unless you choose to change the LLM settings. A failed model check exits with a failure status while retaining your settings for correction.

Fill the gitignored `resume.md` (see [resume.example.md](resume.example.md)) and put job wishes in `analyst.preferences` in `config.yaml`. Extract a profile, review every fact and supporting excerpt in `config/candidate.yaml`, correct any errors, and only then set `reviewed: true`. Keep the generated `resume_hash`; editing the resume requires extraction and review again. The example asserts no candidate qualifications. Unreviewed profiles can explore discovery with `list --all-scores`, but cannot score jobs.

```sh
uv run bio-jobs doctor
uv run bio-jobs extract-profile --force
# Review config/candidate.yaml and set reviewed: true before continuing.
uv run bio-jobs scan
uv run bio-jobs list
uv run bio-jobs review
```

`bio-jobs` or `bio-jobs help` shows commands without network access. On the first operational run without `config.yaml`, the wizard opens automatically in a terminal; noninteractive runs print a setup instruction. Discovery does not call an LLM. Profile extraction and scoring send candidate facts and job text to the configured LLM; setup, `llm-check`, and `doctor --online` send small connection checks. Install and authenticate the CLI you select (OpenCode, Pi, Codex or Claude Code); the script does not install those applications.

## Terminal workflow

| Command | Behavior |
| --- | --- |
| `setup` | Choose primary/fallback LLM, import preferences, check each model |
| `doctor [--online]` | Local setup and source health; opt in to live LLM checks |
| `firms [--validate]` | List the reviewed employer watchlist; explicitly reverify public boards with `--validate` |
| `sync` | Explicit public ATS refresh with persistent cooldowns |
| `extract-profile [--output path] [--force]` | Extract resume facts into an unreviewed YAML profile; retains title/geography preferences; refuses replacement without `--force` |
| `scan` | Sync, then score eligible jobs with missing or stale evaluations |
| `analyze` | Score stored eligible jobs without fetching sources |
| `list` | Highest fit score first; score, evidence, gaps, uncertainty, dates and apply link |
| `review` | **o** open, **s** save, **r** reject, **l** later, **v** override score/recommendation with a reason, **q** quit; shows dimension and must-have evidence |
| `export <file.csv>` | Export matching stored jobs, review decisions and provenance |
| `stats` | Job/review counts, source health, LLM calls, tokens and known costs |
| `llm-check` | One tiny schema-validated request with configured failover |
| `init` / `import-profile [path]` | Initialise/migrate SQLite or version a candidate profile |

`list`, `review` and `export` accept `--title`, `--employer`, `--location`, `--source`, `--since YYYY-MM-DD`, `--status`, `--limit` and `--all-scores`. They honour `analyst.min_score` (default 50); unscored jobs are hidden by that gate. `--all-scores` includes low/unscored jobs for inspection and overrides; setting `min_score: 0` disables the gate. Rejected jobs are hidden unless selected with `--status rejected` or `--status all`. Saved jobs leave the default review queue; deferred jobs return next time. Decisions and overrides persist across refreshes and rescoring. List/review default to 50 rows; export includes all matching jobs unless limited. Equal scores sort by discovery date and ID.

Scores use configurable weights: specialization 30, technical 20, seniority/leadership 15, industry/therapeutic area 10, geography 10, research outcomes 10, salary/arrangements 5. Weights must sum to 100. The LLM supplies dimension ratings with evidence; the application computes the total (nearest integer, halves up). An unknown dimension contributes neutral 50 and always carries an uncertainty flag. Missing qualifications mean an **unknown** must-have, never an unmet requirement; unmet requires explicit candidate evidence. Recommendations follow score bands: apply ≥70, maybe ≥50, skip <50. Confidence remains separate from score; even a high score can need factual checks. Model judgments still require human review.

`analyst.batch_size` and `analyst.parallel` control scoring batches. Each evaluation stores the scoring version, content hash and reviewed profile version. Unchanged inputs reuse the saved evaluation; changes to job content, profile, preferences, weights or provider settings trigger scoring again. Old evaluations remain as history and are hidden when stale. User overrides store a separate score, recommendation and reason without rewriting model evidence. Failed batches remain unscored and `analyze` exits with an error; completed batches survive for the next run. `stats` groups tokens and costs by backend, model and purpose (`score`, `profile`, checks).

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

- `config/candidate.yaml`: reviewed resume facts and evidence, resume hash, specialties, target title tokens and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Generic scientist titles without a known level retain unknown seniority for scoring. Explicit level/role mismatches remain excluded. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 14-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_RESUME_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative environment paths resolve from the repository root; the optional `resume:` setting in config.yaml resolves relative to that config file. YAML preferences and sources are validated before sync. LLM settings use `BIO_CONFIG_PATH` (default `config.yaml`); logs/cooldowns use `BIO_LLM_STATE_DIR` (default `data`).
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.
- Exact source IDs and canonical URLs merge first. Otherwise the same normalized employer/title/location must have description similarity of at least 0.92. Country suffixes and tracking parameters are normalized; functional URL query parameters are preserved. Distinct descriptions are retained. Sources are direct employer ATS feeds or public careers-page watchers. Generic site snapshots are retained for manual review and are not advertised as scientist jobs. Ashby is available as an adapter but excluded from the default watchlist where its robots endpoint blocks access.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.
- A failing source does not stop others or delete old listings. Missing listings are **not** automatically declared closed. Check the last-seen date and employer page. Status rechecks/expiry handling belong to M5.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. Tables cover candidate profiles, cached resume extraction, evaluations, overrides, employers, sources, raw postings, jobs, reviews and sync runs. Documents and application entities remain deferred.

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
# One synthetic scoring request only (Codex by default; no personal resume):
BIO_LIVE_SCORING=1 uv run pytest tests/integration/test_matching.py --live -m live -s
```

CI is configured for offline tests; no CI run or remote operation was performed as part of this local-only delivery.

## Source operation and privacy

The adapters perform GET requests against documented public ATS APIs and reviewed public careers pages. They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A disallow rule, unavailable robots response, authentication restriction, redirect, or access denial stops that source. There is no LinkedIn retrieval, browser automation or CAPTCHA handling. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](docs/STATUS.md).

Descriptions are untrusted data: HTML is converted to plain text, and scoring prompts JSON-delimit employer content and explicitly forbid following its instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services; extraction/scoring send candidate data to your configured LLM backend. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, `resume.md`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files) to erase stored profile versions, extraction/scoring history and discovery history. Keep your editable candidate YAML only as long as needed.


### Application preparation (M3)

Preparation uses your reviewed candidate profile and its matching resume hash. Run
`extract-profile`, review the facts and set `reviewed: true` before generating materials.
Job preferences never count as qualifications. Prepare a scored job like this:

```bash
uv run bio-jobs letters                 # batches jobs at analyst.min_score
uv run bio-jobs letters --job 42 --force # keep a new version of an existing letter
uv run bio-jobs resume 42               # tailored, editable Markdown
uv run bio-jobs pdf 42                  # Chromium PDF plus pdfminer ATS text check
uv run bio-jobs answers --job 42 --show # prepare screening draft, including an empty draft
uv run bio-jobs review --status all
uv run bio-jobs diff 42                 # latest two versions, all material types
```

Resume tailoring orders the reviewed facts by relevance to the job, preserving their
wording, contact lines from the reviewed resume, and fact/evidence references. It does
not ask a model to invent or expand experience. Edit the resulting Markdown path and
run `pdf 42` to capture a new version and rebuild it. The ATS check verifies the name,
email, section order and text ligatures. Missing contact details require your edit.
`./setup.sh` installs Chromium; `doctor` reports when it is missing. Install it manually
with `uv run playwright install chromium` if needed.

Letters are written in the vacancy's language through the configured LLM. Each generated
candidate claim cites reviewed fact IDs. A deterministic check looks for unsupported
numbers, technologies, employers, degrees, names and invalid/missing fact references.
Job text can support statements about the employer, but job requirements do not establish
candidate experience. Each candidate claim is checked against its cited facts as well.
A failing draft receives one guided rewrite. If it still fails, or the rewrite fails,
the draft is saved with visible review warnings. These lexical checks are conservative;
read the letter and its cited facts before approving. They do not prove the meaning of
a paraphrase. Fix warnings by editing or rewriting the letter.

Every letter, resume and screening draft has a database snapshot and an editable file
under `materials/<job>/`, linked to the job content and reviewed profile version. PDF
builds also record an integrity hash and ATS warnings. Changed profile/job inputs make
old materials stale. New edits or generations return the application to `awaiting_review`.
`diff 42 --kind letter` shows a readable changelog between letter versions. The database
retains earlier content even if a material file is edited directly.

Register screening questions manually in M3 (browser discovery arrives in M4):

```bash
uv run bio-jobs answers --job 42 --add 'Do you require visa sponsorship?' --option Yes --option No --show
uv run bio-jobs answers                  # interactively fill unanswered questions
```

`answers.yaml` is a private, editable answer bank; start with `answers.example.yaml`.
Question matching ignores case, required markers and trailing punctuation. Answers are
reused across employers when their options match. An `Employer: Question` key overrides
a shared answer. If a shared answer does not fit another employer's options, the interactive
command saves the replacement for that employer. Enter option numbers separated by commas
for multiple selections; Enter leaves the question unanswered. Legal, visa, salary,
demographic and all other unknown answers stay `needs_human` until you supply them.
Screening draft snapshots retain the actual questions, selected answers and their source.

Review keeps `o` open, `s` save, `r` reject, `l` later, `v` score override and `q` quit.
Additional keys are:

| Key | Action |
| --- | --- |
| `e` | Edit the letter with `$VISUAL`, `$EDITOR`, or `nano`; editor arguments such as `code --wait` work. Saves a checked new version. |
| `w` | Rewrite the letter with your instruction, preserving factual checks. |
| `d` | Show the material diff. |
| `a` | Approve the current materials, show letter/Markdown/PDF/answer paths, and open the apply link for manual completion. |
| `t` | Record `submitted` only after you type `submitted` to confirm you completed the application yourself. |
| `m` | Set a preparation state, including `blocked`, `manual_required` or `withdrawn`. |

Approval requires current, unchanged letter/resume/answer files, a checked resume PDF,
no material warnings and no unanswered required screening questions. It only marks the
job approved and opens its link; it never fills forms or submits. Saving a review decision
also initializes the application state to `saved`. States are `saved`, `preparing`,
`awaiting_review`, `approved`, `blocked`, `manual_required`, `withdrawn` and user-confirmed
`submitted`. Use `list --application-state awaiting_review`, `stats`, or
`state 42 manual_required` to inspect or update preparation. `approved` and `submitted`
require their dedicated review actions.

The setup wizard persists these settings in your local `config.yaml`:

```yaml
apply:
  mode: manual       # manual | assisted | auto
  daily_limit: 5
  max_per_run: 5
  min_score: 80
  active_hours: [8, 23]
  answers_file: answers.yaml
```

In M3 all three modes use the manual handoff; `assisted` and `auto` explicitly say browser
filling arrives in M4. `doctor` and `stats` show the stored mode and gates. Approval enforces
the higher of analyst/apply score thresholds, the local active-hours window, a per-review
approval limit and the local-day count of user-confirmed submissions. These gates control
the CLI handoff, not what you choose to do independently in a browser. No form filling,
automated submission, scheduling or notifications are included.

Offline material tests mock the LLM. A real PDF test runs when Chromium is available and
skips cleanly otherwise. To spend an opt-in request on a synthetic letter (no private data):

```bash
BIO_LIVE_LETTER=1 uv run pytest tests/integration/test_materials.py -k live_synthetic_letter -m live --live -q -s
```

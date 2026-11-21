# London biotech job discovery

A local, single-user tool that finds Principal / Senior Principal Scientist and related senior R&D jobs around London, scores them against your reviewed CV with an explainable LLM match, and helps you prepare and submit applications.

Public ATS JSON (Greenhouse, Lever, Workable, Ashby) and public careers-page watchers → normalize and filter → deduplicate → SQLite → batched scoring → terminal review / new-job alerts → approved materials → manual, assisted or auto application. See [verified source status and limitations](docs/STATUS.md).

Released under the [MIT License](LICENSE).

## Install and quick start

Python 3.12 or newer and SQLite 3.24.0 or newer with JSON1 support are required.

From a clone of this repository on macOS or Linux, run one command:

```sh
./setup.sh
```

The script offers to install [uv](https://docs.astral.sh/uv/getting-started/installation/) if missing, installs the locked dependencies (including a suitable Python when needed), initialises SQLite, and opens the setup wizard. The wizard selects a primary and optional fallback LLM, stores API keys with hidden input in gitignored `.env`, sends one tiny structured check to each selected backend, copies the candidate example when needed and imports the profile. When `resume.md` is filled but not yet extracted (or changed since extraction), it offers to run profile extraction, which sends the resume to the selected LLM. It is safe to rerun: it shows the current LLM and apply settings first, and existing profiles, keys, database history and configuration are retained unless you choose to change them. A failed model check exits with a failure status while retaining your settings for correction.

Fill the gitignored `resume.md` (see [resume.example.md](resume.example.md)) and put job wishes in `analyst.preferences` in `config.yaml`. Extract a profile, review every fact and supporting excerpt in `config/candidate.yaml`, correct any errors, and only then set `reviewed: true`. Keep the generated `resume_hash`; editing the resume requires extraction and review again. The example asserts no candidate qualifications. Unreviewed profiles can explore discovery with `list --all-scores`, but cannot score jobs. `doctor` gives a reason for each check, including the `resume.md` status (missing, empty, example, unextracted or changed), and ends with the single most important next step.

```sh
uv run bio-jobs doctor
uv run bio-jobs extract-profile --force
# Review config/candidate.yaml and set reviewed: true before continuing.
uv run bio-jobs scan
uv run bio-jobs list
uv run bio-jobs review   # a prepares any missing letter, CV and answers, then approves
```

`bio-jobs` or `bio-jobs help` shows commands without network access. On the first operational run without `config.yaml`, the wizard opens automatically in a terminal; noninteractive runs print a setup instruction. Discovery does not call an LLM. Profile extraction and scoring send candidate facts and job text to the configured LLM; setup, `llm-check`, and `doctor --online` send small connection checks. Install and authenticate the CLI you select (OpenCode, Pi, Codex or Claude Code); the script does not install those applications.

## Daily workflow

### Discover and score

`scan` refreshes every enabled source and scores new or changed eligible jobs; `sync` only refreshes and `analyze` only scores stored jobs. Discovery itself does not call an LLM or use a browser.

`list`, `review` and `export` accept `--title`, `--employer`, `--location`, `--source`, `--since YYYY-MM-DD`, `--status`, `--application-state`, `--limit` and `--all-scores`. They honour `analyst.min_score` (default 50); unscored jobs are hidden by that gate. `--all-scores` includes low/unscored jobs for inspection and overrides; setting `min_score: 0` disables the gate. Rejected jobs are hidden unless selected with `--status rejected` or `--status all`. Saved jobs and applications in progress remain in the default review queue, even when unscored; deferred jobs return next time. Decisions and overrides persist across refreshes and rescoring. List/review default to 50 rows; export includes all matching jobs unless limited. Equal scores sort by discovery date and ID.

Scores use configurable weights: specialization 30, technical 20, seniority/leadership 15, industry/therapeutic area 10, geography 10, research outcomes 10, salary/arrangements 5. Weights must sum to 100. The LLM supplies dimension ratings with evidence; the application computes the total (nearest integer, halves up). An unknown dimension contributes neutral 50 and always carries an uncertainty flag. Missing qualifications mean an **unknown** must-have, never an unmet requirement; unmet requires explicit candidate evidence. Recommendations follow score bands: apply ≥70, maybe ≥50, skip <50. Confidence remains separate from score; even a high score can need factual checks. Model judgments still require human review.

`analyst.batch_size` and `analyst.parallel` control scoring batches. Each evaluation stores the scoring version, content hash and reviewed profile version. Unchanged inputs reuse the saved evaluation; changes to job content, profile, preferences, weights or provider settings trigger scoring again. Old evaluations remain as history and are hidden when stale. User overrides store a separate score, recommendation and reason without rewriting model evidence. Failed batches remain unscored and `analyze` exits with an error; completed batches survive for the next run. `stats` groups tokens and costs by backend, model and purpose (`score`, `profile`, checks).

```sh
uv run bio-jobs list --employer Mytos --location London
uv run bio-jobs review --status saved
uv run bio-jobs export jobs.csv --since 2026-11-01
uv run bio-jobs llm-check
```

Scoring uses only eligibility facts you enter in the candidate profile and mark
`eligibility.reviewed: true`; null means unknown. Edit those fields to correct them.
Review shows unmet and unknown requirements with candidate and job evidence as
checks before applying. Missing eligibility evidence never automatically excludes a job.
Scoring failures identify the job, backend, model, safe validation paths or failure
category, and a next step in command output and `stats`. Failed batches are split once;
invalid single-job answers receive one repair attempt and may use the configured
fallback. Cut-off answers may also use that fallback without a quota cooldown.

### Fresh postings

`search.max_posting_age_days` defaults to 7; set it to 0 or null to disable the age filter. Freshness limits scoring and alerts for new prospects. Saved jobs and applications remain visible in list and review with a stale or closed label; closed jobs cannot be applied automatically. The list and review show the posting date, or a clearly labelled first-seen proxy only when the source had previously synced successfully. Unknown dates on a first sync remain visible as unknown. Greenhouse uses `first_published`, never `updated_at`; Lever uses `createdAt`.

### Review, materials and approval

Saving a job in `review` starts its application. Preparation needs a reviewed profile whose resume hash matches `resume.md`. Prepare a scored job in one step:

```sh
uv run bio-jobs prepare 42               # everything missing or stale, then the open questions
uv run bio-jobs review --status saved    # or press a on the job: it prepares first, then approves
```

`prepare` (and `a` in review) writes the cover letter, tailored resume, resume PDF with its ATS check and the screening draft, but only for pieces that are missing or stale; current materials stay as they are. It then shows the letter with each claim and its cited facts, plus any warnings, and asks only the screening questions `answers.yaml` cannot answer. Without an interactive terminal it prepares what it can, lists the unanswered questions and prints the command to run in a terminal.

The single commands are optional, for redoing one piece:

```sh
uv run bio-jobs letters                  # checked cover letters for jobs at analyst.min_score
uv run bio-jobs resume 42                # tailored, editable Markdown CV
uv run bio-jobs pdf 42                   # Chromium PDF plus ATS text check
uv run bio-jobs answers --job 42 --show  # screening-question draft from answers.yaml
uv run bio-jobs diff 42                  # latest two versions of each material
```

Every generated claim cites reviewed facts and is checked for unsupported content; read each letter and its warnings before approving. Approval (`a` in review) prepares anything missing or stale, pins the current letter, CV PDF and answers and requires typing `approve` to accept any warnings or newly prepared materials. Edited files must be re-imported with `bio-jobs import`. Details: [docs/applications.md](docs/applications.md).

### Apply

Applications default to `apply.mode: manual`, which opens the apply link for you to complete. Assisted and auto modes use Playwright-driven Chromium on supported Greenhouse, Lever, Workable and Ashby forms, only for jobs you explicitly approved:

- **manual**: opens the link; record a submission you completed with `t` in review.
- **assisted**: fills and verifies the form, then waits for you to press Submit.
- **auto**: presses Submit once and waits for an acknowledgement; runs only within `active_hours` and score, daily and per-run limits.

```sh
uv run bio-jobs apply 42      # one previously approved job
uv run bio-jobs apply         # approved queue, bounded by max_per_run
uv run bio-jobs autopilot     # auto mode: sync/score, then consume approved queue
uv run bio-jobs run --no-scan # alias; use stored approved queue only
```

Sensitive questions (visa, work authorization, legal declarations, demographics, assessments) are only filled from your stored answers, never by the LLM. Sign-in, MFA, visible CAPTCHA and unknown required questions stop the job for you; nothing is bypassed. A missing acknowledgement becomes `unknown_result` and is never retried automatically. A per-employer cooldown (`apply.company_cooldown_days`, default 30) prevents repeat applications. See [docs/applications.md](docs/applications.md) for the answers bank, per-ATS form filling, blocked-field explanations and submission safeguards.

### Command reference

| Command | Behavior |
| --- | --- |
| `setup` | Show current settings, choose primary/fallback LLM, import preferences, check each model, offer profile extraction |
| `doctor [--online]` | Local setup, resume and source health with a reason per check and one next step; opt in to live LLM checks |
| `firms [--validate]` | List the reviewed employer watchlist; explicitly reverify public boards with `--validate` |
| `sync` | Explicit public ATS refresh with persistent cooldowns |
| `extract-profile [--output path] [--force]` | Extract resume facts and an evidenced header name into an unreviewed YAML profile; retains edited names and title/geography preferences; refuses replacement without `--force` |
| `scan` | Sync, then score eligible jobs with missing or stale evaluations |
| `watch [--scheduled]` | One locked sync, score, digest and new/changed-job alert pass; scheduled runs also consume the approved queue only in auto mode |
| `analyze` | Score stored eligible jobs without fetching sources |
| `list` | Highest fit score first; score, evidence, gaps, uncertainty, dates and apply link |
| `prepare <job>` | Create any missing or stale letter, resume, resume PDF (with ATS check) and screening draft; show the letter claims; ask only the screening questions `answers.yaml` cannot answer |
| `apply [job]` | Fill one approved job or the approved queue in the configured apply mode |
| `autopilot` / `run [--no-scan]` | One bounded auto pass; refresh and score first unless `--no-scan` |
| `review` | **o** open, **s** save, **r** reject, **l** later, **v** override score/recommendation with a reason, **a** prepare and approve, **q** quit; shows dimension and must-have evidence |
| `letters`, `resume`, `pdf`, `letter-pdf`, `answers`, `diff`, `import`, `state` | Optionally redo one material, compare, import and track application materials ([docs/applications.md](docs/applications.md)) |
| `unmerge JOB ORIGIN` | Separate a non-canonical origin from a grouped job |
| `feed-review <feed.json>` | Experimental offline lead review ([docs/feed-review.md](docs/feed-review.md)) |
| `export <file.csv>` | Export matching stored jobs, review decisions and provenance |
| `stats` | Job/review counts, source health, LLM calls, tokens and known costs |
| `llm-check` | One tiny schema-validated request with configured failover |
| `init` / `import-profile [path]` | Initialise/migrate SQLite or version a candidate profile |

`stats --runs` lists local run summaries. Each `sync`, `scan`, `watch`, `autopilot`, and `apply` command writes one
under the gitignored `run-summaries/` directory with run IDs,
source outcomes, job changes, scoring failures, application transitions, and notification
results. Summaries contain IDs and error categories, never CVs, answers, credentials, or provider
response text. A summary left in `running` state indicates the process did not finish cleanly.

Long-running scan, analyze, letters, prepare, watch, autopilot and apply commands prevent idle sleep on
macOS while they run. Ctrl+C or SIGTERM requests a stop; scoring and letter requests already
running are allowed to finish and their results are saved, with saved and unfinished job IDs
reported. A second Ctrl+C exits immediately. Interrupted submissions retain their existing
manual-reconciliation safeguards. Exit codes are 130 for Ctrl+C and 143 for SIGTERM.

## Configuration

### LLM

Run `uv run bio-jobs setup` to change models. The shared `analyst:` section is in gitignored `config.yaml`; [config.example.yaml](config.example.yaml) documents every setting. DeepSeek works through the OpenAI-compatible API (`https://api.deepseek.com/v1`, `DEEPSEEK_API_KEY`), OpenCode or Pi. The direct API setup preset disables thinking to preserve the output token budget. The other backends are Codex CLI, Claude Code CLI, Claude API and a custom stdin/stdout command. See [docs/llm.md](docs/llm.md) for presets, isolation, schema fallback, cooldowns and verification limits.

### Preferences, sources and storage

- `config/candidate.yaml`: reviewed resume facts and evidence, resume hash, specialties, target title tokens and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Generic scientist titles without a known level retain unknown seniority for scoring. Explicit level/role mismatches remain excluded. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 17-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_RESUME_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative environment paths resolve from the repository root; the optional `resume:` setting in config.yaml resolves relative to that config file. YAML preferences and sources are validated before sync. LLM settings use `BIO_CONFIG_PATH` (default `config.yaml`); logs/cooldowns use `BIO_LLM_STATE_DIR` (default `data`).
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.
- Source-scoped IDs and canonical URLs identify origins. New probable duplicates group when the employer, similar title, overlapping location and similar descriptions match; conflicting explicit requisition IDs keep openings separate. Groups show their origin IDs, locations and links in list and review, and appear once in scoring and alerts. Grouping is conservative and may need review: run `bio-jobs unmerge JOB_ID ORIGIN_ID` to separate a non-canonical origin. Existing application jobs are kept separate. Previously stored probable duplicates are grouped for list, review, scoring and alerts without moving their histories; grouped job IDs are shown in list and review. Each job with an application stays visible as its own position. Historical origins need a source refresh before they can be separated. The canonical origin owns job facts; after it expires, the earliest eligible present origin with a saved posting takes ownership. Sources are direct employer ATS feeds or public careers-page watchers.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.
- A failing source does not stop others or close old listings. Complete successful ATS snapshots close an origin after two absences; reappearance reopens it. Partial snapshots and page watchers never close listings. Check the last-seen date and employer page when availability is uncertain.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. Tables cover candidate profiles, cached resume extraction, evaluations, overrides, employers, sources, raw postings, jobs, reviews, sync runs, application materials/approvals/attempts and content-versioned alerts with durable digests.

## Scheduled runs and alerts

Run `uv run bio-jobs watch` for one pass over all enabled sources. It uses the existing filters and versioned scoring cache, requires a reviewed profile, and alerts only for open, non-rejected jobs at or above `watch.min_score` (default: inherit `analyst.min_score`). Submitted applications do not alert. Existing eligible jobs can alert on the first watch run. Job title, description, requirements, location, compensation, arrangements, deadline and apply-link changes create a new alert version; provenance, posting timestamps, whitespace-only changes and changes to the candidate/scoring configuration alone do not. Returning to a previously alerted content version does not alert again.

Configure the optional `watch` block in `config.yaml`:

```yaml
watch:
  min_score: 70       # omit or use null to inherit analyst.min_score
  notify: true       # macOS desktop notifications; ignored on Linux
  telegram: false    # optional bot digest, off by default
  digest_dir: digests # relative to config.yaml, gitignored
```

Up to three matches produce individual macOS notifications; larger batches produce one summary. Every batch also produces a private Markdown digest in `digests/`. To enable Telegram, set `BIO_TELEGRAM_BOT_TOKEN` and `BIO_TELEGRAM_CHAT_ID` in gitignored `.env` and set `watch.telegram: true`. The bot must have access to the target chat. Only the standard library is used to send plain-text bot messages. Email and n8n hooks are deferred.

SQLite records each job/content version and its digest atomically before delivery. External delivery is attempted at most once: an OS/network error or a crash during delivery can lose that notification, but the local digest remains available and is restored on the next run if its file was not written. External delivery is not retried automatically, avoiding duplicate messages after uncertain outcomes. `stats` and `doctor` show per-source last complete success, retained last error and its time, consecutive failed/partial runs, and last complete snapshot count. Source cooldowns are not failures.

A vacancy closes after its origin is absent from **two complete successful snapshots**; another present origin keeps it open. Errors, rate limits, malformed/partial responses and cooldowns close nothing. Page watchers never expire job lists. Reappearing jobs reopen and reset their absence count. Application records, including submitted records, remain intact. Closed roles leave the scoring, review and auto-apply queues; the SQLite history remains available.

Rerun `./setup.sh` to optionally install or update a macOS LaunchAgent (default **no**). Choose one or two distinct local hours; it uses the repository template `launchd/ru.bio-job-agent.watch.plist`, sets PATH for `~/.local/bin` and `/opt/homebrew/bin`, writes `logs/watch.log`, and does not run at load time. The script prints the exact `launchctl unload` command. Declining leaves an existing schedule unchanged. Linux prints a ready-to-copy cron entry and never modifies crontab; create the indicated logs directory first. The project directory must remain at the installed path.

Schedules execute `uv run bio-jobs watch --scheduled`. Only `apply.mode: auto` adds the existing bounded autopilot queue pass; it still requires prior explicit approvals, current materials, active hours, score/limit gates and submission confirmation. Manual and assisted modes only sync, score and alert. Watch does not generate approvals or materials. A database-specific kernel file lock prevents overlapping watch passes, including their auto-apply phase. Process exit/crash releases it automatically; a retained lock file is harmless. SQLite state and the installed schedule survive restarts; the next scheduled or manual run proceeds normally.

## Source operation, privacy and safety

The adapters perform GET requests against documented public ATS APIs and reviewed public careers pages. They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A robots 4xx response other than 429 imposes no restrictions; robots 429, 5xx or network failure stops that source. A disallow rule, endpoint authentication restriction or redirect also stops it. Discovery performs no LinkedIn retrieval and no browser automation. Browser automation is used only by assisted and auto application on forms you approved; it never solves or bypasses CAPTCHA, sign-in or MFA challenges. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](docs/STATUS.md).

Descriptions are untrusted data: HTML is converted to plain text, and scoring prompts JSON-delimit employer content and explicitly forbid following its instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services; extraction/scoring send candidate data to your configured LLM backend. Material snapshots, PDFs, application screenshots, the answers bank, LLM usage logs and the SQLite database are created with private permissions (files 0600, directories 0700). Startup and doctor also tighten existing sensitive artifacts. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, `resume.md`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files) to erase stored profile versions, extraction/scoring history and discovery history. Keep your editable candidate YAML only as long as needed.

An experimental, offline [feed lead review](docs/feed-review.md) can examine a manually downloaded Biotechnology Jobs feed for employer leads; it adds nothing to the database.

## Development and tests

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

The GitHub Actions [tests workflow](.github/workflows/tests.yml) runs ruff and the offline test suite (with Chromium installed) on every push and pull request.

Commit conventions, the protected `main` branch and the release steps (CHANGELOG via git-cliff, versions from git tags) are in [CONTRIBUTING.md](CONTRIBUTING.md); release history is in [CHANGELOG.md](CHANGELOG.md).

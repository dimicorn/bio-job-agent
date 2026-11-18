# London biotech job discovery

A local, single-user job discovery and matching tool for Principal / Senior Principal Scientist and related senior R&D jobs around London. Greenhouse, Lever and Workable discovery, public careers-page watchers, reviewed candidate profiles, explainable LLM matching, SQLite history, terminal review, application materials, approved assisted/auto application, scheduled watch passes and CSV export are implemented.

Public ATS JSON → normalize and filter → deduplicate → SQLite → batched scoring → terminal review / new-job alerts. Optional native scheduling runs a watch pass and, only in auto mode, the approved application queue. See [verified source status and limitations](docs/STATUS.md).

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

## Terminal workflow

| Command | Behavior |
| --- | --- |
| `setup` | Choose primary/fallback LLM, import preferences, check each model |
| `doctor [--online]` | Local setup and source health; opt in to live LLM checks |
| `firms [--validate]` | List the reviewed employer watchlist; explicitly reverify public boards with `--validate` |
| `sync` | Explicit public ATS refresh with persistent cooldowns |
| `extract-profile [--output path] [--force]` | Extract resume facts into an unreviewed YAML profile; retains title/geography preferences; refuses replacement without `--force` |
| `scan` | Sync, then score eligible jobs with missing or stale evaluations |
| `watch [--scheduled]` | One locked sync, score, digest and new/changed-job alert pass; scheduled runs also consume the approved queue only in auto mode |
| `analyze` | Score stored eligible jobs without fetching sources |
| `list` | Highest fit score first; score, evidence, gaps, uncertainty, dates and apply link |
| `apply [job]` | Fill one approved job or the approved queue in the configured apply mode |
| `autopilot` / `run [--no-scan]` | One bounded auto pass; refresh and score first unless `--no-scan` |
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

Applications default to manual. Assisted and auto modes use Playwright on supported ATS forms after explicit material approval.

## LLM configuration

Run `uv run bio-jobs setup` to change models. The shared `analyst:` section is in gitignored `config.yaml`; [config.example.yaml](config.example.yaml) documents every setting. DeepSeek works through the OpenAI-compatible API (`https://api.deepseek.com/v1`, `DEEPSEEK_API_KEY`), OpenCode or Pi. The other backends are Codex CLI, Claude Code CLI, Claude API and a custom stdin/stdout command. See [docs/llm.md](docs/llm.md) for presets, isolation, schema fallback, cooldowns and verification limits.

## Preferences and storage

- `config/candidate.yaml`: reviewed resume facts and evidence, resume hash, specialties, target title tokens and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Generic scientist titles without a known level retain unknown seniority for scoring. Explicit level/role mismatches remain excluded. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 17-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_RESUME_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative environment paths resolve from the repository root; the optional `resume:` setting in config.yaml resolves relative to that config file. YAML preferences and sources are validated before sync. LLM settings use `BIO_CONFIG_PATH` (default `config.yaml`); logs/cooldowns use `BIO_LLM_STATE_DIR` (default `data`).
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.
- Exact source IDs and canonical URLs merge first. Otherwise the same normalized employer/title/location must have description similarity of at least 0.92. Country suffixes and tracking parameters are normalized; functional URL query parameters are preserved. Distinct descriptions are retained. Sources are direct employer ATS feeds or public careers-page watchers. Generic site snapshots are retained for manual review and are not advertised as scientist jobs. The watchlist includes three verified Ashby boards.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.
- A failing source does not stop others or close old listings. Complete successful ATS snapshots close an origin after two absences; reappearance reopens it. Partial snapshots and page watchers never close listings. Check the last-seen date and employer page when availability is uncertain.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. Tables cover candidate profiles, cached resume extraction, evaluations, overrides, employers, sources, raw postings, jobs, reviews, sync runs, application materials/approvals/attempts and content-versioned alerts with durable digests.

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

The adapters perform GET requests against documented public ATS APIs and reviewed public careers pages. They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A robots 4xx response other than 429 imposes no restrictions; robots 429, 5xx or network failure stops that source. A disallow rule, endpoint authentication restriction or redirect also stops it. There is no LinkedIn retrieval, browser automation or CAPTCHA handling. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](docs/STATUS.md).

Descriptions are untrusted data: HTML is converted to plain text, and scoring prompts JSON-delimit employer content and explicitly forbid following its instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services; extraction/scoring send candidate data to your configured LLM backend. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, `resume.md`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files) to erase stored profile versions, extraction/scoring history and discovery history. Keep your editable candidate YAML only as long as needed.


### Application preparation and browser modes

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

Browser filling queues unknown questions for `answers`; you can also register them manually:

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
| `a` | Approve and pin the current letter, CV PDF and answer versions. Manual mode opens the apply link; assisted/auto prints the `apply` command. |
| `t` | Record `submitted` only after you type `submitted` to confirm you completed the application yourself. |
| `m` | Set a preparation state, including `blocked`, `manual_required` or `withdrawn`. |

Approval requires current, unchanged letter/resume/answer files, a checked resume PDF,
no material warnings and no unanswered required screening questions. It only marks the
job approved; browser execution is a separate `apply` command. Saving a review decision
also initializes the application state to `saved`. States are `saved`, `preparing`,
`awaiting_review`, `approved`, `blocked`, `manual_required`, `withdrawn`, `submitting`,
`unknown_result` and `submitted`. Use `list --application-state awaiting_review`, `stats`, or
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
  profile_dir: data/browser-profile
  audit_dir: debug/apply
  submit_wait_minutes: 10
  confirmation_timeout_seconds: 45
  action_seconds: 0.3
```

```bash
uv run bio-jobs apply 42           # one previously approved job
uv run bio-jobs apply              # approved queue, bounded by max_per_run
uv run bio-jobs autopilot          # auto mode: sync/score, then consume approved queue
uv run bio-jobs run --no-scan       # alias; use stored approved queue only
```

`manual` opens the link for completion yourself. `assisted` fills and verifies the form,
then waits up to `submit_wait_minutes` for you to press Submit and for an acknowledgement.
`auto` presses Submit once, then waits up to `confirmation_timeout_seconds` for an
acknowledgement. Both browser modes support Greenhouse (including embedded forms), Lever,
Workable and Ashby when its board is enabled. Standard HTML controls, common React-select
controls, Workable choice groups and Ashby choice buttons are supported. Changed or
unsupported widgets stop for manual handling. No live employer submissions are part of testing.

Identity/contact fields come from the reviewed profile and approved CV; missing contact
values and screening answers require explicit entries in `answers.yaml`. The approved
answer bank is pinned too. Unknown required questions, mismatches, legal/visa/demographic
questions (even with saved answers), assessments, sign-in, MFA and CAPTCHA stop the job as
`manual_required`, with the reason recorded. The browser stays open for a person; no
challenge is solved or bypassed. Unknown optional questions are queued and left blank; an unapproved prefilled value stops the job.
After answering a newly discovered question, rebuild `answers` and approve again.

Browser modes recheck approval, material integrity, the higher of analyst/apply score
thresholds and local active hours before submission. Daily slots are reserved atomically
across processes; attempts count conservatively against the local-day limit, including
manual stops and uncertain results. `max_per_run` bounds attempts. A missing acknowledgement
becomes `unknown_result`, never an automatic retry. A crash during an attempt, a human
handoff or a challenge after clicking also leaves a durable retry barrier, even if someone
later changes the application state. Reconcile these applications on the employer site;
review → `t` can record a submission you personally verified. Automatic retries are never
unlocked by a state change. A URL containing “confirmation” and a click alone are insufficient.

Ordinary Chromium uses the gitignored persistent profile; no stealth plugins or fingerprint
changes are used. Filled-form and confirmation screenshots are saved under the gitignored
`debug/apply` directory. Application events and attempt records in SQLite retain actions,
version IDs, the approved answer-bank snapshot, verification hashes, reasons and confirmation evidence. Screenshots contain personal data; keep
custom profile/audit locations private and ignored too. On a stop or timeout, the CLI
disconnects and leaves Chromium open. Close the tab after manual handling. The next attempt
opens a fresh tab, preserving previous handoffs. Scheduling and notifications are handled by the separate `watch` command described above.

Offline material tests mock the LLM. A real PDF test runs when Chromium is available and
skips cleanly otherwise. To spend an opt-in request on a synthetic letter (no private data):

```bash
BIO_LIVE_LETTER=1 uv run pytest tests/integration/test_materials.py -k live_synthetic_letter -m live --live -q -s
```

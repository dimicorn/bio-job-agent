# Command reference

| Command | Behavior |
| --- | --- |
| `setup` | Show current settings, choose primary/fallback LLM, import preferences, check each model, offer profile extraction |
| `doctor [--online]` | Local setup, resume and source health with a reason per check and one next step; opt in to live LLM checks |
| `firms [--validate]` | List the reviewed employer watchlist; explicitly reverify public boards with `--validate` |
| `help` | Show usage and commands |
| `sync` | Explicit public ATS refresh with persistent cooldowns |
| `add <url> [--employer NAME]` | Add one job by link (Greenhouse, Lever, Ashby, Workable or any job page), score it and print the review command |
| `extract-profile [--output path] [--force]` | Extract resume facts and an evidenced header name into an unreviewed YAML profile; retains edited names and title/geography preferences; refuses replacement without `--force` |
| `scan` | Sync, then score eligible jobs with missing or stale evaluations |
| `watch [--scheduled]` | One locked sync, score, digest and new/changed-job alert pass; scheduled runs also consume the approved queue only in auto mode |
| `analyze` | Score stored eligible jobs without fetching sources |
| `list` | Highest fit score first; score, evidence, gaps, uncertainty, resume keyword match, dates and apply link |
| `prepare <job>` | Create any missing or stale letter and screening draft, check the resume PDF; show the letter claims; read the application form's questions and ask those `answers.yaml` cannot answer |
| `resume` | Check the resume PDF that applications upload unchanged |
| `resume-from-pdf [--pdf path] [--force]` | Write `resume.md` from the resume PDF's text; never replaces a filled `resume.md` without `--force` |
| `apply [job]` | Fill one approved job or the approved queue in the configured apply mode |
| `autopilot` / `run [--no-scan]` | One bounded auto pass; refresh and score first unless `--no-scan` |
| `review` | **o** open, **s** save, **r** reject, **l** later, **v** override score/recommendation with a reason, **a** prepare and approve, **q** quit; shows dimension and must-have evidence |
| `letters`, `letter-pdf`, `answers`, `diff`, `import`, `state` | Optionally redo one material, compare, import and track application materials ([docs/applications.md](applications.md)) |
| `unmerge JOB ORIGIN` | Separate a non-canonical origin from a grouped job |
| `feed-review <feed.json>` | Experimental offline lead review ([docs/feed-review.md](feed-review.md)) |
| `export <file.csv>` | Export matching stored jobs, review decisions, resume keyword match and provenance |
| `stats` | Job/review counts, source health, LLM calls, tokens and known costs |
| `llm-check` | One tiny schema-validated request with configured failover |
| `init` / `import-profile [path]` | Initialise/migrate SQLite or version a candidate profile |

## Run summaries and interruptions

`stats --runs` lists local run summaries. Each `sync`, `scan`, `watch`, `autopilot`, and `apply` command writes one
under the gitignored `run-summaries/` directory with run IDs,
source outcomes, job changes, scoring failures, application transitions, and notification
results. Summaries contain IDs and error categories, never CVs, answers, credentials, or provider
response text. A summary left in `running` state indicates the process did not finish cleanly.
These commands end with one compact line counting the same things, for example
`09:14:02 scan done: 6 sources (1 error, 5 ok) · jobs 3 new · scored 3`.

Run progress is printed as timestamped lines under a header for each phase (Sync, Score,
Letters, Prepare, Apply, Alerts); lines from parallel workers carry the worker name in bold.
Outcomes are coloured: green `✓` done or filled, yellow `!` warning, yellow `⊘` blocked or left
for you, red `✗` failed, dim detail. Job and employer text is shown literally, never as
terminal styling. Output is plain text when redirected (for example to `logs/watch.log`) or when
`NO_COLOR` is set; the timestamps and symbols stay.

Long-running scan, analyze, letters, prepare, watch, autopilot and apply commands prevent idle sleep on
macOS while they run. Ctrl+C or SIGTERM requests a stop; scoring and letter requests already
running are allowed to finish and their results are saved, with saved and unfinished job IDs
reported. A second Ctrl+C exits immediately. Interrupted submissions retain their existing
manual-reconciliation safeguards. Exit codes are 130 for Ctrl+C and 143 for SIGTERM.

See the [daily workflow](../README.md#daily-workflow) and [scheduled runs](watch.md).

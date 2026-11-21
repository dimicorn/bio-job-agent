# London biotech job discovery

A local, single-user tool for finding Principal Scientist, Senior Principal Scientist
and related senior R&D jobs around London. It collects listings from public employer
job boards and careers pages, scores them against your reviewed CV with evidence,
and helps you prepare and submit applications.

## Install and quick start

Save your CV as `resume.pdf` in the repository root. Then, from your clone on
macOS or Linux, run:

```sh
./setup.sh
```

Setup offers to install uv, installs the locked dependencies and Chromium, initializes
SQLite, and opens the wizard to configure and check your LLM. The app requires
Python 3.12+ and SQLite 3.24+ with JSON1. If you choose an LLM CLI, install and sign
in to it separately. See [installation and candidate setup](docs/setup.md).

1. Check the text that setup extracts into `resume.md`, or fill it from
   [the example](resume.example.md).
2. Set your job preferences under `analyst.preferences` in `config.yaml`.
3. If setup did not extract your current profile, run
   `uv run bio-jobs extract-profile --force`. Review the facts and evidence in
   `config/candidate.yaml`, correct any errors, then set `reviewed: true`.
   Run `uv run bio-jobs doctor` to check what still needs attention.

Profile extraction, scoring, and letter drafting send candidate data to your
configured LLM. Discovery uses public sources without an LLM. Applications use
your resume PDF as-is. See [local storage and privacy](docs/operation.md#privacy).

## Daily workflow

```sh
uv run bio-jobs scan     # refresh listings and score eligible jobs
uv run bio-jobs list     # one line per match: score, status, keyword match
uv run bio-jobs show 12  # one job's evidence, gaps, must-haves and keywords
uv run bio-jobs review   # review jobs and prepare applications
```

In an interactive terminal, each card ends with `a apply · r reject · l later · o open · ? more · q quit`;
`?` lists the other keys valid for that job. `a` prepares and approves the application.
Check the cover letter, screening answers, and warnings before confirming. Manual
mode is the default. It opens the application link for you to complete, and `t`
records a submission you completed. Assisted and auto modes fill the form in your
installed Chrome on a private profile and require explicit approval for each job;
`uv run bio-jobs apply <job> --mode dry_run` fills it and saves a screenshot without
submitting. See [applications](docs/applications.md).

To add a posting you found yourself, run `uv run bio-jobs add <url>`; it is stored,
scored, and kept in review. `list`, `show` and `review` also show a
[resume keyword match](docs/keywords.md) computed without an LLM. See
[search and scoring](docs/search.md#add-one-job-by-link).

For discovery without a reviewed profile, run `uv run bio-jobs sync` and
`uv run bio-jobs list --all-scores`. Use `uv run bio-jobs help` for all commands.

## Documentation

- [Installation and candidate setup](docs/setup.md) covers the wizard, resume extraction, and profile review.
- [Search and scoring](docs/search.md) covers filters, adding jobs by link, freshness, ranking, and deduplication.
- [Resume keyword match](docs/keywords.md) covers the LLM-free keyword overlap shown next to scores.
- [LLM backends](docs/llm.md) covers provider settings, model checks, and fallback.
- [Applications](docs/applications.md) covers materials, screening questions, submission modes, and dry runs.
- [Scheduled runs and alerts](docs/watch.md) covers digests, notifications, and launchd or cron.
- [Command reference](docs/commands.md) covers commands, console output, run summaries, and stopping a run.
- [Source operation and privacy](docs/operation.md) covers retrieval, local data, and credentials.
- [Verified sources and limitations](docs/STATUS.md) records source evidence and validation status.

## Development

See [development and tests](docs/development.md) for lint, tests, and explicit live
checks. Commit conventions, the protected `main` branch, and release steps are in
[CONTRIBUTING.md](CONTRIBUTING.md); release history is in [CHANGELOG.md](CHANGELOG.md).

[MIT License](LICENSE)

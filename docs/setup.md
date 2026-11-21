# Installation and candidate setup

Python 3.12 or newer and SQLite 3.24.0 or newer with JSON1 support are required.

From a clone of this repository on macOS or Linux, run one command:

```sh
./setup.sh
```

The script offers to install [uv](https://docs.astral.sh/uv/getting-started/installation/) if missing, installs the locked dependencies (including a suitable Python when needed) and Chromium for Playwright, initialises SQLite, and opens the setup wizard. The wizard selects a primary and optional fallback LLM, stores API keys with hidden input in gitignored `.env`, sends one tiny structured check to each selected backend, copies the candidate example when needed and imports the profile. If your resume PDF is at `resume.pdf` (or `resume_pdf:` in config.yaml), it fills `resume.md` from the PDF's text, asking before replacing a filled one, and tells you to check it and run `extract-profile`. When `resume.md` is filled but not yet extracted (or changed since extraction), it offers to run profile extraction, which sends the resume to the selected LLM. It is safe to rerun: it shows the current LLM and apply settings first, and existing profiles, keys, database history and configuration are retained unless you choose to change them. A failed model check exits with a failure status while retaining your settings for correction.

Save your resume PDF as the gitignored `resume.pdf`: applications upload this file exactly as it is. Fill the gitignored `resume.md` from it (setup or `bio-jobs resume-from-pdf` extracts the text; check the result against the PDF), or by hand from [resume.example.md](../resume.example.md), and put job wishes in `analyst.preferences` in `config.yaml`. Extract a profile, review every fact and supporting excerpt in `config/candidate.yaml`, correct any errors, and only then set `reviewed: true`. Keep the generated `resume_hash`; editing the resume requires extraction and review again. The example asserts no candidate qualifications. Unreviewed profiles can explore discovery with `list --all-scores`, but cannot score jobs. `doctor` gives a reason for each check, including the `resume.md` status (missing, empty, example, unextracted or changed), and ends with the single most important next step.

If you add `resume.pdf` after setup, or setup did not extract it, continue from the PDF:

```sh
uv run bio-jobs resume-from-pdf          # fill resume.md, then check it against the PDF
uv run bio-jobs extract-profile --force
# Review config/candidate.yaml (facts, evidence, eligibility) and set reviewed: true.
uv run bio-jobs doctor
uv run bio-jobs scan
```

Until `reviewed: true` is set, `scan` stops with "Scoring requires factual review". Listing and review show jobs scoring at least `analyst.min_score` (default 50); preparing or submitting an application also requires `apply.min_score` (default 80), so lower-scoring jobs are not applied to unless you override them in `review --all-scores`.

`bio-jobs` or `bio-jobs help` shows commands without network access. On the first operational run without `config.yaml`, the wizard opens automatically in a terminal; noninteractive runs print a setup instruction. Discovery does not call an LLM. Profile extraction and scoring send candidate facts and job text to the configured LLM; setup, `llm-check`, and `doctor --online` send small connection checks. Install and authenticate the CLI you select (OpenCode, Pi, Codex or Claude Code); the script does not install those applications.

Continue with the [daily workflow](../README.md#daily-workflow). See [LLM configuration](llm.md) for supported backends and [search preferences](search.md#preferences) for filtering.

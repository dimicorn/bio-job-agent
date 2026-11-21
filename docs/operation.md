# Source operation, storage and privacy

## Source operation

The adapters perform GET requests against documented public ATS APIs, reviewed public careers pages and the official Biotechnology Jobs JSON Feed (hourly at most, as its publisher asks). They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A robots 4xx response other than 429 imposes no restrictions; robots 429, 5xx or network failure stops that source. A disallow rule, endpoint authentication restriction or redirect also stops it. Discovery performs no LinkedIn retrieval and no browser automation. Browser automation is used only by assisted and auto application on forms you approved; it never solves or bypasses CAPTCHA, sign-in or MFA challenges. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](STATUS.md).

An experimental, offline [feed lead review](feed-review.md) can examine a manually downloaded Biotechnology Jobs feed for employer leads; it adds nothing to the database.

## Local settings and storage

- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_RESUME_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative environment paths resolve from the repository root; the optional `resume:` and `resume_pdf:` settings in config.yaml resolve relative to that config file. YAML preferences and sources are validated before sync. LLM settings use `BIO_CONFIG_PATH` (default `config.yaml`); logs/cooldowns use `BIO_LLM_STATE_DIR` (default `data`).
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. Tables cover candidate profiles, cached resume extraction, evaluations, overrides, employers, sources, raw postings, jobs, reviews, sync runs, application materials/approvals/attempts and content-versioned alerts with durable digests.

## Privacy

Descriptions are untrusted data: HTML is converted to plain text, and scoring prompts JSON-delimit employer content and explicitly forbid following its instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services; extraction/scoring send candidate data to your configured LLM backend. Material snapshots, PDFs, application screenshots, the answers bank, LLM usage logs and the SQLite database are created with private permissions (files 0600, directories 0700). Startup and doctor also tighten existing sensitive artifacts. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, `resume.md`, `resume.pdf`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files) to erase stored profile versions, extraction/scoring history and discovery history. Keep your editable candidate YAML only as long as needed.

# London biotech job discovery

A local, single-user discovery MVP for Principal / Senior Principal Scientist and related senior R&D jobs around London. M1 from [PLAN.md](PLAN.md) is implemented: Greenhouse and Lever discovery, reviewed industry employers, configurable geography and titles, SQLite history and provenance, Streamlit review, manual application links, and CSV export.

Public ATS JSON → normalize and filter → deduplicate → SQLite → Streamlit / CSV. Candidate preferences and source health remain local. There is no LLM, CV parsing, application submission, or scheduling. See [verified source status and limitations](docs/STATUS.md).

## Native setup (Python 3.12+)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands from the repository root:

```sh
uv sync --frozen
cp .env.example .env
cp config/candidate.example.yaml config/candidate.yaml
```

Edit `config/candidate.yaml`, review the title/geography preferences, and set `reviewed: true` when accurate. Set `BIO_CANDIDATE_PATH=config/candidate.yaml` in `.env`. The example contains preferences only; it asserts no qualifications or scientific specialization. An unreviewed example can be used to explore discovery, and is visibly labelled in the UI.

```sh
uv run bio-jobs init
uv run bio-jobs import-profile
uv run bio-jobs sync
uv run streamlit run app/dashboard/main.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

Open http://127.0.0.1:8501. A new dashboard also creates/migrates the database and imports the configured profile automatically. Startup does **not** contact employers. Use **Refresh public sources** or the explicit `sync` command to fetch. Employer links open the actual application page; applying is manual.

Without uv: create a Python 3.12+ virtual environment, activate it, and run `python -m pip install -e . pytest ruff`. The same `bio-jobs` and `streamlit` commands then work without `uv run`; uv is recommended for the committed dependency lock.

## Docker Compose

```sh
docker compose up --build -d
docker compose ps
```

Open http://127.0.0.1:8501 and refresh sources. The image applies database migrations on the first dashboard session. SQLite is persisted in the `job_data` named volume; `config/` is mounted read-only so preferences can be edited on the host. It runs as a non-root user, exposes only loopback on the host, and has a Streamlit HTTP health check. Native `.env` settings for the candidate/source paths are forwarded; `.env` and the database are never baked into the image. To sync from the container explicitly:

```sh
docker compose exec dashboard /app/.venv/bin/bio-jobs sync
```

**Validation limitation:** the Docker client exists on the development machine, but its daemon socket is unavailable. `docker compose config --quiet` passed; an image build and clean container start could not be executed. Native installation, rendered dashboard tests, and HTTP startup were verified. Docker startup is therefore **unverified**, not claimed as a passed acceptance test.

## Preferences and storage

- `config/candidate.yaml`: target title tokens, specialties (stored, not scored), reviewed flag, and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml`: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- `.env`: `BIO_DATABASE_PATH` (default `data/jobs.sqlite3`), `BIO_CANDIDATE_PATH`, `BIO_SOURCES_PATH`, `BIO_HTTP_TIMEOUT`, `BIO_USER_AGENT`. Relative settings paths resolve from the repository root. YAML configuration is validated before sync.
- Profiles are versioned by content hash. Jobs preserve raw content snapshots, exact source IDs, original source/apply URLs, first/last seen times and source-level sync results. Missing dates, salaries, sponsorship and other facts stay null/unknown. Greenhouse `updated_at` is **not** a publication date.
- Exact source IDs and canonical URLs merge first. Otherwise the same normalized employer/title/location must have description similarity of at least 0.92. Country suffixes and tracking parameters are normalized; functional URL query parameters are preserved. Distinct descriptions are retained. All configured sources are direct employer ATS feeds, so there is no aggregator-versus-employer preference to resolve in M1.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.
- A failing source does not stop others or delete old listings. Missing listings are **not** automatically declared closed. Check the last-seen date and employer page. Status rechecks/expiry handling belong to M5.

The SQLite schema and transactional, version-tracked SQL migrations are in `migrations/`. M1 tables cover candidate profiles, employers, sources, raw postings, jobs, job sources and sync runs. The scoring, document, approval and application entities described in PLAN.md are deferred with M2–M5.

## Export and tests

The dashboard's download exports the currently filtered view with provenance. CLI export uses current YAML preferences:

```sh
uv run bio-jobs export jobs.csv
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest
```

Default tests use recorded/sanitized fixtures and block network sockets. They cover real API shapes, pagination, deduplication, industry/geography exclusions, partial/malformed feeds, failed sources, robots restrictions, cooldown/429 handling, transactional migrations, profile versions, CSV formula safety and the empty/populated Streamlit dashboard.

Live checks are separate and explicit; they use a temporary database and never submit anything:

```sh
uv run pytest --live -m live -s
```

CI is configured for offline tests and Compose validation; no CI run or remote operation was performed as part of this local-only delivery.

## Source operation and privacy

The adapters perform GET requests only against documented public ATS APIs. They check the current API host's robots file before listing retrieval, obey crawl delay/request rate, pace requests at least one second apart per host, and enforce a persistent 15-minute source refresh interval by default. They do not retry automatically; 429 `Retry-After` extends the cooldown. A disallow rule, unavailable robots response, authentication restriction, redirect, or access denial stops that source. There is no LinkedIn retrieval, browser automation or CAPTCHA handling. Re-review provider/employer terms before expanding the watchlist. See [source evidence and fallbacks](docs/STATUS.md).

Descriptions are untrusted data: HTML is converted to plain text, never executed as instructions; CSV cells are protected against spreadsheet formulas. Discovery sends no candidate profile to employers or external AI services. The database is local and **not encrypted by the application**; use OS disk protection and private filesystem permissions. `.env`, personal candidate YAML, databases and virtual environments are gitignored. Retain local exports privately. Stop the app before deleting the SQLite database (and associated WAL/SHM files), or the Docker named volume, to erase stored profile versions and discovery history. Keep your editable candidate YAML only as long as needed.

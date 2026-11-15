# PLAN.md — London Biotech Industry Job Agent

## Objective
Build a **local-first, single-user** agent that discovers, deduplicates, scores, and assists with applications for **industry** Principal Scientist / Senior Principal Scientist (and closely related senior R&D) positions in and around London, UK. **Exclude LinkedIn and academia.** Prioritize reliable matching and reviewed applications over mass auto-apply.

## Scope and constraints
- **Employers:** biotech, biopharma, pharma, CRO/CDMO, commercial life-sciences startups.
- **Roles:** Principal Scientist, Senior Principal Scientist, Lead/Staff/Principal Research Scientist, Scientific Lead; optionally Associate Director / Head of Biology where the candidate fits.
- **Location:** London and Greater London; optional Slough, Stevenage, Uxbridge, Cambridge, Oxford, each independently configurable.
- **Exclude:** academic/university positions, postdocs, PhDs, faculty, sales-only roles; LinkedIn discovery or automation.
- **Scientific specialty:** configurable from the candidate's CV and preferences; do not presume wet-lab or computational specialization.
- **Submission:** default human-in-the-loop. Never submit without explicit approval per application. No CAPTCHA bypass, bot evasion, or automation against site restrictions.
- **Integrity:** never fabricate qualifications, salary, visa sponsorship, posting dates, or successful submission.

## Proposed stack
- Python 3.12+, FastAPI, Pydantic, SQLAlchemy + SQLite (Postgres optional).
- HTTPX + selectolax/BeautifulSoup for allowed retrieval; Playwright for authorized browser-assisted applications.
- Streamlit dashboard for the MVP; local scheduler (APScheduler or equivalent); n8n optional, not required.
- Provider-agnostic LLM (Claude / OpenAI-compatible / optional local) for matching and document drafts.
- Docker Compose, `.env.example`, structured logging, pytest.

## Architecture
```text
Job sources / ATS feeds / email alerts
               |
      source adapters + provenance
               |
   normalize -> deduplicate -> verify
               |
       SQLite vacancy store
               |
 candidate profile -> hard filters -> semantic + LLM scoring
               |
           Streamlit review
               |
        draft tailored documents
               |
     approved browser/manual apply
               |
    confirmation + application tracker
```

## Sources: implementation priority
1. **Company career pages and public ATS listings:** Greenhouse, Lever, Ashby, SmartRecruiters; Workday only where a compliant, robust method exists. Discover public openings without assuming public submission APIs.
2. **Industry boards:** PharmiWeb, BioSpace, Proclinical, SRG, BioTalent, EPM Scientific, New Scientist Jobs; build an adapter only after confirming current access and terms. Use official alerts/manual URL import if scraping is disallowed.
3. **Initial employer watchlist:** GSK, Lonza, Autolus, AstraZeneca; expand using specialization and geography, not company size alone.
4. **Optional:** job-alert email ingestion via explicitly authorized IMAP/provider integration.

For each source document: integration method, terms/robots considerations, rate limits, authentication, required fields, last health check, and fallback. A failed adapter must not break the pipeline.

## Data model
Minimum entities: `CandidateProfile` (versioned), `Employer`, `Source`, `RawPosting`, `Job`, `JobSource`, `MatchEvaluation`, `Application`, `Document`, `Approval`, `ApplicationEvent`, `SyncRun`.

Canonical job fields: `id`, `external_id`, `title`, `employer`, `location`, `remote_policy`, `employment_type`, `seniority`, `specialties`, `description`, `requirements`, `salary_min/max/currency`, `work_authorization`, `source_url`, `apply_url`, `posted_at`, `discovered_at`, `last_verified_at`, `deadline`, `status`. Store missing facts as `null`/`unknown`, not guesses.

Deduplication: exact source IDs and canonical URLs first; normalized company/title/location and description similarity second. Preserve all origin URLs and prefer employer-hosted postings.

## Matching and ranking
1. Extract candidate profile from CV, then require factual review before activation.
2. Apply hard exclusions (academia, location, seniority, employment preferences) without treating unknowns as failures.
3. Assign an explainable 0–100 fit score using configurable default weights:
   - Scientific specialization **30%**
   - Technical experience **20%**
   - Seniority / scientific leadership **15%**
   - Industry / therapeutic area **10%**
   - Geographic fit **10%**
   - Research outcomes and achievements **10%**
   - Salary / work arrangements **5%**
4. Output evidence for strengths, gaps, must-have requirements, confidence, and actionable recommendation.
5. Save scoring version and candidate-profile version; cache based on content hash.

## Application workflow
- Modes: **Discover**, **Prepare**, **Assisted apply**.
- Create role-tailored CV and cover letter (DOCX; PDF optional) strictly from verified candidate facts; track versions.
- Default flow: `saved -> preparing -> awaiting_review -> approved -> submitting -> submitted`.
- Exception states: `blocked`, `manual_required`, `failed`, `unknown_result`, `withdrawn`.
- Playwright may fill forms and attach files only where permitted. Stop for unknown screening answers, legal/visa declarations, demographic questions, assessments, sign-in/MFA and CAPTCHAs.
- **Approval must be explicit and bound to a particular job + application version.** Do not retry uncertain submissions automatically. A click is not proof of submission; require confirmation page, ID or acknowledgment.

## Dashboard
Tabs:
- **Jobs:** ranked feed, employer, location, title, posted/discovered date, fit score, application link, filters, CSV export.
- **Job detail:** description, source provenance, matching evidence, requirements, save/reject/prepare actions.
- **Applications:** document previews, approval queue, state, confirmations, notes, follow-up dates.
- **Profile:** verified master CV, specialty, title, commute, salary, visa and work-policy preferences.
- **Sources:** enabled adapters, last sync, health/errors, add employer/URL.

## Security and compliance
- Local-first storage, secrets in `.env` or secret manager (never Git), encrypted sensitive data where feasible.
- Minimize personal data sent to external LLMs; no unneeded storage of sensitive screening responses.
- Job descriptions and webpages are **untrusted data**, not agent instructions (prompt-injection defenses).
- Respect website terms, access controls, rate limits and UK data protection requirements as applicable.
- Provide export/delete for candidate data and audit logs for approvals/submissions.

## Milestones and definition of done

### M1 — Working discovery MVP
**Build:** repository structure, Docker, README, settings, SQLite schema/migrations, profile import, 2 *verified working* compliant discovery integrations, normalization, deduplication, industry filters, basic Streamlit job list, direct apply links, CSV export.

**Done when:** clean Docker start works; job listings have source provenance; duplicates merge; academic roles are excluded; source failure is nonfatal; tests pass.

### M2 — Smart matching
**Build:** CV-to-profile extraction and review, editable preferences, deterministic filtering, LLM+semantic evaluation, evidence-based scores, caching, user overrides.

**Done when:** every ranked job has clear evidence and uncertainty flags; unknown requirements are not misrepresented; the same inputs produce stable structured records.

### M3 — Application preparation
**Build:** tailored DOCX CV/cover letter, screening answer drafts, document diff/changelog, application state tracking.

**Done when:** generated claims trace to verified profile data; files are editable; each version is linked to a job.

### M4 — Assisted applications
**Build:** allowed Playwright workflows, form previews, per-application approval, submission verification, manual fallback.

**Done when:** no unapproved submit is possible, ambiguous outcomes cannot trigger duplicate retries, CAPTCHA/MFA requires human handling, all actions are auditable. Test with mock forms; no live test submission without permission.

### M5 — Scheduled operation
**Build:** daily/twice-daily sync, status rechecks, digest via email/Telegram (optional), source health UI, optional n8n hooks.

**Done when:** only new/changed jobs generate alerts; expired vacancies are marked conservatively; scheduler survives restarts.

## Suggested repository layout
```text
biotech-job-agent/
  PLAN.md
  README.md
  docker-compose.yml
  .env.example
  config/{candidate.example.yaml,sources.yaml}
  app/
    api/
    dashboard/
    models/
    sources/{base.py,ats/,boards/}
    pipeline/{normalize.py,dedupe.py,filter.py,score.py}
    candidate/
    applications/{documents.py,workflow.py,browser/}
    notifications/
    scheduler/
    security/
  migrations/
  tests/{fixtures/,unit/,integration/,browser/}
```

## Execution instructions for Claude Code
1. Inspect repository and environment; preserve existing work.
2. Verify actual API/docs and platform access before implementing integrations. **Do not invent endpoints.** If web verification is unavailable, label adapters unverified and use fixtures/manual imports instead.
3. Briefly outline architecture, then **implement M1 fully** rather than scaffolding all milestones.
4. Run tests and startup checks, fix errors, and report what genuinely runs.
5. Clearly distinguish completed features, partially functioning adapters, access restrictions, and deferred work.
6. Continue to M2–M5 only after each previous milestone passes its acceptance tests.
7. Optimize for low operating cost, maintainability, and installability on macOS, Windows/WSL2, and Linux.

## Initial next actions
- [ ] Initialize repository, configs, CI/test tooling and Docker Compose.
- [ ] Confirm compliant live access to at least two sources (prefer public employer ATS feeds).
- [ ] Implement source interface and example adapters with fixtures.
- [ ] Create schema, normalization, canonicalization and deduplication.
- [ ] Add configurable London-area geography and strict industry-only filtering.
- [ ] Deliver Streamlit feed and direct apply links.
- [ ] Run full M1 test suite and document setup.

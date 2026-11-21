# Search and scoring

## Discover and score

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

## Fresh postings

`search.max_posting_age_days` defaults to 7; set it to 0 or null to disable the age filter. Freshness limits scoring and alerts for new prospects. Saved jobs and applications remain visible in list and review with a stale or closed label; closed jobs cannot be applied automatically. The list and review show the posting date, or a clearly labelled first-seen proxy only when the source had previously synced successfully. Unknown dates on a first sync remain visible as unknown. Greenhouse uses `first_published`, never `updated_at`; Lever uses `createdAt`.

## Preferences

- `config/candidate.yaml`: reviewed resume facts and evidence, resume hash, specialties, target title tokens and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Generic scientist titles without a known level retain unknown seniority for scoring. Explicit level/role mismatches remain excluded. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 17-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.

## Deduplication and source availability

- Source-scoped IDs and canonical URLs identify origins. New probable duplicates group when the employer, similar title, overlapping location and similar descriptions match; conflicting explicit requisition IDs keep openings separate. Groups show their origin IDs, locations and links in list and review, and appear once in scoring and alerts. Grouping is conservative and may need review: run `bio-jobs unmerge JOB_ID ORIGIN_ID` to separate a non-canonical origin. Existing application jobs are kept separate. Previously stored probable duplicates are grouped for list, review, scoring and alerts without moving their histories; grouped job IDs are shown in list and review. Each job with an application stays visible as its own position. Historical origins need a source refresh before they can be separated. The canonical origin owns job facts; after it expires, the earliest eligible present origin with a saved posting takes ownership. Sources are direct employer ATS feeds or public careers-page watchers.
- A failing source does not stop others or close old listings. Complete successful ATS snapshots close an origin after two absences; reappearance reopens it. Partial snapshots and page watchers never close listings. Check the last-seen date and employer page when availability is uncertain.

See [source operation and local storage](operation.md) and [verified sources](STATUS.md).

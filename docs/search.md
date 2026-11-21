# Search and scoring

## Discover and score

`scan` refreshes every enabled source and scores new or changed eligible jobs; `sync` only refreshes and `analyze` only scores stored jobs. Discovery itself does not call an LLM or use a browser.

`list`, `review` and `export` accept `--title`, `--employer`, `--location`, `--source`, `--job`, `--since YYYY-MM-DD`, `--status`, `--application-state`, `--limit` and `--all-scores`. They honour `analyst.min_score` (default 50); unscored jobs are hidden by that gate. `--all-scores` includes low/unscored jobs for inspection and overrides; setting `min_score: 0` disables the gate. Rejected jobs are hidden unless selected with `--status rejected` or `--status all`. Saved jobs and applications in progress remain in the default review queue, even when unscored; deferred jobs return next time. Decisions and overrides persist across refreshes and rescoring. List/review default to 50 rows; export includes all matching jobs unless limited. Equal scores sort by discovery date and ID.

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
invalid single-job answers receive one repair attempt, whose prompt lists the validation
errors with how to fix them, and may use the configured fallback. Before validation,
unknown extra keys inside must-have items are dropped and near-synonym statuses are mapped
conservatively: partial, likely or possible matches become unknown, "not met" or "missing"
becomes unmet only with explicit candidate evidence, and nothing is mapped to met. Each
adjustment is listed under `normalized` in the LLM usage log; other schema errors still fail. Cut-off answers may also use that fallback without a quota cooldown.

Next to the LLM score, `list`, `review` and `export` show a **resume keyword match** computed without any LLM call: the share of the job's biotech/pharma R&D keywords (techniques, assays, modalities, therapeutic areas, platforms, multiword terms such as "flow cytometry" or "single-cell RNA-seq", and acronyms from the job text) that also appear in `resume.md`, with the top present and missing ones. Benefits, EEO, privacy and company-blurb text is ignored, and case, plurals, hyphens, British spellings and common abbreviations are folded together. The result is cached per job and recomputed when the job text or `resume.md` changes. It is informational only: it never changes the LLM score, the score gate or any filter. See [resume keyword match](keywords.md).

## Add one job by link

```sh
uv run bio-jobs add https://job-boards.greenhouse.io/<board>/jobs/<id>
uv run bio-jobs add https://careers.example.com/jobs/senior-scientist --employer "Example Bio"
```

`add` stores one posting you found yourself. Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`, embedded `job_app?for=…&token=…`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`) and Workable (`apply.workable.com/<account>/j/<code>`, `<account>.workable.com/j/<code>`) links are read through the same public APIs and adapters as discovery; Ashby and Workable have no single-posting API, so their public board is read once. Any other https page is fetched once (GET only, robots.txt, pacing and timeouts as discovery, no redirects followed) and reduced to plain text: job markup (`JobPosting`) when present, otherwise the page title, heading and main text. Page content is untrusted data and is never followed or executed. `--employer` names the employer when the page or ATS does not.

The job is stored with an origin marked as added by link (source `link-<employer>`), deduplicated against stored jobs, scored when the profile is reviewed, and the command prints its job ID, score and `uv run bio-jobs review --job <ID>`. The employer need not be on the watchlist. When the industry (not on the reviewed watchlist), role, seniority, geography or freshness filter would normally exclude it, the job is still stored, `add`, `show` and `review` say which filters apply, and it stays in review (bypassing the score gate) for you to save or reject. Adding the same link again, or a link to a job already stored, reports the existing job without fetching. A probable duplicate of a stored job joins it as another origin. Failures (unreachable page, robots.txt disallow, HTTP errors, redirects, no job text) print a plain message and a next step. Jobs added by link are not expired by later syncs; reject them in review when they close.

## Fresh postings

`search.max_posting_age_days` defaults to 7; set it to 0 or null to disable the age filter. Freshness limits scoring and alerts for new prospects. Saved jobs and applications remain visible in list and review with a stale or closed label; closed jobs cannot be applied automatically. The list and review show the posting date, or a clearly labelled first-seen proxy only when the source had previously synced successfully. Unknown dates on a first sync remain visible as unknown. Greenhouse uses `first_published`, never `updated_at`; Lever uses `createdAt`.

## Preferences

- `config/candidate.yaml`: reviewed resume facts and evidence, resume hash, specialties, target title tokens and independent geography switches for London/Greater London, Slough, Stevenage, Uxbridge, Cambridge and Oxford. Generic scientist titles without a known level retain unknown seniority for scoring. Explicit level/role mismatches remain excluded. Research Leader and Senior Research Investigator are example related senior R&D titles; remove them for a narrower search. Associate Director / Head of Biology can be added explicitly.
- Unknown/missing location is retained as **needs review** by default. Disable `include_unknown_location` to hide it. A country-wide UK remote listing does not prove a London workplace. Explicit non-UK countries and other locations are excluded.
- Uxbridge has its own switch even though it is in Greater London. Oxford includes Oxfordshire/Milton Park. Geography uses explicit location fields, never employer headquarters or incidental description mentions.
- `config/sources.yaml` plus the 28-employer `firms.yaml` watchlist: only vetted commercial life-sciences employers, plus the [Biotechnology Jobs board feed](#job-board-feed), whose postings are vetted one by one. `industry_verified` must be true; academic employers, academic role titles, postdocs, PhD positions, faculty and sales roles are excluded. A PhD qualification or university collaboration **in the description** does not make an industry role academic.
- Current preferences are reapplied when viewing/exporting stored jobs. Widening filters requires a fresh sync to admit previously rejected postings; the persistent cooldown still applies. A source's confirmed excluded update hides its previous accepted listing.

## Deduplication and source availability

- Source-scoped IDs and canonical URLs identify origins. New probable duplicates group when the employer, similar title, overlapping location and similar descriptions match; conflicting explicit requisition IDs keep openings separate. Groups show their origin IDs, locations and links in list and review, and appear once in scoring and alerts. Grouping is conservative and may need review: run `bio-jobs unmerge JOB_ID ORIGIN_ID` to separate a non-canonical origin. Existing application jobs are kept separate. Previously stored probable duplicates are grouped for list, review, scoring and alerts without moving their histories; grouped job IDs are shown in list and review. Each job with an application stays visible as its own position. Historical origins need a source refresh before they can be separated. The canonical origin owns job facts; after it expires, the earliest eligible present origin with a saved posting takes ownership. Sources are direct employer ATS feeds, public careers-page watchers and the official Biotechnology Jobs feed.
- A failing source does not stop others or close old listings. Complete successful ATS snapshots close an origin after two absences; reappearance reopens it. Partial snapshots, page watchers and the job board feed never close listings. Check the last-seen date and employer page when availability is uncertain.

## Job board feed

`biotechnologyjobs-jsonfeed` in `config/sources.yaml` reads the official [Biotechnology Jobs](https://biotechnologyjobs.co.uk/) JSON Feed, `https://biotechnologyjobs.co.uk/jobs.json`, enabled by default. Its robots.txt lists the feed as public, the publisher offers it under CC BY 4.0 with a link back to the board, and asks for no more than one fetch per hour ([feed terms](https://biotechnologyjobs.co.uk/feeds)), so the source's refresh interval is 3600 seconds. One sync makes two GETs, robots.txt and the feed, through the same robots-checking, paced client as the ATS adapters. Job pages, `/apply/` and `/redirect/` links are never fetched.

Each item's Schema.org `JobPosting` supplies the title, hiring organisation, location and country, posting date (`datePosted`), workplace type, level, annual salary and description; the source ID is the item's canonical job URL, as the feed documents, and the apply link is the job's board page. `validThrough` is a rolling feed expiry, not a closing date, and is not stored.

The board is reviewed as a life-sciences board (`industry_verified: true`); its employers are not, so each posting goes through the standard filters plus two board-only checks: employers named as institutes, trusts, research councils, schools or charities are excluded as `academic_employer`, and recruitment agency adverts (agency-style names such as Recruitment, Resourcing or Talent, or text such as "our client" or "on behalf of a") are excluded as `recruitment_agency`, because they hide the actual employer. A posting from an employer on the watchlist shares that employer's reviewed record and groups with its own ATS posting; any other employer is stored with industry `unreviewed`. `list` and `review` say which job board a job came from and, for an employer not on the watchlist, ask you to check it is a commercial life-sciences employer before applying. Keyword filtering is a first pass: review board jobs like leads.

The feed holds only the latest 50 active jobs, so a job dropping out of it is not evidence it closed: board origins are never expired by syncs, and the freshness filter ages them out of new prospects. Check the board page before applying to an older one. Set `enabled: false` on the entry to stop reading it. [`feed-review`](feed-review.md) remains for reviewing a saved copy of the feed for employer leads.

See [source operation and local storage](operation.md) and [verified sources](STATUS.md).

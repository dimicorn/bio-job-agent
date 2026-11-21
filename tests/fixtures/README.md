# Recorded API fixtures

Captured 2026-11-15 around 14:06 UTC from the exact public endpoints in `docs/STATUS.md`.

- `lever.json`: two actual Mytos response records: Principal Scientist (PD/MSAT) and Associate Scientist. IDs, titles, categories, countries, source URLs and apply URLs are recorded values.
- `greenhouse.json`: two actual Isomorphic Labs records: Research Leader (In-vitro Pharmacology, Exploratory Sciences), London and Principal Software Engineer (Infrastructure), London. IDs, titles, locations, URLs, departments/offices and update timestamps are recorded values. The wrapper total is adjusted to the selected subset.
- `*_robots.txt`: actual API-host robots responses.

Descriptions, requirements lists and closing prose are replaced with clearly synthetic content, and unrelated fields are removed, to avoid redistributing employer text or personal contact details. The records preserve fields and encoding used by the adapters. They are test data, not demo vacancies or a current inventory. Failure/academic/duplicate variants are generated in tests. Only explicit `--live -m live` tests may use the network.

Additional source fixtures (2026-11-15):

- `workable.json`: recorded OMass Therapeutics widget response from `https://apply.workable.com/api/v1/widget/accounts/omass-therapeutics?details=true`. Employer summary and job description are replaced with synthetic text; job ID, title, location, dates and URLs are recorded.
- `site.html`: reduced public response from `https://www.rxcelerate.com/careers/`. Title, heading and no-vacancies state are recorded; the explanatory paragraph is synthetic. JSON-LD, link, shell and restriction variants are generated in tests.
- `ashby.json`: metadata subset of the public Voleon Posting API recording in quant-job-agent's `tests/fixtures/ashby_board.json`, from `https://api.ashbyhq.com/posting-api/job-board/voleon`. Descriptions are synthetic. This is a reference recording, not a recording of a watchlist employer. The financial employer is only parser test data.

`firms.yaml` is the active watchlist; `bio-jobs firms --validate` checks public access, payloads and employer identity without opening the local database. Site snapshots preserve public text/link changes in `raw_postings`; they are for manual review and do not claim that the page itself is a scientist vacancy.

`biotechnologyjobs.json` (2026-11-21): six items recorded from the public JSON Feed `https://biotechnologyjobs.co.uk/jobs.json` (CC BY 4.0, Biotechnology Jobs, https://biotechnologyjobs.co.uk/): Isomorphic Labs, Genomics, a recruitment agency, a university, a remote GB role and a US role. Feed metadata, item IDs, URLs, titles, employers, dates, tags, locations and salaries are recorded; descriptions and summaries are synthetic, and education, experience and benefits fields are removed.

Form question fixtures (`form_api/`, 2026-11-21) are synthetic. Their structure follows the public Greenhouse `?questions=true` response, the Lever `…/apply` page markup and the Workable `api/v1/jobs/<shortcode>/form` response; every label, option, ID and URL is invented.

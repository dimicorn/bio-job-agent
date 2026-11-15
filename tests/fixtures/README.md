# Recorded API fixtures

Captured 2026-11-15 around 14:06 UTC from the exact public endpoints in `docs/STATUS.md`.

- `lever.json`: two actual Mytos response records: Principal Scientist (PD/MSAT) and Associate Scientist. IDs, titles, categories, countries, source URLs and apply URLs are recorded values.
- `greenhouse.json`: two actual Isomorphic Labs records: Research Leader (In-vitro Pharmacology, Exploratory Sciences), London and Principal Software Engineer (Infrastructure), London. IDs, titles, locations, URLs, departments/offices and update timestamps are recorded values. The wrapper total is adjusted to the selected subset.
- `*_robots.txt`: actual API-host robots responses.

Descriptions, requirements lists and closing prose are replaced with clearly synthetic content, and unrelated fields are removed, to avoid redistributing employer text or personal contact details. The records preserve fields and encoding used by the adapters. They are test data, not demo vacancies or a current inventory. Failure/academic/duplicate variants are generated in tests. Only explicit `--live -m live` tests may use the network.

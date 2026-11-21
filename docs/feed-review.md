# Experimental feed lead review

`bio-jobs feed-review /path/to/jobs.json` examines a locally obtained
[Biotechnology Jobs JSON Feed](https://biotechnologyjobs.co.uk/feeds) for London
employer leads. It is an explicit, offline review tool for finding employers to add to the
watchlist. It does not add employers, jobs or approvals to the database; normal
refresh and watch runs read the same feed as the `biotechnologyjobs-jsonfeed`
source (see [job board feed](search.md#job-board-feed)). Names already present in `firms.yaml` are labelled reviewed; every
other employer remains unreviewed. Check the employer, industry and original
board independently and follow the existing `firms` review and validation
workflow before adding a source. Name matching is exact after normalization and
is not proof of identity. Recruiter and academic keyword filtering is only a
first pass, so review all leads. Only explicit links to supported ATS hosts are
shown; feed redirects and application links are never followed. No original ATS
link means the listing cannot be imported automatically.

The publisher offers the latest 50 active jobs as CC BY 4.0 with a visible link
back to [Biotechnology Jobs](https://biotechnologyjobs.co.uk/), and requests no
more than one feed fetch per hour ([feed terms](https://biotechnologyjobs.co.uk/feeds)).
Its robots file permits `/jobs.json` but disallows `/redirect/` and `/apply/`
([robots.txt](https://biotechnologyjobs.co.uk/robots.txt), checked 2026-11-19).
Download the feed manually at most once per hour, and not in the same hour as a
sync of the feed source; this command performs no network requests. The feed's
limited window and missing original ATS links mean that for employer discovery it
is a lead source, not a complete one.

A single feed snapshot on 2026-11-19 contained 50 listings; this tool retained
11 London leads, including two employer names absent from the watchlist, and
found no explicit supported ATS links. It added zero eligible roles. This is a
measurement of this snapshot and staging path, not evidence that the feed has
no other relevant jobs or that those leads satisfy the candidate's role filters.

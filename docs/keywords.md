# Resume keyword match

A rough, free check in the spirit of an employer's ATS keyword screen: which keywords of a job
appear in your `resume.md` and which do not. It runs locally with no LLM call, and it is
informational only. It never changes the LLM score, `analyst.min_score`, the review queue or any
filter.

## Where it shows

- `list`: a **Resume keywords** column, for example `62% (8/13)` and the first three missing
  keywords. `no resume.md` means there is nothing to compare against.
- `review`: the percentage with the counts, then up to eight keywords found in the resume and up
  to eight missing ones, most-mentioned first.
- `export`: `keyword_match` (the percentage), `keywords_present` and `keywords_missing`
  (all of them, separated by `; `).

## How it works

1. **Job text.** The title, description and requirements, with boilerplate removed. Benefits,
   "what we offer", equal-opportunity, diversity, privacy and recruitment-agency sections are
   dropped up to the next content heading ("Responsibilities", "Requirements", "About you" and
   similar). A company blurb ("About us", "Who we are") is dropped only when a content heading
   follows it, so a posting that opens with a blurb and runs straight into the job keeps its job
   text. Single sentences about salary, pension, holidays, insurance, share options, funding
   rounds or protected characteristics are dropped wherever they appear.
2. **Keywords.** A fixed biotech/pharma R&D vocabulary in `app/keywords.py`: molecular and cell
   biology techniques, assays, biophysics, omics and sequencing, computational tools, modalities,
   therapeutic areas, discovery and development stages, process, quality and regulatory terms,
   and a few role signals (PhD, line management, cross-functional work). Each keyword has
   alternative spellings, so "FACS" counts as flow cytometry and "scRNA-seq" as single-cell
   RNA-seq. Acronyms in the job text that are not in the vocabulary (for example "CD34" or
   "MSAT") count as keywords too, apart from a stop list of places, legal forms, degrees and
   HR words and the employer's own name.
3. **Normalisation.** Both texts are lower-cased; hyphens, slashes and joins are treated alike
   ("RNA-seq", "RNA seq", "RNAseq"); trailing plurals are accepted; British spellings are folded
   ("tumour", "characterisation", "analyse"); "Ph.D." equals "PhD". Short spellings that are
   ordinary words in lower case ("IND", "SEC", "IO", "AI", "ML", "DoE") only match as written.
4. **Overlaps.** A keyword seen only inside a longer one ("RNA-seq" inside "single-cell RNA-seq")
   is not counted separately; a separate mention of it is.
5. **Score.** `round(100 × present / found)`. Jobs with no recognised keywords show
   "no keywords found" and no percentage.

## Caching

Results are stored in the `keyword_matches` table, one row per job with a hash of the job text,
`resume.md` and the vocabulary version. `list`, `review` and `export` reuse the stored result
and recompute only the jobs whose text or resume changed. There are no settings.

## Limits

Matching is literal. A missing keyword can still be covered by experience the resume describes
in other words, and a present keyword says nothing about depth. Use it to spot vocabulary worth
adding to a tailored resume, not to decide whether to apply.

"""Keyword match against resume.md: synthetic job texts and resumes only."""

import json

from app.keywords import (
    attach_keyword_matches,
    job_text,
    keyword_match,
    normalize,
    summary,
)
from app.settings import Settings

JOB = (
    "Senior Scientist, Flow Cytometry. About us: Examplegen Bio is a Series B biotech founded "
    "in 2019 and backed by leading venture capital funds. Our oncology pipeline grows. "
    "About the role: You will develop cell-based assays and run multi-colour flow cytometry "
    "and single-cell RNA-seq on primary T cells. Requirements: PhD in immunology; experience "
    "with CRISPR knockouts, ELISA and MSD; Python or R (Bioconductor); qPCR. Experience with "
    "SPR and LC-MS/MS is desirable. Familiarity with TR-FRET and CD34+ HSCs. "
    "What we offer: competitive salary, pension, private medical insurance with dental cover, "
    "Python lunch club, 25 days holiday and share options. Examplegen is an equal opportunity "
    "employer and does not discriminate on the basis of disability or Western blot. "
    "Read our GDPR privacy notice."
)
RESUME = (
    "# Alex Example\nPostdoctoral researcher. Single cell RNA sequencing (10x), FACS, ELISAs, "
    "CRISPR/Cas9 gene editing, Python. Immunologist working on T-cell biology. "
    "Ph.D. in Immunology. HSC biology; cell-based assay development."
)


def match(job=JOB, resume=RESUME, **kwargs):
    return keyword_match("Senior Scientist, Flow Cytometry", job, resume, **kwargs)


def test_present_missing_and_percentage():
    result = match(employer="Examplegen Bio")
    present, missing = set(result["present"]), set(result["missing"])
    assert {
        "flow cytometry",
        "single-cell RNA-seq",
        "immunology",
        "PhD",
        "CRISPR",
        "ELISA",
        "Python",
        "cell-based assays",
        "HSC",
    } <= present
    assert {"MSD", "qPCR", "SPR", "mass spectrometry", "AlphaLISA/HTRF", "CD34", "R"} <= missing
    assert not present & missing
    assert result["total"] == len(present) + len(missing)
    assert result["score"] == round(100 * len(present) / result["total"])


def test_boilerplate_and_blurb_are_ignored():
    result = match(employer="Examplegen Bio")
    found = set(result["present"]) | set(result["missing"])
    # Only in the company blurb and the benefits/EEO text.
    assert "oncology" not in found and "Western blot" not in found
    text = job_text("Title", JOB)
    assert "pension" not in text and "venture" not in text and "equal opportunity" not in text
    assert "Examplegen" not in found and "GDPR" not in found


def test_blurb_without_a_later_heading_keeps_the_job():
    text = "About us: we are a small team. You will run Western blots and ELISA daily."
    result = keyword_match("Scientist", text, "western blotting")
    assert set(result["missing"]) == {"ELISA"}
    assert result["present"] == ["Western blot"]


def test_longer_term_hides_its_parts_but_separate_mentions_count():
    only_long = keyword_match("Scientist", "Experience with single-cell RNA-seq.", "")
    assert only_long["missing"] == ["single-cell RNA-seq"]
    both = keyword_match("Scientist", "Single-cell RNA-seq and bulk RNA-seq.", "")
    assert set(both["missing"]) == {"single-cell RNA-seq", "RNA-seq"}


def test_normalisation_case_plurals_hyphens_spelling_and_abbreviations():
    job = (
        "Requirements: monoclonal antibodies, LC-MS/MS, tumour biology, characterisation, "
        "next-generation sequencing, scRNAseq, Biacore, organoids"
    )
    resume = (
        "Monoclonal antibody (mAb) discovery; mass spec; tumor biology; NGS; single cell "
        "RNA-seq; surface plasmon resonance; organoid culture"
    )
    result = keyword_match("Scientist", job, resume)
    assert result["missing"] == []
    assert {"monoclonal antibodies", "mass spectrometry", "oncology", "NGS", "SPR"} <= set(
        result["present"]
    )
    assert normalize("Cell-Based") == normalize("cell based") == "cell based"
    assert normalize("characterisation") == "characterization"


def test_short_ambiguous_terms_need_their_written_case():
    # "ind", "sec", "io" and "doe" are ordinary prose in lower case.
    prose = keyword_match("Scientist", "Kind people, a second io port and doe-eyed staff.", "")
    assert prose is None
    upper = keyword_match("Scientist", "Requirements: IND-enabling studies, SEC and DoE.", "")
    assert set(upper["missing"]) == {
        "IND-enabling studies",
        "chromatography",
        "design of experiments",
    }


def test_ordering_by_frequency_then_first_mention():
    job = "Requirements: ELISA. qPCR. qPCR. Flow cytometry. qPCR. ELISA."
    result = keyword_match("Scientist", job, "")
    assert result["missing"] == ["qPCR", "ELISA", "flow cytometry"]


def test_stop_acronyms_and_no_keywords():
    assert keyword_match("Manager", "Based in the UK. Join our HR team ASAP.", "") is None
    assert keyword_match("", "", "") is None


def test_summary_cell():
    assert summary(None) == "no keywords found"
    cell = summary({"score": 50, "present": ["a"], "missing": ["b", "c", "d", "e"], "total": 5})
    assert cell == "50% (1/5)\nmissing: b, c, d"


def _job(conn, description, title="Scientist"):
    conn.execute(
        """INSERT OR IGNORE INTO employers(id,name,industry,review_url,industry_verified)
        VALUES (1,'E','biotech','https://example.com',1)"""
    )
    cur = conn.execute(
        """INSERT INTO jobs(employer_id,title_key,location_key,canonical_url,payload,description,
        discovered_at,last_verified_at) VALUES (1,?,'','https://example.com/j','{}',?,
        '2026-01-01','2026-01-01')""",
        (title.casefold(), description),
    )
    return {
        "id": cur.lastrowid,
        "title": title,
        "employer": "E",
        "description": description,
        "requirements": None,
    }


def test_cache_reuses_and_recomputes_on_job_or_resume_change(conn, tmp_path, monkeypatch):
    cfg = Settings(_env_file=None, resume_path=tmp_path / "resume.md")
    row = _job(conn, "Requirements: ELISA and qPCR.")
    attach_keyword_matches(conn, cfg, [row])
    assert row["keywords"] is None and row["keywords_resume"] is False
    assert conn.execute("SELECT count(*) FROM keyword_matches").fetchone()[0] == 0

    cfg.resume_path.write_text("ELISA", encoding="utf-8")
    attach_keyword_matches(conn, cfg, [row])
    assert row["keywords"]["present"] == ["ELISA"] and row["keywords"]["score"] == 50
    stored = conn.execute("SELECT content_hash,payload FROM keyword_matches").fetchone()
    assert json.loads(stored["payload"]) == row["keywords"]

    calls = []
    import app.keywords as module

    real = module.keyword_match
    monkeypatch.setattr(module, "keyword_match", lambda *a, **k: calls.append(1) or real(*a, **k))
    attach_keyword_matches(conn, cfg, [dict(row)])
    assert calls == []  # unchanged job and resume: cached

    cfg.resume_path.write_text("ELISA, qPCR", encoding="utf-8")
    fresh = dict(row)
    attach_keyword_matches(conn, cfg, [fresh])
    assert calls == [1] and fresh["keywords"]["score"] == 100

    changed = dict(row, description="Requirements: ELISA, qPCR and SPR.")
    attach_keyword_matches(conn, cfg, [changed])
    assert calls == [1, 1] and changed["keywords"]["missing"] == ["SPR"]
    assert conn.execute("SELECT count(*) FROM keyword_matches").fetchone()[0] == 1


# A collapsed Ashby-style posting: bullets joined by " - ", a mission blurb naming the
# employer, office locations with an abbreviation, and a benefits list.
ASHBY = (
    "Location: Cambridge, Boston or RTP (Hybrid) The Mission: Why We Exist Examplomics is a "
    "techbio using genomics and machine learning to accelerate drug discovery. Examplomics "
    "aims to help people live longer. Role Purpose You will turn genetic associations into "
    "targets. A Day in the Life - Prioritising causal genes with fine-mapping and eQTL "
    "colocalisation - Mining single-cell and proteomic datasets Who You Are - A PhD in a "
    "quantitative discipline - Statistical programming in Python or R It's a bonus if you "
    "have: - GWAS experience - Pipelines in Snakemake or Nextflow - Git and Docker "
    "Your Package - Competitive salary and bonus - Pension and private health insurance "
    "- Hybrid working from our Cambridge office. Examplomics is an equal-opportunity employer."
)
ASHBY_ARGS = {
    "employer": "Examplomics Ltd",
    "location": "Cambridge; Boston; Research Triangle Park",
}


def found(result):
    return set(result["present"]) | set(result["missing"])


def test_employer_name_and_locations_are_not_keywords():
    result = keyword_match("Scientist - Target Genetics", ASHBY, "", **ASHBY_ARGS)
    terms = found(result)
    assert "RTP" not in terms and "Examplomics" not in terms
    # "genomics", "machine learning" and "drug discovery" only appear in the mission blurb.
    assert not {"genomics", "machine learning", "drug discovery"} & terms
    text = job_text("T", ASHBY, **ASHBY_ARGS)
    assert "Examplomics" not in text and "Cambridge" not in text and "RTP" not in text


def test_bulleted_requirements_survive_a_benefits_word():
    # The whole " - " list used to be one sentence, dropped for "bonus" and "salary".
    result = keyword_match("Scientist", ASHBY, "", **ASHBY_ARGS)
    assert {
        "statistical genetics",
        "QTL mapping",
        "proteomics",
        "PhD",
        "Python",
        "Nextflow/Snakemake",
        "Git",
        "containers",
    } <= found(result)
    assert result["total"] == 8
    text = job_text("T", ASHBY, **ASHBY_ARGS)
    assert "Pension" not in text and "salary" not in text and "equal-opportunity" not in text


def test_employer_variants_and_location_initials():
    job = (
        "Isoexample Labs builds AI for drug discovery. Responsibilities: run ELISA at our "
        "South San Francisco site (SSF) with the IL platform team and Isoexample's chemists."
    )
    result = keyword_match(
        "Scientist", job, "", employer="Isoexample Labs", location="South San Francisco"
    )
    assert result["missing"] == ["ELISA"]


def test_leading_text_without_the_employer_name_is_kept():
    job = "You will run qPCR daily. Requirements: ELISA."
    result = keyword_match("Scientist", job, "", employer="Examplomics")
    assert set(result["missing"]) == {"qPCR", "ELISA"}


def test_cache_recomputes_when_the_location_changes(conn, tmp_path):
    cfg = Settings(_env_file=None, resume_path=tmp_path / "resume.md")
    cfg.resume_path.write_text("ELISA", encoding="utf-8")
    row = dict(_job(conn, "Requirements: ELISA at RTP."), location="")
    attach_keyword_matches(conn, cfg, [row])
    assert "RTP" in row["keywords"]["missing"]
    moved = dict(row, location="Research Triangle Park")
    attach_keyword_matches(conn, cfg, [moved])
    assert moved["keywords"]["missing"] == []

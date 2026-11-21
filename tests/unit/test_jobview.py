import io

import pytest

from app import cli, jobview
from app.console import _Console

DIMENSIONS = (
    "specialization",
    "technical",
    "seniority_leadership",
    "industry_therapeutic",
    "geography",
    "research_outcomes",
    "salary_arrangements",
)


def synthetic(job_id, *, words=40):
    """A synthetic job with a long, repetitive scoring explanation like real LLM output."""
    long = " ".join(["detail"] * words)
    dimensions = {
        name: {
            "score": None if name == "salary_arrangements" else 60,
            "confidence": "unknown" if name == "salary_arrangements" else "partial",
            "explanation": f"{name} explanation {long}",
            "candidate_evidence": None if name == "salary_arrangements" else "candidate quote",
            "job_evidence": None if name == "salary_arrangements" else "job quote",
        }
        for name in DIMENSIONS
    }
    must_haves = [
        {
            "requirement": "Right to work in the UK",
            "status": "unknown",
            "candidate_evidence": None,
            "job_evidence": "Applicants must have the right to work",
        },
        {
            "requirement": "PhD in cell biology",
            "status": "met",
            "candidate_evidence": "PhD",
            "job_evidence": "PhD required",
        },
    ]
    flags = [f"{name}: {d['confidence']} — {d['explanation']}" for name, d in dimensions.items()]
    flags += ["Must-have unknown: Right to work in the UK", "Salary not stated " + long]
    return {
        "id": job_id,
        "title": f"Principal Scientist, Synthetic Assay Development and Translational Biology {job_id}",
        "employer": "Example Therapeutics Holdings Limited",
        "location": "London, England, United Kingdom",
        "locations": ["London, England, United Kingdom", "Cambridge, England"],
        "date_label": "posted 2026-11-01T09:30:15+00:00 [stale]",
        "company_cooldown_until": "2026-12-01T00:00:00+00:00",
        "review": "saved",
        "application_state": "awaiting_review",
        "apply_url": f"https://jobs.example.com/{job_id}/apply",
        "description": "First   paragraph\nof the job.\n\n\nSecond paragraph " + long,
        "origins": [
            {"id": job_id, "source_id": "example", "apply_url": f"https://example.com/{job_id}"}
        ],
        "score": 73,
        "override": None,
        "scoring_weights": dict.fromkeys(DIMENSIONS, 10),
        "scoring_version": "v1",
        "profile_version": 1,
        "evaluation": {
            "score": 73,
            "recommendation": "apply",
            "confidence": "partial",
            "dimensions": dimensions,
            "strengths": ["Assay development leadership.", "assay development leadership"]
            + [f"Strength {n}" for n in range(5)],
            "gaps": ["No GMP experience", "No GMP experience."],
            "must_haves": must_haves,
            "uncertainty_flags": flags,
        },
        "keywords_resume": True,
        "keywords": {
            "score": 40,
            "total": 5,
            "present": ["assay development", "drug discovery"],
            "missing": ["gmp", "flow cytometry", "crispr"],
        },
    }


def render(width, draw):
    file = io.StringIO()
    target = _Console(file=file, width=width)
    draw(target)
    return file.getvalue()


@pytest.mark.parametrize("width", [80, 100, 200])
def test_list_is_one_line_per_job_within_the_terminal(width):
    rows = [synthetic(n) for n in range(1, 5)]
    out = render(width, lambda target: target.print(jobview.table(rows, target.width)))
    lines = out.splitlines()
    assert all(len(line) <= width for line in lines)
    # Title line, header, rule, then one line per job.
    assert len(lines) <= 3 + len(rows)
    body = [line for line in lines if line.split() and line.split()[0].isdigit()]
    assert len(body) == len(rows)
    assert "2026-11-01 stale" in out or "2026-11-0…" in out
    assert "73 apply" in out
    assert "40%" in out or width < 100
    assert "detail detail" not in out and "Uncertainty" not in out and "GMP" not in out


def test_list_cli_compact_at_100_columns(console_output, monkeypatch):
    output = console_output(io.StringIO(), width=100)
    cli._list([synthetic(n) for n in range(1, 5)])
    lines = output.getvalue().splitlines()
    assert len(lines) <= 4 * 2 + 4
    assert all(len(line) <= 100 for line in lines)
    assert "Details: bio-jobs show <ID>" in output.getvalue()


def test_short_cells():
    row = synthetic(1)
    assert jobview.posted(row) == "2026-11-01 stale"
    assert jobview.status(row) == "to review (cd)"
    assert jobview.status({"review": "new"}) == "new"
    assert jobview.score(row) == "73 apply"
    row["override"] = {"score": 90, "recommendation": "maybe", "reason": "checked"}
    row["score"] = 90
    assert jobview.score(row) == "90 maybe*"
    assert jobview.posted({"date_label": "first seen 2026-10-02T01:02:03+00:00"}) == (
        "seen 2026-10-02"
    )
    assert jobview.posted({"date_label": "posting date unknown"}) == "unknown"


def test_detail_has_sections_and_states_each_point_once():
    out = render(100, lambda target: jobview.detail(synthetic(1), target=target))
    for title in ("Evidence", "Gaps", "Must-haves to check", "Uncertainties", "Keyword match"):
        assert f"\n{title}\n" in out
    assert out.count("Right to work in the UK") == 1
    assert out.count("No GMP experience") == 1
    assert out.lower().count("assay development leadership") == 1
    assert "In resume: assay development, drug discovery" in out
    assert "Missing: gmp, flow cytometry, crispr" in out
    assert "Origin 1 (example): https://example.com/1" in out
    # Per-dimension notes and the description only with --full.
    assert "specialization explanation" not in out and "First paragraph" not in out
    full = render(100, lambda target: jobview.detail(synthetic(1), full=True, target=target))
    assert "specialization 60 (partial, weight 10)" in full
    assert "First paragraph of the job." in full


def test_review_card_is_short_and_hides_long_text(console_output, conn, monkeypatch):
    output = console_output(io.StringIO(), width=100)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    keys = iter("q")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    cli._review(conn, [synthetic(1)])
    out = output.getvalue()
    lines = out.splitlines()
    assert len(lines) <= 30
    for title in ("Evidence", "Gaps", "Must-haves to check", "Uncertainties"):
        assert f"\n{title}\n" in out
    assert out.count("Right to work in the UK") == 1
    assert "+3 more (x: scoring details)" in out
    assert "candidate quote" not in out and "First paragraph" not in out
    assert "a apply · r reject · l later · o open · ? more · q quit" in out
    assert "i description" not in out and "s save" not in out


def test_review_keys_show_description_and_scoring(console_output, conn, monkeypatch):
    output = console_output(io.StringIO(), width=100)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    keys = iter("ixq")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    cli._review(conn, [synthetic(1)])
    out = output.getvalue()
    assert "First paragraph of the job." in out
    assert "Must-have evidence" in out and "candidate: PhD; job: PhD required" in out
    assert "In resume: assay development" in out


def test_review_question_mark_lists_secondary_keys_on_same_card(console_output, conn, monkeypatch):
    output = console_output(io.StringIO(), width=100)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    keys = iter("?q")
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    cli._review(conn, [synthetic(1)])
    out = output.getvalue()
    assert "i description · x scoring · v override · m state" in out
    # No letter yet: letter keys, diff and mark submitted are not offered.
    for hidden in ("c letter", "e edit", "w rewrite", "d diff", "t mark", "s save"):
        assert hidden not in out
    assert out.count("? more") == 2  # the card's line is shown again after the list
    assert "1/1" in out and out.count("1/1") == 1


def _key_db(letters=0, answers=0, state=None):
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE material_versions(job_id, kind)")
    db.execute("CREATE TABLE applications(job_id, state)")
    db.executemany(
        "INSERT INTO material_versions VALUES (1, ?)",
        [("letter",)] * letters + [("answers",)] * answers,
    )
    if state:
        db.execute("INSERT INTO applications VALUES (1, ?)", (state,))
    return db


class _Cfg:
    def __init__(self, mode):
        self.apply = type("Apply", (), {"mode": mode})()


@pytest.mark.parametrize(
    ("mode", "label"),
    [("assisted", "a apply/fill"), ("manual", "a apply/open"), ("auto", "a apply")],
)
def test_review_default_line_label_follows_apply_mode(mode, label):
    primary, _ = cli._review_keys(_key_db(), {"id": 1}, _Cfg(mode))
    assert primary == f"{label} · r reject · l later · o open · ? more · q quit"


@pytest.mark.parametrize(
    ("db", "mode", "shown", "hidden"),
    [
        ({}, "manual", [], ["c letter", "e edit", "w rewrite", "d diff", "t mark"]),
        ({"letters": 1}, "manual", ["c letter", "e edit", "w rewrite"], ["d diff", "t mark"]),
        ({"letters": 2}, "manual", ["d diff"], ["t mark"]),
        ({"letters": 1, "state": "approved"}, "manual", ["t mark submitted"], ["d diff"]),
        ({"letters": 1, "state": "approved"}, "assisted", ["t mark submitted"], []),
        ({"letters": 1, "state": "approved"}, "auto", [], ["t mark"]),
        ({"letters": 1, "state": "manual_required"}, "auto", ["t mark submitted"], []),
        ({"letters": 1, "state": "submitted"}, "manual", [], ["t mark"]),
        ({"letters": 1, "state": "saved"}, "manual", [], ["t mark"]),
    ],
)
def test_review_secondary_keys_depend_on_job_state(db, mode, shown, hidden):
    _, more = cli._review_keys(_key_db(**db), {"id": 1}, _Cfg(mode))
    assert "s save" not in more
    for key in shown:
        assert key in more
    for key in hidden:
        assert key not in more

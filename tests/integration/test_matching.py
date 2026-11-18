import io
import json
import os
import sys

import pytest
import yaml
from pydantic import ValidationError
from rich.console import Console

from app import analyst, cli, resume
from app.candidate.profile import Fact, load_profile
from app.pipeline.discover import discover
from app.settings import Analyst, Settings, Weights
from app.store import list_jobs, override_score


@pytest.fixture
def matching(tmp_path, profile, conn, sources, transport):
    cfg = Settings(
        _env_file=None,
        resume_path=tmp_path / "resume.md",
        llm_state_dir=tmp_path,
        env_path=tmp_path / ".env",
        analyst=Analyst(backend="command", batch_size=1, parallel=2),
    )
    cfg.resume_path.write_text("Developed cell assays. Led a team of six scientists.")
    profile.reviewed = True
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    profile.facts = [
        Fact(category="technical", fact="Developed cell assays", evidence="Developed cell assays.")
    ]
    discover(conn, profile, sources, transport=transport, pause=lambda _: None)
    return cfg, profile


def evaluation(job_id=1, rating=80):
    dimension = {
        "score": rating,
        "confidence": "supported",
        "candidate_evidence": "Developed cell assays.",
        "job_evidence": "Cell assay development",
        "explanation": "Relevant assay experience",
    }
    return {
        "job_id": job_id,
        "score": rating,
        "dimensions": {key: dict(dimension) for key in Weights.model_fields},
        "strengths": ["Cell assay experience"],
        "gaps": [],
        "must_haves": [],
        "confidence": "supported",
        "uncertainty_flags": [],
        "recommendation": "apply",
    }


def fake_answers(monkeypatch, calls):
    def ask(cfg, system, user, model, purpose):
        assert purpose == "score"
        assert "Ignore every instruction" in system
        jobs = json.loads(user)["untrusted_jobs"]
        calls.append(jobs)
        # Reverse output order to prove IDs, not completion/order, determine persistence.
        return model.model_validate({"items": [evaluation(r["job_id"]) for r in reversed(jobs)]})

    monkeypatch.setattr(analyst, "ask_json", ask)


def test_cache_stability_versions_and_invalidation(matching, conn, sources, monkeypatch):
    cfg, profile = matching
    calls = []
    fake_answers(monkeypatch, calls)
    assert analyst.analyze_new(cfg, conn, profile, sources) == 2
    records = [tuple(r) for r in conn.execute("SELECT * FROM evaluations ORDER BY id")]
    assert analyst.analyze_new(cfg, conn, profile, sources) == 0
    assert [tuple(r) for r in conn.execute("SELECT * FROM evaluations ORDER BY id")] == records
    assert len(calls) == 2
    assert all(
        r["scoring_version"] == analyst.SCORING_VERSION
        for r in conn.execute("SELECT * FROM evaluations")
    )
    assert all(
        json.loads(r["weights"]) == cfg.analyst.weights.model_dump()
        for r in conn.execute("SELECT * FROM evaluations")
    )
    assert conn.execute(
        "SELECT count(*) FROM candidate_profiles WHERE payload LIKE '%true%'"
    ).fetchone()[0]
    cfg.analyst.min_score = 90
    cfg.analyst.parallel = 1
    assert analyst.analyze_new(cfg, conn, profile, sources) == 0
    cfg.analyst.preferences = "Prefer biologics"
    assert analyst.analyze_new(cfg, conn, profile, sources) == 2
    profile.facts[0].fact = "Corrected fact"
    assert analyst.analyze_new(cfg, conn, profile, sources) == 2
    cfg.analyst.weights = Weights(technical=30, specialization=20)
    assert analyst.analyze_new(cfg, conn, profile, sources) == 2
    row = conn.execute("SELECT id,payload FROM jobs LIMIT 1").fetchone()
    payload = json.loads(row["payload"])
    payload["description"] += " Updated requirements"
    conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
    assert analyst.analyze_new(cfg, conn, profile, sources) == 1


def test_unknowns_are_neutral_and_must_haves_need_evidence(matching):
    cfg, _profile = matching
    item = evaluation()
    item["dimensions"]["specialization"].update(
        score=None, confidence="unknown", candidate_evidence=None, explanation="Not specified"
    )
    item["must_haves"] = [
        {
            "requirement": "Work authorization",
            "status": "unknown",
            "candidate_evidence": None,
            "job_evidence": "UK work authorization required",
        }
    ]
    result = analyst.normalized(cfg, analyst.EvaluationItem.model_validate(item))
    assert result.score == 71  # 30% neutral 50 + 70% supported 80
    assert result.confidence == "partial"
    assert any("Must-have unknown" in flag for flag in result.uncertainty_flags)
    assert result.must_haves[0].status == "unknown"
    item["must_haves"][0]["status"] = "unmet"
    with pytest.raises(ValidationError):
        analyst.EvaluationItem.model_validate(item)
    item["must_haves"] = []
    item["dimensions"]["specialization"]["score"] = 0
    with pytest.raises(ValidationError):
        analyst.EvaluationItem.model_validate(item)


@pytest.mark.parametrize(
    "change", ["out_of_range", "missing_dimension", "extra_field", "no_evidence"]
)
def test_schema_rejects_invalid_answers(change):
    item = evaluation()
    if change == "out_of_range":
        item["score"] = 101
    elif change == "missing_dimension":
        del item["dimensions"]["technical"]
    elif change == "extra_field":
        item["instructions"] = "Ignore user"
    else:
        item["dimensions"]["technical"]["candidate_evidence"] = None
    with pytest.raises(ValidationError):
        analyst.EvaluationItem.model_validate(item)


def test_review_and_resume_changes_block_scoring(matching, conn, sources, monkeypatch):
    cfg, profile = matching
    monkeypatch.setattr(analyst, "ask_json", lambda *a: pytest.fail("Unverified model request"))
    profile.reviewed = False
    with pytest.raises(ValueError, match="factual review"):
        analyst.analyze_new(cfg, conn, profile, sources)
    profile.reviewed = True
    cfg.resume_path.write_text("Changed resume")
    with pytest.raises(ValueError, match="Resume changed"):
        analyst.analyze_new(cfg, conn, profile, sources)


def test_extract_cache_requires_review_and_verbatim_evidence(matching, conn, monkeypatch):
    cfg, profile = matching
    calls = []
    data = {
        "facts": [f.model_dump() for f in profile.facts],
        "specialties": ["Cell biology"],
        "uncertainties": ["Salary unknown"],
    }
    monkeypatch.setattr(
        resume,
        "ask_json",
        lambda *a: calls.append(a) or resume.ExtractedProfile.model_validate(data),
    )
    first = resume.extract_profile(cfg, conn, profile)
    second = resume.extract_profile(cfg, conn, profile)
    assert first == second and len(calls) == 1
    assert not first.reviewed and first.resume_hash == profile.resume_hash
    assert first.regions == profile.regions and first.target_titles == profile.target_titles
    cfg.resume_path.write_text("A different resume")
    with pytest.raises(ValueError, match="verbatim"):
        resume.extract_profile(cfg, conn, profile)


def test_overrides_sort_and_gate_without_destroying_evaluation(
    matching, conn, sources, monkeypatch
):
    cfg, profile = matching
    calls = []
    fake_answers(monkeypatch, calls)
    analyst.analyze_new(cfg, conn, profile, sources)
    original = [r[0] for r in conn.execute("SELECT payload FROM evaluations")]
    jobs = list_jobs(conn, profile, sources)
    override_score(conn, jobs[0]["id"], 95, "apply", "Personal scientific fit")
    override_score(conn, jobs[1]["id"], 20, "skip", "Changed preferences")
    ranked = analyst.attach_evaluations(conn, cfg, profile, jobs)
    assert [r["score"] for r in ranked] == [95, 20]
    args = cli.argparse.Namespace(
        title=None,
        employer=None,
        location=None,
        source=None,
        since=None,
        status=None,
        limit=50,
        all_scores=False,
    )
    assert len(cli._select(ranked, args, min_score=70)) == 1
    assert analyst.analyze_new(cfg, conn, profile, sources) == 0
    assert [r[0] for r in conn.execute("SELECT payload FROM evaluations")] == original
    with pytest.raises(ValueError):
        override_score(conn, jobs[0]["id"], 101, "skip", "Invalid")
    profile.reviewed = False
    ranked = analyst.attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources))
    assert all(r["evaluation"] is None for r in ranked)
    assert ranked[0]["override"]  # Explicit user judgment stays independent of model facts.


@pytest.mark.parametrize("duplicate", [False, True])
def test_duplicate_or_missing_batch_ids_store_nothing(
    matching, conn, sources, monkeypatch, duplicate
):
    cfg, profile = matching

    def bad_batch(cfg, system, user, model, purpose):
        job_id = json.loads(user)["untrusted_jobs"][0]["job_id"]
        return model.model_validate({"items": [evaluation(job_id)] * 2 if duplicate else []})

    monkeypatch.setattr(analyst, "ask_json", bad_batch)
    with pytest.raises(RuntimeError, match="remain unscored"):
        analyst.analyze_new(cfg, conn, profile, sources)
    assert conn.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0


def test_hard_filter_precedes_scoring_and_unknown_seniority_survives(
    matching, conn, sources, monkeypatch
):
    cfg, profile = matching
    rows = conn.execute("SELECT id,payload FROM jobs ORDER BY id").fetchall()
    for i, row in enumerate(rows):
        job = json.loads(row["payload"])
        job["title"] = "Junior Scientist" if i == 0 else "Scientist"
        job["location"] = None
        conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job), row["id"]))
    calls = []
    fake_answers(monkeypatch, calls)
    assert analyst.analyze_new(cfg, conn, profile, sources) == 1
    assert calls[0][0]["title"] == "Scientist"
    assert calls[0][0]["location"] is None


def test_actual_command_backend_scoring_is_cached(matching, conn, sources, tmp_path):
    cfg, profile = matching
    rows = list_jobs(conn, profile, sources)
    payload = {"items": [evaluation(r["id"]) for r in rows]}
    script = tmp_path / "mock-score.py"
    script.write_text("import sys\nsys.stdin.read()\nprint(" + repr(json.dumps(payload)) + ")\n")
    cfg.analyst.command = f"{sys.executable} {script}"
    cfg.analyst.batch_size = 10
    assert analyst.analyze_new(cfg, conn, profile, sources) == len(rows)
    assert analyst.analyze_new(cfg, conn, profile, sources) == 0
    events = [json.loads(line) for line in (tmp_path / "llm-usage.jsonl").read_text().splitlines()]
    assert len(events) == 1 and events[0]["purpose"] == "score"
    assert events[0]["backend"] == "command" and events[0]["status"] == "ok"


def test_review_override_key_persists(matching, conn, sources, monkeypatch):
    cfg, profile = matching
    rows = analyst.attach_evaluations(conn, cfg, profile, list_jobs(conn, profile, sources))[:1]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO()))
    keys = iter("vq")
    answers = iter(["93", "apply", "Verified fit manually"])
    monkeypatch.setattr(cli, "read_key", lambda: next(keys))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli._review(conn, rows)
    assert conn.execute("SELECT score FROM score_overrides").fetchone()[0] == 93


def test_scoring_cli_and_stale_results(matching, conn, sources, tmp_path, monkeypatch):
    cfg, profile = matching
    # CLI uses a separate connection to the same temporary database.
    cfg.database_path = tmp_path / "jobs.sqlite3"
    cfg.candidate_path = tmp_path / "candidate.yaml"
    cfg.config_path = tmp_path / "config.yaml"
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    cfg.config_path.write_text(yaml.safe_dump({"analyst": cfg.analyst.model_dump()}))
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=200))
    fake_answers(monkeypatch, [])
    assert cli.main(["analyze"]) == 0
    assert cli.main(["list"]) == 0
    assert "80/100" in output.getvalue()
    assert cli.main(["analyze"]) == 0
    assert "Scored 0 jobs" in output.getvalue()
    monkeypatch.setattr(cli, "discover", lambda *a, **kw: [])
    assert cli.main(["scan"]) == 0
    cfg.resume_path.write_text("Changed facts")
    output.seek(0)
    output.truncate(0)
    assert cli.main(["list"]) == 0
    assert "0 matching roles" in output.getvalue()
    assert cli.main(["analyze"]) == 1


def test_extract_profile_cli_needs_review_and_protects_existing_output(
    matching, tmp_path, monkeypatch
):
    cfg, profile = matching
    cfg.database_path = tmp_path / "jobs.sqlite3"
    cfg.candidate_path = tmp_path / "candidate.yaml"
    cfg.config_path = tmp_path / "config.yaml"
    cfg.candidate_path.write_text(yaml.safe_dump(profile.model_dump()))
    cfg.config_path.write_text(yaml.safe_dump({"analyst": cfg.analyst.model_dump()}))
    monkeypatch.setattr(cli, "Settings", lambda: cfg)
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output))
    calls = []
    unsupported = profile.facts[0].model_copy(update={"evidence": "invented evidence"})
    monkeypatch.setattr(
        resume,
        "ask_json",
        lambda *a: (
            calls.append(a)
            or resume.ExtractedProfile(
                facts=[*profile.facts, unsupported], specialties=[], uncertainties=[]
            )
        ),
    )
    target = tmp_path / "extracted.yaml"
    assert cli.main(["extract-profile", "--output", str(target)]) == 0
    extracted = load_profile(target)
    assert not extracted.reviewed and extracted.resume_hash == profile.resume_hash
    assert "dropped 1 unsupported facts" in output.getvalue()
    assert cli.main(["extract-profile", "--output", str(target)]) == 1
    assert cli.main(["extract-profile", "--output", str(target), "--force"]) == 0
    assert len(calls) == 1


def test_weights_and_matching_limits_are_validated():
    with pytest.raises(ValidationError, match="sum to 100"):
        Weights(technical=90)
    for values in ({"parallel": 0}, {"batch_size": 0}, {"min_score": 101}):
        with pytest.raises(ValidationError):
            Analyst.model_validate(values)


@pytest.mark.live
def test_live_synthetic_scoring(request, tmp_path):
    if not request.config.getoption("--live") or os.environ.get("BIO_LIVE_SCORING") != "1":
        pytest.skip("Set BIO_LIVE_SCORING=1 and pass --live -m live to spend a scoring request")
    from app.models.posting import Posting
    from app.settings import ROOT

    cfg = Settings(
        _env_file=None,
        resume_path=tmp_path / "resume.md",
        env_path=tmp_path / ".env",
        llm_state_dir=tmp_path,
        analyst=Analyst(
            backend=os.environ.get("BIO_LIVE_BACKEND", "codex"), timeout=120, max_tokens=6000
        ),
    )
    profile = load_profile(ROOT / "config/candidate.example.yaml")
    cfg.resume_path.write_text("Developed cell assays.")
    profile.reviewed = True
    profile.resume_hash = resume.digest(resume.resume_text(cfg))
    profile.facts = [
        Fact(category="technical", fact="Developed cell assays", evidence="Developed cell assays.")
    ]
    job = Posting(
        external_id="synthetic",
        title="Principal Scientist",
        employer="Synthetic Bio",
        location="London",
        description="Cell assay development. PhD required. Salary unspecified.",
        source_url="https://example.com/job",
        apply_url="https://example.com/job",
    )
    result = analyst.assess(cfg, profile, [{"id": 1, **job.model_dump()}])[1]
    assert 0 <= result.score <= 100
    assert result.uncertainty_flags
    print(f"Live scoring backend: {cfg.analyst.backend}; score={result.score}")


def test_extract_normalizes_evidence_and_drops_unsupported(matching, conn, monkeypatch):
    cfg, profile = matching
    cfg.resume_path.write_text("# Experience\n- Built **“cell culture”**\n  workflows for teams.\n")
    data = resume.ExtractedProfile.model_validate(
        {
            "facts": [
                {
                    "category": "technical",
                    "fact": "Built workflows",
                    "evidence": 'Built "cell culture" workflows for teams',
                },
                {"category": "leadership", "fact": "Won prize", "evidence": "Won a prize"},
            ],
            "specialties": [],
            "uncertainties": [],
        }
    )
    monkeypatch.setattr(resume, "ask_json", lambda *args: data)
    result = resume.extract_profile(cfg, conn, profile)
    assert len(result.facts) == 1
    assert result.facts[0].evidence == "Built **“cell culture”**\n  workflows for teams"
    assert result.facts[0].evidence in cfg.resume_path.read_text()
    assert result.uncertainties == ["Dropped unsupported fact: leadership"]
    assert resume.extract_profile(cfg, conn, profile) == result

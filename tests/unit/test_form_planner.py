import json
from pathlib import Path

import pytest

from app.applier import FormApplier, FormStop, ProposedAnswer, ProposedAnswers, greenhouse_questions
from app.settings import Apply, Settings


def field(label, *, required=True, options=None, kind="text"):
    return {
        "token": label,
        "label": label,
        "required": required,
        "options": options or [],
        "kind": kind,
        "identity": "",
        "disabled": False,
    }


def test_greenhouse_questions_fixture():
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/greenhouse_questions.json").read_text()
    )
    fields = greenhouse_questions(payload)
    assert [f.id for f in fields] == [
        "first_name",
        "resume",
        "question_1",
        "question_2",
        "location",
    ]
    assert fields[2].options == ["Yes", "No"]
    assert fields[3].type == "multi_value_multi_select"


def test_shared_planner_sensitive_and_batched_llm(monkeypatch):
    cfg = Settings(_env_file=None, apply=Apply(mode="assisted"))
    applier = FormApplier(None, cfg)
    calls = []
    queued = []

    def llm(_cfg, _system, user, _model, purpose):
        calls.append((json.loads(user), purpose))
        return ProposedAnswers(
            answers=[
                ProposedAnswer(id="Describe assays", answer=["Cell assays"], needs_human=False),
                ProposedAnswer(id="Unknown", needs_human=True),
            ]
        )

    monkeypatch.setattr("app.llm.ask_json", llm)
    fields = [
        field("Right to work?", options=["Yes", "No"], kind="select"),
        field("Gender", required=False),
        field("Describe assays"),
        field("Unknown"),
    ]
    context = {"resume": "Cell assays", "facts": []}
    with pytest.raises(FormStop, match="Unknown required question: Unknown"):
        applier.plan(
            fields, {}, {"right to work": "Yes"}, "Example", lambda *x: queued.append(x), context
        )
    assert len(calls) == 1 and calls[0][1] == "answers"
    assert [q["label"] for q in calls[0][0]["questions"]] == ["Describe assays", "Unknown"]
    assert queued == [("Unknown", [], True)]
    steps = applier.plan(
        fields[:3], {}, {"right to work": "Yes"}, "Example", lambda *x: None, context
    )
    assert [(s.field["label"], s.source) for s in steps] == [
        ("Right to work?", "answers.yaml (approved snapshot)"),
        ("Describe assays", "llm"),
    ]
    cfg.apply.mode = "auto"
    with pytest.raises(FormStop, match="LLM answer requires review"):
        applier.plan(fields[:3], {}, {"right to work": "Yes"}, "Example", lambda *x: None, context)
    cfg.apply.auto_llm_answers = True
    assert (
        len(
            applier.plan(
                fields[:3], {}, {"right to work": "Yes"}, "Example", lambda *x: None, context
            )
        )
        == 2
    )
    with pytest.raises(FormStop, match="Human declaration"):
        applier.plan(fields[:1], {}, {}, "Example", lambda *x: None, context)


def test_greenhouse_form_url_fetches_questions_with_public_client(monkeypatch):
    from app.applier import GreenhouseApplier

    urls = []

    def get_json(_self, url):
        urls.append(url)
        return {"questions": []}

    monkeypatch.setattr("app.applier.PublicClient.get_json", get_json)
    applier = GreenhouseApplier(None, Settings(_env_file=None))
    assert applier.form_url("https://job-boards.greenhouse.io/example/jobs/123") == (
        "https://job-boards.greenhouse.io/embed/job_app?for=example&token=123"
    )
    assert urls == ["https://boards-api.greenhouse.io/v1/boards/example/jobs/123?questions=true"]


def test_single_option_consent_requires_opt_in():
    cfg = Settings(_env_file=None, apply=Apply(mode="assisted"))
    applier = FormApplier(None, cfg)
    consent = field("I consent to updates", options=["Yes"], kind="choices")
    with pytest.raises(FormStop, match="Unknown required question"):
        applier.plan([consent], {}, {}, "Example", lambda *x: None)
    cfg.apply.auto_consent = True
    assert applier.plan([consent], {}, {}, "Example", lambda *x: None)[0].source == "consent"


def test_existing_bank_keys_match_api_labels():
    from app import answers

    bank = {
        answers.norm("Country*"): "UK",
        answers.norm("  Phone  "): "+44 0000 000000",
        answers.norm(
            "In which of our office locations would you prefer to be based? (Note that certain jobs may only be available in one location - please check the details before applying)*"
        ): "London",
        answers.norm("Where did you hear about Iso?*"): ["LinkedIn", "Other"],
    }
    assert answers.lookup(bank, "Example", "country") == "UK"
    assert answers.lookup(bank, "Example", "Phone*") == "+44 0000 000000"
    assert (
        answers.lookup(
            bank,
            "Example",
            "In which of our office locations would you prefer to be based? (Note that certain jobs may only be available in one location - please check the details before applying)",
        )
        == "London"
    )
    assert answers.lookup(bank, "Example", "where did you hear about iso?") == ["LinkedIn", "Other"]


def test_stored_answer_that_does_not_match_options_is_not_overridden(monkeypatch):
    cfg = Settings(_env_file=None, apply=Apply(mode="assisted"))
    applier = FormApplier(None, cfg)
    calls = []
    monkeypatch.setattr("app.llm.ask_json", lambda *args: calls.append(args))
    with pytest.raises(FormStop, match="Stored answer does not match options"):
        applier.plan(
            [field("Office", kind="combo", options=["London and Oxford", "London and Cambridge"])],
            {},
            {"office": "London"},
            "Example",
            lambda *args: None,
            {"resume": "", "facts": []},
        )
    assert not calls

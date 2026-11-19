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


def test_raw_bank_and_normalized_option_matches():
    from app import answers

    cfg = Settings(_env_file=None, apply=Apply(mode="assisted"))
    events = []
    applier = FormApplier(None, cfg, lambda action, **data: events.append((action, data)))
    steps = applier.plan(
        [field("Preferred office?", kind="select", options=["London Office", "Oxford"])],
        {},
        {"Preferred office?*": "  LONDON   OFFICE "},
        "Example",
        lambda *x: None,
    )
    assert steps[0].values == ["London Office"]
    assert (
        answers.lookup({"Preferred office?*": "London"}, "Example", "Preferred office?") == "London"
    )
    assert events[0][1]["source"].startswith("answers.yaml")


def test_plan_explains_blocked_fields():
    cfg = Settings(_env_file=None, apply=Apply(mode="auto"))
    events = []
    applier = FormApplier(None, cfg, lambda action, **data: events.append(data))
    with pytest.raises(FormStop):
        applier.plan(
            [field("Visa?"), field("Office", kind="select", options=["London"])],
            {},
            {"office": "Oxford"},
            "Example",
            lambda *x: None,
        )
    assert [e["reason"] for e in events] == [
        "sensitive with no stored answer",
        "option not matched: 'Oxford' is not a listed option",
    ]
    assert [row["source"] for row in applier.plan_summary] == ["needs_human", "needs_human"]


OFFICES = [
    "I'm already based in the UK and I'm happy to work in London",
    "I would be willing to relocate and work in Oxford (UK)",
    "I would be willing to relocate to any location",
    "Other (please explain)",
]


def test_match_options_normalised_and_unique_loose():
    from app.answers import match_options

    assert match_options(OFFICES, ["  i would be WILLING to relocate to any location. "]) == (
        [OFFICES[2]],
        None,
    )
    # Loose matching is opt-in and only accepts a single option containing the answer.
    assert match_options(OFFICES, ["London"])[0] is None
    assert match_options(OFFICES, ["London"], loose=True) == ([OFFICES[0]], None)
    assert match_options(["London office", "Oxford"], ["London"], loose=True) == (
        ["London office"],
        None,
    )
    matched, reason = match_options(["Yes", "Yes, with sponsorship", "No"], ["Yes"], loose=True)
    assert matched == ["Yes"]  # an exact label always wins
    matched, reason = match_options(OFFICES, ["relocate"], loose=True)
    assert matched is None and reason == "option not matched: 'relocate' fits 2 options"


def test_stored_preference_under_starred_label_counts():
    # Shaped like the job-6 dry run: label stored with a trailing '*', free-text choice.
    cfg = Settings(_env_file=None, apply=Apply(mode="auto"))
    events = []
    applier = FormApplier(None, cfg, lambda action, **data: events.append(data))
    label = "In which of our office locations would you prefer to be based? (Note: check)"
    steps = applier.plan(
        [field(label, kind="select", options=OFFICES)],
        {},
        {label + "*": "happy to work in London"},
        "Example",
        lambda *x: None,
    )
    assert steps[0].values == [OFFICES[0]]
    assert events[-1]["source"] == "answers.yaml (approved snapshot)"


def test_ambiguous_stored_preference_explains_why():
    cfg = Settings(_env_file=None, apply=Apply(mode="auto"))
    events = []
    applier = FormApplier(None, cfg, lambda action, **data: events.append(data))
    with pytest.raises(FormStop, match="does not match options"):
        applier.plan(
            [field("Preferred office", kind="select", options=OFFICES)],
            {},
            {"Preferred office*": "relocate"},
            "Example",
            lambda *x: None,
        )
    assert events[-1]["reason"] == "option not matched: 'relocate' fits 2 options"


def test_sensitive_answers_need_exact_option():
    cfg = Settings(_env_file=None, apply=Apply(mode="auto"))
    events = []
    applier = FormApplier(None, cfg, lambda action, **data: events.append(data))
    options = ["Yes, I need sponsorship", "No"]
    with pytest.raises(FormStop):
        applier.plan(
            [field("Do you need visa sponsorship?", kind="select", options=options)],
            {},
            {"Do you need visa sponsorship?": "sponsorship"},
            "Example",
            lambda *x: None,
        )
    assert events[-1]["reason"] == "option not matched: 'sponsorship' is not a listed option"


def test_lever_required_marker_is_normalised():
    from app.answers import norm

    assert norm("Which assays have you run?\u2731") == norm("which assays have you run")


def test_place_parts_fold_country_names_and_codes():
    from app.applier import place_parts

    wanted = place_parts("London, United Kingdom")
    assert wanted <= place_parts("London, Greater London, England, GBR")
    assert not wanted <= place_parts("London, ON, CAN")
    assert place_parts("Rio de Janeiro, Brazil") == {"brazil"}

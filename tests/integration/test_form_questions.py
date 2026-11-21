"""Application form questions are read and asked at preparation, before any browser opens."""

import json
from pathlib import Path

import httpx
import pytest
import test_materials
import yaml
from test_materials import mock_letters

from app import answers, applications, forms, materials
from app.applier import FormApplier, FormStop
from app.contact import contact_values, country_from_place, resume_phones, resume_places
from app.prepare import prepare_answers, prepare_materials
from app.settings import Apply, Settings
from app.sources.base import PublicClient

FIXTURES = Path(__file__).parents[1] / "fixtures/form_api"
READ = forms.read_form  # conftest replaces forms.read_form with an offline reader per test
preparation = test_materials.preparation  # shared pytest fixture
GREENHOUSE = "https://job-boards.greenhouse.io/example/jobs/4000001"
LEVER = "https://jobs.lever.co/example/00000000-0000-4000-8000-000000000001/apply"
WORKABLE = "https://apply.workable.com/j/ABC123DEF4/apply"
ASHBY = "https://jobs.ashbyhq.com/example/00000000-0000-4000-8000-000000000002"


def recorded(requests, *, robots="User-agent: *\nDisallow: /embed/\n", status=200):
    def handler(request):
        requests.append((request.method, str(request.url)))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if status != 200:
            return httpx.Response(status)
        host = request.url.host
        if host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=json.loads((FIXTURES / "greenhouse.json").read_text()))
        if host == "jobs.lever.co":
            return httpx.Response(200, text=(FIXTURES / "lever.html").read_text())
        if host == "apply.workable.com":
            return httpx.Response(200, json=json.loads((FIXTURES / "workable.json").read_text()))
        return httpx.Response(404)

    def read(cfg, url, client=None):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        return READ(cfg, url, PublicClient(http, cfg.user_agent, pause=lambda _: None))

    return read


def labels(fields):
    return [(f["label"], f["required"], f["options"]) for f in fields]


def test_reads_greenhouse_lever_and_workable_forms_with_get_only():
    cfg = Settings(_env_file=None)
    requests = []
    read = recorded(requests)

    greenhouse = read(cfg, GREENHOUSE)
    assert requests[:2] == [
        ("GET", "https://boards-api.greenhouse.io/robots.txt"),
        ("GET", "https://boards-api.greenhouse.io/v1/boards/example/jobs/4000001?questions=true"),
    ]
    assert [f["label"] for f in greenhouse] == [
        "First Name",
        "Last Name",
        "Email",
        "Phone",
        "Resume/CV",
        "Cover Letter",
        "Example statement about interview tools",
        "Which of our offices would you prefer?",
        "What are your salary expectations?",
        "Gender",
        "LinkedIn Profile",
        "Website",
        "Country",
    ]
    assert greenhouse[7]["options"] == ["Example City", "Other City"]
    assert greenhouse[6]["required"] and greenhouse[4]["kind"] == "file"

    lever = read(cfg, LEVER)
    assert ("GET", LEVER) in requests
    assert labels(lever) == [
        ("Resume/CV", False, []),
        ("Full name", True, []),
        ("Email", True, []),
        ("Phone", False, []),
        ("Current location", False, []),
        ("LinkedIn URL", False, []),
        ("Describe your synthetic assay experience.", True, []),
        ("Are you able to work on site?", False, ["Yes", "No"]),
        ("Preferred start month", False, ["January", "February"]),
    ]

    workable = read(cfg, WORKABLE)
    assert ("GET", "https://apply.workable.com/api/v1/jobs/ABC123DEF4/form") in requests
    assert labels(workable) == [
        ("First name", True, []),
        ("Last name", True, []),
        ("Email", True, []),
        ("Headline", False, []),
        ("Do you have the right to work in the example country?", True, ["Yes", "No"]),
        ("Phone", False, []),
        ("Address", False, []),
        ("Resume", True, []),
        ("Cover letter", True, []),
        ("What is your current notice period?", True, []),
        ("Are you willing to relocate?", False, ["Yes", "No"]),
    ]
    assert {method for method, _ in requests} == {"GET"}


@pytest.mark.parametrize(
    "url,kwargs,reason",
    [
        (ASHBY, {}, "robots.txt disallows"),
        ("https://careers.example.org/jobs/1", {}, "unsupported application site"),
        (GREENHOUSE, {"status": 500}, "HTTP 500"),
        (GREENHOUSE, {"robots": "User-agent: *\nDisallow: /\n"}, "robots.txt disallows"),
        ("https://job-boards.greenhouse.io/example", {}, "Cannot identify Greenhouse"),
    ],
)
def test_unreadable_forms_give_a_plain_reason(url, kwargs, reason):
    requests = []
    with pytest.raises(forms.FormUnavailable, match=reason):
        recorded(requests, **kwargs)(Settings(_env_file=None), url)
    if url == ASHBY:
        assert not requests  # nothing is requested from a path robots.txt disallows


def test_questions_skip_identity_fields_the_profile_fills(profile):
    fields = recorded([])(Settings(_env_file=None), GREENHOUSE)
    profile.name = "Alex Example"
    values = contact_values(profile, "alex@example.org")
    asked = [f["label"] for f in forms.questions(fields, values)]
    # No phone, LinkedIn, website or country is known, so those are asked; Gender is optional EEO.
    assert asked == [
        "Phone",
        "Example statement about interview tools",
        "Which of our offices would you prefer?",
        "What are your salary expectations?",
        "LinkedIn Profile",
        "Website",
        "Country",
    ]
    profile.phone = "+44 20 7946 0000"
    profile.linkedin = "https://www.linkedin.com/in/alex-example"
    profile.city = "Example City, United Kingdom"
    asked = [f["label"] for f in forms.questions(fields, contact_values(profile, ""))]
    assert "Phone" not in asked and "LinkedIn Profile" not in asked and "Country" not in asked
    assert "Website" in asked


def test_contact_values_come_from_the_profile_and_resume_only(profile):
    cv = (
        "# Alex Example\nExample City, UK\n+44 20 7946 0000 · alex@example.org · "
        "linkedin.com/in/alex-example\n\n## Experience\n2019 - 2023 Assay development\n"
        "Reference 0123 4567 8 in a later line\n"
    )
    assert resume_phones(cv) == ["+44 20 7946 0000"]
    profile.name = "Alex Example"
    values = contact_values(profile, cv)
    assert values["phone"] == "+44 20 7946 0000"
    assert values["email"] == "alex@example.org"
    assert values["linkedin"] == "linkedin.com/in/alex-example"
    assert values["website"] == ""
    # A stated "City, Country" place gives the city and country; none stated, none used.
    assert resume_places(cv) == ["Example City, UK"]
    assert values["city"] == "Example City, UK" and values["country"] == "United Kingdom"
    unplaced = contact_values(profile, cv.replace("Example City, UK\n", ""))
    assert unplaced["city"] == "" and unplaced["country"] == ""
    assert (
        contact_values(profile, "# Alex Example\nLocation: Example City, Ireland\n")["country"]
        == "Ireland"
    )
    two_places = cv.replace("Example City, UK", "Example City, UK · Other City, France")
    assert contact_values(profile, two_places)["country"] == ""  # ambiguous: never pick one
    assert resume_places("# Alex Example, PhD\nalex@example.org\n") == []
    two = cv.replace("· alex", "· 020 7946 0001 · alex")
    assert contact_values(profile, two)["phone"] == ""  # ambiguous: never pick one
    profile.phone = "+44 1632 960000"
    assert contact_values(profile, two)["phone"] == "+44 1632 960000"
    assert country_from_place("Example City, UK") == "United Kingdom"
    assert country_from_place("Example City") == ""


def test_planner_never_asks_the_llm_for_contact_details(monkeypatch):
    applier = FormApplier(None, Settings(_env_file=None, apply=Apply(mode="assisted")))
    monkeypatch.setattr("app.llm.ask_json", lambda *a: pytest.fail("LLM asked for contact"))

    def field(label, **extra):
        base = {"token": label, "label": label, "required": True, "options": [], "kind": "text"}
        return {**base, "identity": "", "disabled": False, **extra}

    queued = []
    context = {"resume": "Phone available on request", "facts": []}
    with pytest.raises(FormStop, match="Missing contact detail: Phone"):
        applier.plan([field("Phone")], {}, {}, "Example", lambda *x: queued.append(x), context)
    assert queued == [("Phone", [], True)]
    country = field("Country", options=["France", "United Kingdom"], kind="choices")
    steps = applier.plan(
        [country], {"country": "United Kingdom"}, {}, "Example", lambda *x: None, context
    )
    assert [(s.values, s.source) for s in steps] == [(["United Kingdom"], "country")]
    steps = applier.plan(
        [country], {}, {answers.norm("Country"): "France"}, "Example", lambda *x: None, context
    )
    assert [(s.values, s.source) for s in steps] == [
        (["France"], "answers.yaml (approved snapshot)")
    ]


def prepare(cfg, conn, profile, row, read, *, interactive, replies=(), said=None):
    replies = iter(replies)
    prompts = []

    def reply(prompt):
        prompts.append(prompt)
        return next(replies)

    said = [] if said is None else said
    result = prepare_materials(cfg, conn, profile, row, output=said.append)
    import builtins

    original = builtins.input
    builtins.input = reply
    try:
        prepare_answers(
            cfg,
            conn,
            profile,
            row,
            result,
            interactive=interactive,
            output=said.append,
            read_form=read,
        )
    finally:
        builtins.input = original
    return result, prompts, said


def test_prepare_asks_form_questions_up_front_and_reuses_answers(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    mock_letters(monkeypatch, [])
    profile.phone = "+44 20 7946 0000"
    profile.linkedin = "https://www.linkedin.com/in/alex-example"
    profile.city = "Example City, United Kingdom"
    first, second = rows[0], rows[1]
    first["apply_url"] = GREENHOUSE
    second["apply_url"] = GREENHOUSE.replace("4000001", "4000002")
    requests = []
    read = recorded(requests)

    result, prompts, said = prepare(
        cfg,
        conn,
        profile,
        first,
        read,
        interactive=True,
        replies=["1", "1", "Example range", ""],
    )
    assert any("Read the Greenhouse application form: 4 questions" in s for s in said)
    assert any("[declaration or sensitive: only you answer this]" in s for s in said)
    assert any("1. Example City" in s for s in said)
    assert len(prompts) == 4 and prompts[2] == "Answer (Enter skips): "
    assert result.form_read and "answers" in result.created
    stored = [
        r["label"]
        for r in conn.execute(
            "SELECT label FROM screening_questions WHERE job_id=? ORDER BY id", (first["id"],)
        )
    ]
    assert stored == [
        "Example statement about interview tools",
        "Which of our offices would you prefer?",
        "What are your salary expectations?",
        "Website",
    ]
    bank = yaml.safe_load(Path(cfg.apply.answers_file).read_text())
    assert bank == {
        "Example statement about interview tools": [
            "I confirm I have read and agree to these terms."
        ],
        "What are your salary expectations?": "Example range",
        "Which of our offices would you prefer?": ["Example City"],
    }
    draft = yaml.safe_load(materials.latest(conn, first["id"], "answers")["content"])["items"]
    assert [(i["question"], i["source"]) for i in draft] == [
        ("Example statement about interview tools", "answers.yaml"),
        ("Which of our offices would you prefer?", "answers.yaml"),
        ("What are your salary expectations?", "answers.yaml"),
        ("Website", "needs_human"),
    ]
    assert not json.loads(materials.latest(conn, first["id"], "answers")["warnings"])
    assert applications.approve(cfg, conn, profile, first)

    # The same form for another job asks only what the bank cannot answer.
    _, prompts, _ = prepare(cfg, conn, profile, second, read, interactive=True, replies=[""])
    assert len(prompts) == 1
    draft = yaml.safe_load(materials.latest(conn, second["id"], "answers")["content"])["items"]
    assert [i["source"] for i in draft] == ["answers.yaml"] * 3 + ["needs_human"]
    assert {method for method, _ in requests} == {"GET"}


def test_unanswered_required_form_questions_block_silent_approval(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    mock_letters(monkeypatch, [])
    row = rows[0]
    row["apply_url"] = GREENHOUSE
    result, prompts, _ = prepare(cfg, conn, profile, row, recorded([]), interactive=False)
    assert not prompts
    assert {i["question"] for i in result.unanswered if i["required"]} == {
        "Example statement about interview tools",
        "Which of our offices would you prefer?",
        "Country",
    }
    with pytest.raises(applications.UnresolvedWarnings) as caught:
        applications.approve(cfg, conn, profile, row)
    assert "Unanswered: Which of our offices would you prefer?" in caught.value.warnings["answers"]


def test_prepare_continues_when_the_form_cannot_be_read(preparation, conn, monkeypatch):
    cfg, profile, rows = preparation
    mock_letters(monkeypatch, [])
    row = rows[0]
    row["apply_url"] = ASHBY
    result, prompts, said = prepare(cfg, conn, profile, row, recorded([]), interactive=True)
    assert not prompts and not result.form_read and not result.problems
    message = next(s for s in said if s.startswith("Could not read the application form"))
    assert "robots.txt disallows" in message
    assert f"bio-jobs answers --job {row['id']}" in message
    assert "answers" in result.created
    assert not conn.execute(
        "SELECT 1 FROM screening_questions WHERE job_id=?", (row["id"],)
    ).fetchone()


CONTACT_RESUME = (
    "# Alex Example\n+44 20 7946 0000 · alex@example.org · linkedin.com/in/alex-example\n\n"
    "## Experience\nDeveloped cell assays.\n\n## Skills\nPython analysis.\n\n"
    "## Education\nBSc at Example University.\n"
)


def store_stale(conn, row):
    """Identity rows a browser fill stored before the resume's contact details were read."""
    for label, required in (
        ("Phone", True),
        ("Country*", True),
        ("LinkedIn Profile", False),
        ("Website", False),
        ("What is your current notice period?", False),
    ):
        answers.add_question(conn, row["id"], label, [], required)


def test_stored_identity_questions_the_resume_answers_are_not_asked(preparation, conn):
    cfg, profile, rows = preparation
    row = rows[0]
    cfg.resume_path.write_text(CONTACT_RESUME)
    store_stale(conn, row)
    answers.save_answer(cfg, "Phone", "+44 1632 960000")  # the user's answer is kept as it is
    asked = [i["question"] for i in answers.plan(cfg, conn, row, profile)]
    # No profile or resume place states a country, so Country is still asked.
    assert asked == ["Country*", "Website", "What is your current notice period?"]
    said = []
    answers.ask_missing(cfg, conn, row, profile=profile, output=said.append)
    assert not any("Phone" in s or "LinkedIn" in s for s in said)
    assert any("Country*" in s for s in said)

    cfg.resume_path.write_text(CONTACT_RESUME.replace("\n+44", "\nExample City, UK\n+44"))
    asked = [i["question"] for i in answers.plan(cfg, conn, row, profile)]
    assert asked == ["Website", "What is your current notice period?"]
    profile.website = "https://alex.example.org"
    assert [i["question"] for i in answers.plan(cfg, conn, row, profile)] == [
        "What is your current notice period?"
    ]
    # The stored rows and the user's stored answer are never deleted.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM screening_questions WHERE job_id=?", (row["id"],)
        ).fetchone()[0]
        == 5
    )
    assert yaml.safe_load(Path(cfg.apply.answers_file).read_text()) == {"Phone": "+44 1632 960000"}
    # Without a profile nothing is filtered (the identity values are unknown).
    assert len(answers.plan(cfg, conn, row)) == 5


def test_prepare_skips_stale_identity_questions_and_reads_phone_from_resume(
    preparation, conn, monkeypatch
):
    cfg, profile, rows = preparation
    mock_letters(monkeypatch, [])
    cfg.resume_path.write_text(CONTACT_RESUME)
    profile.resume_hash = resume_digest(cfg)
    row = rows[0]
    row["apply_url"] = GREENHOUSE
    store_stale(conn, row)
    result, _, said = prepare(
        cfg, conn, profile, row, recorded([]), interactive=True, replies=[""] * 10
    )
    labels_asked = [s for s in said if s.startswith(f"{row['id']} ")]
    assert not any(": Phone" in s or ": LinkedIn Profile" in s for s in labels_asked)
    assert any(s.endswith(": Country*") for s in labels_asked)
    stored = {
        r["label"]
        for r in conn.execute("SELECT label FROM screening_questions WHERE job_id=?", (row["id"],))
    }
    # The form's phone is filled from the resume, so it is not registered again; the
    # synthetic Greenhouse Country selector is, because nothing states a country.
    assert "Country" in stored
    assert {i["question"] for i in result.unanswered} >= {"Country", "Country*"}
    assert not {"Phone", "LinkedIn Profile"} & {i["question"] for i in result.unanswered}
    draft = yaml.safe_load(materials.latest(conn, row["id"], "answers")["content"])["items"]
    assert not {"Phone", "LinkedIn Profile"} & {i["question"] for i in draft}


def resume_digest(cfg):
    from app import resume

    return resume.digest(resume.resume_text(cfg))

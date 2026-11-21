"""Read an application form's questions before any browser opens.

GET requests only, through the same robots-checking, paced client as discovery; nothing
is posted or submitted. A form that cannot be read is reported, and the browser fill
still reads the live form as before.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from app import answers
from app.applier import (
    DEMOGRAPHIC,
    Field,
    FormStop,
    append_path,
    greenhouse_ids,
    greenhouse_questions,
    standard_key,
)
from app.sources.base import AccessRestricted, PublicClient, RateLimited

ATS_HOSTS = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("workable.com", "workable"),
    ("ashbyhq.com", "ashby"),
)


class FormUnavailable(RuntimeError):
    pass


def ats_for(url):
    host = urlsplit(url).hostname or ""
    for domain, ats in ATS_HOSTS:
        if host == domain or host.endswith("." + domain):
            return ats
    return None


def _kind(ftype, options):
    if ftype == "input_file":
        return "file"
    if ftype.startswith("multi_value") or options:
        return "choices"
    return "text"


def _field(id, label, required, ftype, options=(), standard=None):
    options = [" ".join(o.split()) for o in options if o and o.strip()]
    field = Field(
        id, " ".join(label.split()), bool(required), ftype, options, kind=_kind(ftype, options)
    )
    field["identity"] = id
    if standard:
        field["standard"] = standard
    return field


def greenhouse_form(client, url):
    board, job = greenhouse_ids(url)
    payload = client.get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"
    )
    fields = [
        _field(f.id, f.label, f.required, f.type, f.options) for f in greenhouse_questions(payload)
    ]
    for field in fields:
        if field["id"] == "location":
            field["standard"] = "city"
    if any(standard_key(f) == "phone" for f in fields):
        # The hosted form pairs its phone field with a required Country selector the API omits.
        fields.append(_field("country", "Country", True, "input_text", standard="country"))
    return fields


class _LeverForm(HTMLParser):
    """Each Lever question is an <li class="application-question"> with a label and controls."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.questions = []
        self.question = None
        self.depth = 0
        self.question_depth = 0
        self.label = 0
        self.option = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "li":
            self.depth += 1
            if "application-question" in classes:
                self.question = {"label": "", "inputs": [], "options": [], "required": False}
                self.question_depth = self.depth
            return
        if self.question is None:
            return
        if tag == "div" and ("application-label" in classes or self.label):
            self.label += 1
        elif tag == "span" and self.label and "required" in classes:
            self.question["required"] = True
        elif tag in ("input", "textarea", "select") and attrs.get("name"):
            if attrs.get("type") == "hidden":
                return
            kind = "select" if tag == "select" else attrs.get("type") or tag
            self.question["inputs"].append({"kind": kind, "name": attrs["name"]})
            if "required" in attrs:
                self.question["required"] = True
            if kind in ("radio", "checkbox") and attrs.get("value"):
                self.question["options"].append(attrs["value"])
        elif tag == "option" and attrs.get("value"):
            self.option = ""

    def handle_endtag(self, tag):
        if tag == "li":
            if self.question is not None and self.depth == self.question_depth:
                self.questions.append(self.question)
                self.question = None
            self.depth -= 1
        elif tag == "div" and self.label:
            self.label -= 1
        elif tag == "option" and self.option is not None:
            if self.option.strip() and self.question is not None:
                self.question["options"].append(self.option)
            self.option = None

    def handle_data(self, data):
        if self.question is None:
            return
        if self.label:
            self.question["label"] += data
        elif self.option is not None:
            self.option += data


LEVER_STANDARD = {
    "name": "full_name",
    "email": "email",
    "phone": "phone",
    "location": "city",
    "resume": "resume",
    "comments": "letter",
}


def lever_fields(html):
    parser = _LeverForm()
    parser.feed(html)
    fields = []
    for question in parser.questions:
        if not question["inputs"]:
            continue
        first = question["inputs"][0]
        name = first["name"]
        if name.startswith("eeo["):
            continue
        ftype = {
            "radio": "multi_value_single_select",
            "checkbox": "multi_value_multi_select",
            "select": "multi_value_single_select",
            "file": "input_file",
            "textarea": "textarea",
        }.get(first["kind"], "input_text")
        label = question["label"].replace("✱", "")
        fields.append(
            _field(
                name,
                label or name,
                question["required"],
                ftype,
                question["options"],
                LEVER_STANDARD.get(name),
            )
        )
    if not any(f.get("standard") == "email" for f in fields):
        raise FormUnavailable("Lever form has no email field; its layout may have changed")
    return fields


def lever_form(client, url):
    return lever_fields(client.get_text(append_path(url, "apply")))


WORKABLE_STANDARD = {
    "firstname": "first_name",
    "lastname": "last_name",
    "email": "email",
    "phone": "phone",
    "address": "city",
    "resume": "resume",
    "cover_letter": "letter",
}


def workable_fields(payload):
    if not isinstance(payload, list):
        raise FormUnavailable("Invalid Workable form response")
    fields = []
    for section in payload:
        for item in (section or {}).get("fields") or []:
            fid, ftype = item.get("id") or "", item.get("type") or ""
            label = item.get("label") or fid
            required = item.get("required")
            standard = WORKABLE_STANDARD.get(fid)
            if fid == "avatar" or ftype == "group":
                continue  # photo and education/experience blocks are left to the browser
            if ftype == "boolean":
                fields.append(
                    _field(fid, label, required, "multi_value_single_select", ["Yes", "No"])
                )
            elif ftype in ("dropdown", "multiple"):
                multi = ftype == "multiple" and not item.get("singleOption", True)
                options = [str(o.get("value") or "") for o in item.get("options") or []]
                kind = "multi_value_multi_select" if multi else "multi_value_single_select"
                fields.append(_field(fid, label, required, kind, options))
            elif ftype == "file":
                fields.append(_field(fid, label, required, "input_file", standard=standard))
            elif ftype == "paragraph":
                fields.append(_field(fid, label, required, "textarea", standard=standard))
            else:
                fields.append(_field(fid, label, required, "input_text", standard=standard))
    return fields


def workable_form(client, url):
    path = [p for p in urlsplit(url).path.split("/") if p]
    if "j" not in path or path.index("j") + 1 >= len(path):
        raise FormUnavailable("Cannot identify the Workable job")
    code = path[path.index("j") + 1]
    if not re.fullmatch(r"[A-Za-z0-9]+", code):
        raise FormUnavailable("Invalid Workable job identifier")
    return workable_fields(client.get_json(f"https://apply.workable.com/api/v1/jobs/{code}/form"))


def ashby_form(client, url):
    raise FormUnavailable(
        "Ashby serves its form only under jobs.ashbyhq.com/api/, which robots.txt disallows"
    )


READERS = {
    "greenhouse": greenhouse_form,
    "lever": lever_form,
    "workable": workable_form,
    "ashby": ashby_form,
}


def read_form(cfg, url, client=None):
    """The form's fields, or FormUnavailable with a plain reason."""
    ats = ats_for(url)
    if ats is None:
        raise FormUnavailable("unsupported application site")
    try:
        if client is not None:
            return READERS[ats](client, url)
        with httpx.Client(timeout=cfg.http_timeout) as http:
            return READERS[ats](PublicClient(http, cfg.user_agent), url)
    except FormUnavailable:
        raise
    except (AccessRestricted, RateLimited, FormStop) as exc:
        raise FormUnavailable(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise FormUnavailable(f"HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise FormUnavailable(f"network error ({type(exc).__name__})") from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FormUnavailable(f"unexpected form response ({type(exc).__name__})") from exc


def questions(fields, values):
    """Form fields a person must answer: identity fields the profile or resume fills are skipped."""
    result = []
    for field in fields:
        key = standard_key(field)
        if field["kind"] == "file" or key in ("resume", "letter"):
            continue
        if DEMOGRAPHIC.search(field["label"]) and not field["required"]:
            continue  # optional equal-opportunity surveys are never filled
        if key and values.get(key):
            continue
        result.append(field)
    return result


def store(conn, row, fields):
    for field in fields:
        answers.add_question(conn, row["id"], field["label"], field["options"], field["required"])


def refresh(cfg, conn, profile, row, *, cv="", output=print, read=read_form):
    """Read the form, store its questions and return them; None when the form cannot be read."""
    from app.contact import contact_values

    try:
        fields = read(cfg, row["apply_url"])
    except FormUnavailable as exc:
        output(
            f"Could not read the application form before the browser: {exc}. "
            "The browser fill will read it; answer anything new with "
            f"uv run bio-jobs answers --job {row['id']}."
        )
        return None
    found = questions(fields, contact_values(profile, cv))
    store(conn, row, found)
    output(
        f"Read the {ats_for(row['apply_url']).title()} application form: "
        f"{len(found)} question{'s' if len(found) != 1 else ''} beyond the profile's contact details."
    )
    return found

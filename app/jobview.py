"""How a job reads in the terminal: the compact ``list`` table, the review card and ``show``.

``list`` is an overview of one line per job; the review card fits about one screen; ``show``
prints the whole scoring explanation for one job. Evidence, gaps, must-haves and uncertainties
are short bullet lists in which a statement appears once, however many of the evaluation's
fields repeat it. Job text is untrusted and goes through ``app.console.text``.
"""

from __future__ import annotations

import re

from rich.table import Table
from rich.text import Text

from app.console import clean, console, text

APPLICATION_SHORT = {
    "awaiting_review": "to review",
    "manual_required": "manual",
    "unknown_result": "unknown",
}
CARD_ITEMS = 3


def flat(value) -> str:
    """Untrusted text on one line: escapes removed, whitespace collapsed."""
    return " ".join(clean(value).split())


def paragraphs(value) -> list[str]:
    """Job text as paragraphs, whitespace collapsed inside each."""
    return [flat(part) for part in re.split(r"\n\s*\n", clean(value)) if part.strip()]


def _key(value) -> str:
    return re.sub(r"[^0-9a-z]+", " ", flat(value).casefold()).strip()


def unique(items, seen: set[str]) -> list[str]:
    """Items not already in ``seen`` (case and punctuation ignored); adds them to it."""
    result = []
    for item in items:
        key = _key(item)
        if key and key not in seen:
            seen.add(key)
            result.append(flat(item))
    return result


def posted(row) -> str:
    """YYYY-MM-DD from the date label, with short stale/closed tags."""
    label = row.get("date_label") or ""
    found = re.search(r"\d{4}-\d{2}-\d{2}", label)
    value = found.group(0) if found else "unknown"
    if found and label.startswith("first seen"):
        value = "seen " + value
    tags = [tag for tag in ("stale", "closed") if f"[{tag}]" in label]
    return " ".join([value, *tags])


def status(row) -> str:
    """The application state when there is one (it implies the job was kept), else the review."""
    state = row.get("application_state")
    result = (
        APPLICATION_SHORT.get(state, state.replace("_", " "))
        if state
        else row.get("review") or "new"
    )
    return result + (" (cd)" if row.get("company_cooldown_until") else "")


def score(row) -> str:
    if row.get("score") is None:
        return "unscored"
    override = row.get("override")
    recommendation = (override or row.get("evaluation") or {}).get("recommendation", "")
    return f"{row['score']} {recommendation}".strip() + ("*" if override else "")


def confidence(row) -> str:
    return (row.get("evaluation") or {}).get("confidence") or "-"


def keyword_cell(row) -> str:
    if "keywords" not in row:
        return "-"
    if not row.get("keywords_resume"):
        return "no cv"
    return f"{row['keywords']['score']}%" if row.get("keywords") else "-"


def location(row) -> str:
    places = row.get("locations") or [row.get("location") or "unknown"]
    return flat(places[0]) + (f" +{len(places) - 1}" if len(places) > 1 else "")


TEXT_FLOORS = {"Title": 14, "Employer": 8, "Location": 8}
TEXT_SHARES = {"Title": 0.5, "Employer": 0.28, "Location": 0.22}
ORDER = ("#", "ID", "Title", "Employer", "Location", "Posted", "Status", "Score", "Conf")
ORDER += ("KW", "Link")
# Dropped in this order when the terminal is too narrow for them.
OPTIONAL = ("Conf", "Link", "#")


def table(rows, width: int) -> Table:
    """One line per job at any width: short columns sized to content, text columns share the rest.

    Columns are one space apart; cells never wrap and end in an ellipsis when cut.
    """
    cells = {
        "#": [str(rank) for rank in range(1, len(rows) + 1)],
        "ID": [str(row["id"]) for row in rows],
        "Title": [flat(row["title"]) for row in rows],
        "Employer": [flat(row["employer"]) for row in rows],
        "Location": [location(row) for row in rows],
        "Posted": [posted(row) for row in rows],
        "Status": [status(row) for row in rows],
        "Score": [score(row) for row in rows],
        "Conf": [confidence(row) for row in rows],
        "KW": [keyword_cell(row) for row in rows],
        "Link": ["open" for _ in rows],
    }
    longest = {name: max([len(name), *map(len, values)]) for name, values in cells.items()}
    order = list(ORDER)

    def room():
        short = sum(longest[name] for name in order if name not in TEXT_FLOORS)
        return width - short - (len(order) - 1)

    for name in OPTIONAL:
        if room() >= sum(TEXT_FLOORS.values()):
            break
        order.remove(name)
    available = max(room(), 3 * len(TEXT_FLOORS))
    # Below the floors (a very narrow terminal) the text columns share what is left.
    scale = min(1.0, available / sum(TEXT_FLOORS.values()))
    widths = {
        name: max(int(floor * scale), min(longest[name], int(available * TEXT_SHARES[name])))
        for name, floor in TEXT_FLOORS.items()
    }
    while sum(widths.values()) > available and max(widths.values()) > 3:
        widest = max(widths, key=widths.get)
        widths[widest] -= 1
    spare = available - sum(widths.values())
    for name in TEXT_FLOORS:
        extra = max(0, min(spare, longest[name] - widths[name]))
        widths[name] += extra
        spare -= extra

    result = Table(
        title="Jobs — highest fit score first",
        box=None,
        padding=(0, 1, 0, 0),
        pad_edge=False,
        header_style="bold",
    )
    for name in order:
        result.add_column(
            name,
            justify="right" if name in ("#", "ID", "KW") else "left",
            no_wrap=True,
            overflow="ellipsis",
            width=widths.get(name, longest[name]),
        )
    for index, row in enumerate(rows):
        result.add_row(
            *(
                Text("open", style=f"link {row['apply_url']}")
                if name == "Link"
                else text(cells[name][index])
                for name in order
            )
        )
    return result


def legend(rows) -> str:
    notes = []
    if any(row.get("override") for row in rows):
        notes.append("* score set by your override")
    if any(row.get("company_cooldown_until") for row in rows):
        notes.append("(cd) company cooldown")
    return "; ".join(notes)


def sections(row) -> dict[str, list[str]]:
    """Evidence, gaps, must-haves and uncertainties, each statement once."""
    evaluation = row.get("evaluation")
    if not evaluation:
        return {}
    seen: set[str] = set()
    must_haves = []
    for requirement in evaluation["must_haves"]:
        if requirement["status"] != "met":
            name = flat(requirement["requirement"])
            if _key(name) not in seen:
                seen.add(_key(name))
                seen.add(_key(f"Must-have unknown: {name}"))
                must_haves.append(f"{name} ({requirement['status']})")
    dimension_flags = {
        _key(f"{name}: {dim['confidence']} — {dim['explanation']}")
        for name, dim in evaluation["dimensions"].items()
    }
    flags = [
        flag
        for flag in evaluation["uncertainty_flags"]
        if _key(flag) not in dimension_flags and not flag.startswith("Must-have unknown:")
    ]
    return {
        "Evidence": unique(evaluation["strengths"], seen),
        "Gaps": unique(evaluation["gaps"], seen),
        "Must-haves to check": must_haves,
        "Uncertainties": unique(flags, seen),
    }


def bullets(items, *, limit=None, more_hint="") -> Table:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=3, no_wrap=True)
    grid.add_column(overflow="fold")
    shown = items if limit is None else items[:limit]
    for item in shown:
        grid.add_row("  •", text(item))
    if len(items) > len(shown):
        grid.add_row("", text(f"+{len(items) - len(shown)} more{more_hint}", "dim"))
    return grid


def _section(title, items, *, limit=None, more_hint="", empty=None, target=None) -> None:
    target = target or console
    if not items and empty is None:
        return
    target.print(Text(title, style="bold"))
    if items:
        target.print(bullets(items, limit=limit, more_hint=more_hint))
    else:
        target.print(text(f"    {empty}", "dim"))


def heading(row, *, target=None) -> None:
    """Title, employer, location, date, status and score: the first lines of a job."""
    target = target or console
    title = Text()
    title.append(text(flat(row["title"]), "bold"))
    title.append(text(f" — {flat(row['employer'])}"))
    title.append(text(f"  #{row['id']}", "dim"))
    target.print(title)
    places = "; ".join(flat(x) for x in row.get("locations") or []) or flat(
        row.get("location") or "unknown"
    )
    state = row.get("application_state") or "unprepared"
    facts = [places, f"posted {posted(row)}", f"{row.get('review') or 'new'} / {state}"]
    if row.get("company_cooldown_until"):
        facts.append(f"company cooldown until {row['company_cooldown_until'][:10]}")
    target.print(text(" · ".join(facts)))
    line = ["unscored" if row.get("score") is None else f"Score {row['score']}/100"]
    evaluation = row.get("evaluation")
    if evaluation:
        line += [evaluation["recommendation"], f"confidence {evaluation['confidence']}"]
    line.append(keyword_line(row))
    target.print(text(" · ".join(line), "bold"))
    override = row.get("override")
    if override:
        target.print(
            text(f"User override: {override['recommendation']} — {flat(override['reason'])}")
        )
    for note in link_lines(row):
        target.print(text(note, "yellow"))
    for note in board_lines(row):
        target.print(text(note, "yellow"))
    if len(row.get("grouped_job_ids", [])) > 1:
        target.print(text(f"Grouped stored job IDs: {row['grouped_job_ids']}", "dim"))


def keyword_line(row) -> str:
    match = row.get("keywords")
    if "keywords" not in row:
        return "resume keywords not checked"
    if not row.get("keywords_resume"):
        return "resume keywords: no resume.md"
    if not match:
        return "resume keywords: none recognised"
    return f"resume keywords {match['score']}% ({len(match['present'])}/{match['total']})"


def link_lines(row) -> list[str]:
    if not row.get("added_by_link"):
        return []
    notes = row.get("filter_notes") or []
    if not notes:
        return ["Added by link."]
    return [
        "Added by link; normally excluded by: " + "; ".join(notes),
        "Kept for you to decide: save (s) or reject (r).",
    ]


def board_lines(row) -> list[str]:
    boards = row.get("board_sources") or []
    if not boards:
        return []
    where = ", ".join(boards)
    if row.get("employer_reviewed"):
        return [f"Also listed on job board {where}."]
    note = (
        f"From job board {where}: {row['employer']} is not on the reviewed watchlist;"
        " check it is a commercial life-sciences employer before applying."
    )
    return [note]


def card(row, *, target=None) -> None:
    """The review card: heading, the top evidence and gaps, must-haves once."""
    target = target or console
    heading(row, target=target)
    parts = sections(row)
    hint = " (x: scoring details)"
    for title in ("Evidence", "Gaps", "Must-haves to check", "Uncertainties"):
        _section(
            title,
            parts.get(title, []),
            limit=CARD_ITEMS,
            more_hint=hint,
            empty=(
                "none listed; confirm eligibility and conditions with the employer"
                if title == "Must-haves to check" and parts
                else None
            ),
            target=target,
        )
    target.print(text(f"Apply: {row['apply_url']}", "dim"), soft_wrap=True)


def keyword_section(row, *, target=None) -> None:
    """Informational: the LLM score, score gate and filters never use this."""
    target = target or console
    target.print(Text("Keyword match", style="bold"))
    match = row.get("keywords")
    if not row.get("keywords_resume"):
        target.print(text("    Resume keywords: no resume.md to compare", "dim"))
        return
    if not match:
        target.print(text("    Resume keywords: no recognised job keywords (informational)", "dim"))
        return
    target.print(
        text(
            f"    Resume keywords: {match['score']}% — {len(match['present'])} of "
            f"{match['total']} job keywords are in resume.md (informational; not part of the score)"
        )
    )
    for label, key in (("In resume", "present"), ("Missing", "missing")):
        if match[key]:
            target.print(bullets([f"{label}: " + ", ".join(match[key])]))


def explanation(row, *, target=None) -> None:
    """Review key x: every evidence point, gap and uncertainty, then the scoring details."""
    target = target or console
    for title, items in sections(row).items():
        _section(title, items, empty="none", target=target)
    scoring(row, target=target)
    keyword_section(row, target=target)


def scoring(row, *, target=None) -> None:
    """Per-dimension scores and the full must-have evidence."""
    target = target or console
    evaluation = row.get("evaluation")
    if not evaluation:
        target.print(text("No current evaluation: run bio-jobs analyze.", "dim"))
        return
    target.print(Text("Scoring details", style="bold"))
    target.print(
        text(
            f"    Scoring version {row.get('scoring_version')}; "
            f"reviewed profile version {row.get('profile_version')}",
            "dim",
        )
    )
    weights = row.get("scoring_weights") or {}
    items = []
    for name, dim in evaluation["dimensions"].items():
        value = "unknown" if dim["score"] is None else dim["score"]
        item = f"{name} {value} ({dim['confidence']}, weight {weights.get(name, '?')}): "
        item += flat(dim["explanation"])
        items.append(item)
    target.print(bullets(items))
    target.print(Text("Must-have evidence", style="bold"))
    target.print(
        bullets(
            [
                f"{flat(r['requirement'])} ({r['status']}); "
                f"candidate: {flat(r['candidate_evidence'] or 'unknown')}; "
                f"job: {flat(r['job_evidence'])}"
                for r in evaluation["must_haves"]
            ]
        )
    )


def description(row, *, target=None) -> None:
    target = target or console
    target.print(Text("Job description", style="bold"))
    for part in paragraphs(row.get("description")):
        target.print(text(part))
        target.print()


def detail(row, *, full=False, target=None) -> None:
    """Everything about one job's fit: ``show`` and ``list --details``."""
    target = target or console
    heading(row, target=target)
    parts = sections(row)
    if not parts:
        target.print(text("No current evaluation: run bio-jobs analyze.", "dim"))
    for title, items in parts.items():
        _section(title, items, empty="none", target=target)
    keyword_section(row, target=target)
    target.print(Text("Links", style="bold"))
    target.print(text(f"    Apply: {row['apply_url']}"), soft_wrap=True)
    for origin in row.get("origins", []):
        target.print(
            text(f"    Origin {origin['id']} ({origin['source_id']}): {origin['apply_url']}"),
            soft_wrap=True,
        )
    if full:
        scoring(row, target=target)
        description(row, target=target)

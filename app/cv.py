"""Recover CV structure from resume text and write it as clean Markdown.

The input is Markdown (``#`` name, ``##`` sections, ``-`` bullets) or plain text such as
the text extracted from a resume PDF: bare section titles, ``Employer --- City`` lines,
``Title | Month Year--present`` lines, bullets with indented continuation lines and
numbered publications. Only layout is recovered; wording is kept as written.
"""

import re
from dataclasses import dataclass, field

# Bare-line section titles must name a usual CV section; anything else stays text.
SECTION_WORDS = {
    "about",
    "accomplishments",
    "achievements",
    "activities",
    "affiliations",
    "appointments",
    "awards",
    "background",
    "career",
    "certifications",
    "certificates",
    "community",
    "competencies",
    "conferences",
    "contact",
    "courses",
    "education",
    "employment",
    "experience",
    "expertise",
    "fellowships",
    "funding",
    "grants",
    "highlights",
    "history",
    "honors",
    "honours",
    "interests",
    "languages",
    "leadership",
    "memberships",
    "mentoring",
    "objective",
    "patents",
    "presentations",
    "profile",
    "projects",
    "property",
    "publications",
    "qualifications",
    "references",
    "service",
    "skills",
    "summary",
    "supervision",
    "talks",
    "teaching",
    "training",
    "volunteering",
}
# A short line naming a job is an entry, not a section, even when it holds a section word.
ROLE_WORDS = {
    "analyst",
    "assistant",
    "associate",
    "chair",
    "consultant",
    "coordinator",
    "director",
    "engineer",
    "fellow",
    "head",
    "intern",
    "lecturer",
    "manager",
    "officer",
    "postdoc",
    "president",
    "professor",
    "researcher",
    "scientist",
    "specialist",
    "student",
    "technician",
}
SMALL_WORDS = {"a", "an", "and", "at", "de", "for", "in", "of", "on", "the", "to", "with", "&"}
MONTH = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
    r"|Spring|Summer|Autumn|Fall|Winter"
)
WHEN = rf"(?:(?:{MONTH})\s+)?(?:19|20)\d\d"
UNTIL = r"(?:present|current|now|today|ongoing)"
DATE_RANGE = re.compile(
    rf"(?:{WHEN})\s*(?:-{{1,3}}|–|—|to)\s*(?:{WHEN}|{UNTIL})|(?:since|from)\s+{WHEN}",
    re.IGNORECASE,
)
YEAR = re.compile(rf"\b(?:19|20)\d\d\b|\b{UNTIL}\b", re.IGNORECASE)
URL = re.compile(r"(?:https?://|www\.)\S+")
CONTACT = re.compile(
    r"@|https?://|www\.|linkedin|github|orcid|\+?\d[\d ()./-]{6,}\d|·|\|", re.IGNORECASE
)
BULLET = re.compile(r"^\s*[-*+•●▪◦]\s+(.*)$")
NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
# A dash list run together on one line: "...(2019). - Next item" or "| 2014--2018 - GPA".
INLINE_BULLET = re.compile(r"(?<=[.;:)\d])\s+-\s+(?=[A-Z])")


@dataclass
class Block:
    kind: str  # paragraph | bullets | numbered | entry
    text: str = ""
    items: list = field(default_factory=list)  # bullets: [str]; numbered: [(n, str)]
    right: str = ""  # entry: dates or location, aligned right
    style: str = ""  # entry: org (employer line) | role (dated line) | plain


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class CV:
    name: str = ""
    contact: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    @property
    def titles(self):
        return [s.title for s in self.sections if s.title]


def clean(text):
    """Typographic dashes, no stray escapes or hard-break backslashes, single spaces."""
    parts = []
    last = 0
    for match in URL.finditer(text):
        parts.append(_typography(text[last : match.start()]))
        parts.append(match.group())
        last = match.end()
    parts.append(_typography(text[last:]))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _typography(text):
    text = re.sub(r"\\([^\w\s])", r"\1", text)  # Markdown escapes such as \$ \& \*
    text = re.sub(r"\\(?=\s|$)", "", text)  # pandoc hard line breaks
    text = text.replace("---", "—").replace("--", "–")
    return text


def _plain(text):
    """Text with Markdown emphasis and link syntax removed, for matching and checks."""
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1", text)
    return re.sub(r"(\*\*|__|\*)(.+?)\1", r"\2", text)


def section_title(line):
    """The section title a bare or Markdown line declares, else None."""
    match = re.match(r"^\s{0,3}#{1,2}\s+(.+?)\s*#*\s*$", line)
    if match:
        return clean(_plain(match.group(1)))
    text = _plain(line.strip())
    text = re.sub(r"^(\*\*|__)(.+)\1$", r"\2", text).strip().rstrip(":").strip()
    if not text or len(text) > 60 or re.search(r"[\d|,;.!?()\\/]|—|--|\s[-–]\s", text):
        return None
    words = text.split()
    if len(words) > 8 or BULLET.match(line) or not words[0][:1].isupper():
        return None
    if any(w.lower() not in SMALL_WORDS and not w[:1].isupper() for w in words):
        return None
    lowered = {re.sub(r"\W", "", w.lower()) for w in words}
    if lowered & ROLE_WORDS:
        return None
    shouting = text.isupper() and sum(c.isalpha() for c in text) >= 3
    if not shouting and not lowered & SECTION_WORDS:
        return None
    return clean(text)


def _is_rule(line):
    return bool(re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*", line))


def _dated(text):
    """The trailing date part of an entry header line, or None."""
    text = clean(_plain(text))
    if "|" in text:
        tail = text.rsplit("|", 1)[1].strip()
        if YEAR.search(tail) and len(tail) <= 40:
            return tail
    match = re.search(rf"\(?((?:{DATE_RANGE.pattern}))\)?\s*$", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _org(line):
    """An employer line such as ``Employer --- City, Country`` or ``Employer | City``."""
    text = clean(line)
    if len(text) > 120:
        return False
    if " | " in text:
        tail = text.rsplit(" | ", 1)[1]
        return bool(tail) and len(tail) <= 40 and not YEAR.search(tail) and not text.endswith(",")
    if not re.search(r"\s[—–]\s", text):
        return False
    last = text.split()[-1].lower()
    return not re.search(r"[,.;:|]$", text) and last not in SMALL_WORDS


def _entry(text, style):
    text = clean(text)
    right = ""
    if style == "org":
        left, _, right = text.rpartition(" | ")
        if not left:
            left, _, right = text.partition(" — ")
        if not right:
            left, _, right = text.partition(" – ")
        return Block("entry", left.strip(), right=right.strip(), style="org")
    when = _dated(text)
    if when and "|" in text and text.rsplit("|", 1)[1].strip() == when:
        text, right = text.rsplit("|", 1)[0].strip(), when
    elif when:
        index = text.rfind(when)
        text, right = text[:index].rstrip(" ,(–—-"), when
    return Block("entry", text, right=right.strip(), style=style)


def parse(source):
    """Parse Markdown or plain-text resume source into a CV."""
    text = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)  # YAML front matter
    lines = [line.rstrip().expandtabs(4) for line in text.replace("\r\n", "\n").split("\n")]
    cv = CV()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines):
        match = re.match(r"^\s*#\s+(.+)$", lines[i])
        first = match.group(1) if match else lines[i]
        if match or (len(first.strip()) <= 80 and not section_title(first)):
            cv.name = clean(_plain(first.strip().strip("*_")))
            i += 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if section_title(line) or _is_rule(line):
            break
        if not CONTACT.search(line) or len(line) > 160:
            break
        cv.contact.append(clean(_plain(line)).strip(" |·"))
        i += 1

    body, title = [], ""
    for index in range(i, len(lines)):
        line = lines[index]
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if line.strip() and re.fullmatch(r"\s*(?:={3,}|-{3,})\s*", nxt) and not BULLET.match(line):
            lines[index + 1] = ""  # Setext heading underline
            found = clean(_plain(line.strip()))
        else:
            found = section_title(line)
        if found:
            if title or any(x.strip() for x in body):
                cv.sections.append(Section(title, _blocks(body)))
            body, title = [], found
        elif _is_rule(line):
            body.append("")
        else:
            body.append(line)
    if title or any(x.strip() for x in body):
        cv.sections.append(Section(title, _blocks(body)))
    return cv


def _blocks(lines):
    blocks, buffer, item = [], [], None

    def flush():
        nonlocal buffer
        if buffer:
            blocks.append(Block("paragraph", clean(" ".join(buffer))))
        buffer = []

    def add_item(kind, value):
        nonlocal item
        if not blocks or blocks[-1].kind != kind:
            blocks.append(Block(kind))
        item = [value]
        blocks[-1].items.append(item)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            item = None
            continue
        heading = re.match(r"^\s{0,3}#{3,6}\s+(.+?)\s*#*$", line)
        bullet = BULLET.match(line)
        numbered = NUMBERED.match(line)
        if heading:
            flush()
            item = None
            blocks.append(_entry(_plain(heading.group(1)), "plain"))
        elif bullet:
            flush()
            add_item("bullets", bullet.group(1))
        elif numbered:
            flush()
            add_item("numbered", (numbered.group(1), numbered.group(2)))
        elif item is not None and (
            line.startswith("  ")
            or stripped[:1].islower()
            or stripped[:1].isdigit()
            or re.search(r"[,|&]$|\b(?:and|of|the|in|for|to|with)$", _item_text(item))
        ):
            item.append(stripped)
        elif not buffer and stripped.startswith("|") and blocks and blocks[-1].kind == "entry":
            # "Degree --- University" then "| 2014--2018 - GPA: ... - Thesis ..."
            item = None
            rest = stripped[1:].strip()
            when, _, extra = rest.partition(" - ")
            entry = blocks[-1]
            if entry.style == "org" and entry.right:
                entry.text = f"{entry.text} — {entry.right}"
            entry.right, entry.style = clean(when), "plain"
            if extra.strip():
                add_item("bullets", extra.strip())
        else:
            item = None
            buffer.append(stripped)
            joined = " ".join(buffer)
            sentence = any(re.search(r"[.!?]$", b) for b in buffer[:-1])
            wrapped = re.search(r"[,|&–—-]$|\b(?:and|of|the|in|for|to|with|at)$", stripped)
            if _dated(joined) and len(joined) <= 220 and not sentence and not wrapped:
                after_org = blocks and blocks[-1].kind == "entry" and blocks[-1].style != "plain"
                blocks.append(_entry(joined, "role" if after_org else "plain"))
                buffer = []
            elif len(buffer) == 1 and _org(stripped):
                blocks.append(_entry(stripped, "org"))
                buffer = []
    flush()
    for block in blocks:
        if block.kind == "bullets":
            items = []
            for parts in block.items:
                items.extend(INLINE_BULLET.split(" ".join(parts)))
            block.items = [clean(x) for x in items if x.strip()]
        elif block.kind == "numbered":
            block.items = [
                (parts[0][0], clean(" ".join([parts[0][1], *parts[1:]]))) for parts in block.items
            ]
    return blocks


def _item_text(item):
    last = item[-1]
    return last[1] if isinstance(last, tuple) else last


def to_markdown(cv):
    """The CV as readable Markdown: # name, contact line, ## sections, ### entries."""
    out = []
    if cv.name:
        out += [f"# {cv.name}", ""]
    if cv.contact:
        out += [" · ".join(cv.contact), ""]
    for section in cv.sections:
        if section.title:
            out += [f"## {section.title}", ""]
        for block in section.blocks:
            if block.kind == "paragraph":
                out += [block.text, ""]
            elif block.kind == "bullets":
                out += [f"- {x}" for x in block.items] + [""]
            elif block.kind == "numbered":
                out += [f"{n}. {x}" for n, x in block.items] + [""]
            else:
                joiner = " — " if block.style == "org" else " | "
                out += [f"### {block.text}{joiner + block.right if block.right else ''}", ""]
    return "\n".join(out).rstrip() + "\n"

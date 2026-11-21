"""The resume PDF the user supplies: attached to applications exactly as it is.

Nothing here renders or changes the PDF. It is checked (a real PDF whose text contains
the candidate name and email) at prepare, approval and fill time, never copied or pinned,
so a revised PDF is used as soon as it is saved. Its text can be extracted into resume.md,
which the profile, letters and screening answers use.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mode

from app import cv
from app.private import private_open

EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
BULLET_GLYPHS = "•●▪◦‣∙·■□➢►▶✓–-*"


@dataclass
class PdfCheck:
    path: Path
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.problems


def pdf_path(cfg):
    return cfg.resolved(cfg.resume_pdf_path)


def pdf_text(path):
    """All text pdfminer extracts; raises ValueError when the file is not a readable PDF."""
    from pdfminer.high_level import extract_text
    from pdfminer.pdfparser import PDFSyntaxError

    path = Path(path)
    with path.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError(f"{path} is not a PDF")
    try:
        return extract_text(str(path))
    except (PDFSyntaxError, ValueError, TypeError, KeyError, AssertionError) as exc:
        raise ValueError(f"{path} cannot be read as a PDF ({type(exc).__name__})") from exc


def _flat(text):
    return re.sub(r"\s+", " ", text).casefold()


def check(cfg, profile, resume_md=""):
    """Problems that block attaching the supplied PDF; each says how to fix it."""
    path = pdf_path(cfg)
    result = PdfCheck(path)
    if not path.is_file():
        result.problems.append(
            f"Resume PDF missing: put your resume PDF at {path} (or set resume_pdf in config.yaml)"
        )
        return result
    try:
        text = pdf_text(path)
    except (ValueError, OSError) as exc:
        result.problems.append(f"{exc}; export your resume as a PDF and save it at {path}")
        return result
    flat = _flat(text)
    if len(flat.strip()) < 10:
        result.problems.append(
            f"Resume PDF {path} has no extractable text (a scan or image?); applicant tracking "
            "systems cannot read it. Export it from your word processor as a text PDF"
        )
        return result
    name = (profile.name or "").strip()
    if not name or _flat(name) not in flat:
        result.problems.append(
            f"Resume PDF text does not contain your name ({name or 'unset'}); check the PDF "
            f"and the name in your candidate profile"
        )
    emails = [profile.email] if profile.email else EMAIL.findall(resume_md)
    if not emails:
        result.problems.append(
            "No email known: add it to resume.md or the profile (bio-jobs setup), and the PDF"
        )
    elif not any(_flat(e) in flat for e in emails):
        result.problems.append(
            f"Resume PDF text does not contain your email ({emails[0]}); add it to the PDF"
        )
    return result


@dataclass
class _Line:
    page: int
    top: float
    x0: float
    x1: float
    size: float
    text: str


def _layout_lines(path):
    """Text lines in reading order, with pieces on one baseline joined left to right."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

    pieces = []

    def walk(item, page):
        if isinstance(item, LTTextLine):
            text = re.sub(r"\s+", " ", item.get_text()).strip()
            sizes = [c.size for c in item if isinstance(c, LTChar)]
            if text:
                pieces.append(
                    _Line(page, -item.y1, item.x0, item.x1, max(sizes) if sizes else 0, text)
                )
        elif isinstance(item, LTTextContainer) or hasattr(item, "__iter__"):
            for child in item:
                walk(child, page)

    for page_number, page in enumerate(extract_pages(str(path), laparams=LAParams())):
        for item in page:
            walk(item, page_number)
    pieces.sort(key=lambda p: (p.page, round(p.top), p.x0))
    lines = []
    for piece in pieces:
        last = lines[-1] if lines else None
        if last and last.page == piece.page and abs(last.top - piece.top) < 2.5:
            gap = piece.x0 - last.x1
            last.text += (" | " if gap > 12 else " ") + piece.text
            last.x1, last.size = piece.x1, max(last.size, piece.size)
        else:
            lines.append(piece)
    return lines


def to_plain_text(path):
    """Layout text with paragraph breaks, headings marked by size and bullets normalised.

    Bullets drawn as shapes leave no glyph, so an indented line starts a new bullet unless
    it continues the line above (no sentence end and the line above reached the margin).
    """
    lines = _layout_lines(path)
    if not lines:
        return ""
    body = mode(round(line.size, 1) for line in lines)
    biggest = max(line.size for line in lines)
    left = min(line.x0 for line in lines)
    right = max(line.x1 for line in lines)
    out, previous, in_list = [], None, False
    for line in lines:
        text = line.text
        gap = previous and line.page == previous.page and line.top - previous.top > 1.6 * body
        if gap:
            out.append("")
        glyph = re.match(rf"^[{re.escape(BULLET_GLYPHS)}]\s+", text)
        numbered = re.match(r"^\d+[.)]\s", text)
        indented = line.x0 > left + 3
        continues = (
            previous is not None
            and not gap
            and not re.search(r"[.!?;:]$", previous.text)
            and previous.x1 > left + 0.8 * (right - left)
        )
        if not out and line.size >= biggest - 0.1:
            out.append("# " + text)
            in_list = False
        elif (
            line.size >= body + 1
            and len(text.split()) <= 8
            and not re.search(r"\d|@", text)
            and not glyph
        ):
            out.append("## " + text)
            in_list = False
        elif glyph:
            out.append("- " + text[glyph.end() :])
            in_list = True
        elif numbered:
            out.append(text)
            in_list = True
        elif indented and in_list and (continues or not text[:1].isupper()):
            out.append("   " + text)
        elif indented and text[:1].isupper() or indented and text[:1].isdigit():
            out.append("- " + text)
            in_list = True
        else:
            in_list = False
            out.append(text)
        previous = line
    return "\n".join(out)


def extract_markdown(path):
    """Readable Markdown from the resume PDF's text; wording is kept, layout recovered."""
    pdf_text(path)  # clear errors for non-PDFs before layout analysis
    text = to_plain_text(path)
    if len(text.strip()) < 10:
        raise ValueError(
            f"{path} has no extractable text (a scan or image?); export it as a text PDF"
        )
    return cv.to_markdown(cv.parse(text))


def write_resume_md(cfg, *, force=False, source=None):
    """Extract the resume PDF into resume.md; never replaces a real resume.md unless forced."""
    from app.resume import resume_status_text

    source = Path(source) if source else pdf_path(cfg)
    target = cfg.resolved(cfg.resume_path)
    if not source.is_file():
        raise ValueError(f"No resume PDF at {source}; save your resume PDF there first")
    state = resume_status_text(target)
    if state == "filled" and not force:
        raise ValueError(f"{target} already has your resume; use --force to replace it")
    markdown = extract_markdown(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with private_open(target, encoding="utf-8") as output:
        output.write(markdown)
    return target, markdown

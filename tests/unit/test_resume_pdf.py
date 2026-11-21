"""Synthetic resumes only: structure recovery for resume.md and the resume PDF check."""

from app import cv, resume_pdf
from app.candidate.profile import CandidateProfile
from app.settings import Settings

PLAIN = """Jordan Sample, PhD
+44 7000 000000 · jordan.sample@example.org
Profile
Immunologist with 10+ years of discovery research. Led programmes
from target to candidate.
Professional Experience
Example Therapeutics --- London, UK
Principal Scientist | January 2026--present
Senior Scientist | March 2024--December 2025
- Led a cross-functional team of four, defining strategy
  and timelines.
- Built an in-house screening platform.
Example Institute --- Cambridge, UK
Postdoctoral Fellow, Laboratory of A. Person |
2020--2024
Research focus: tumour immunology
- Ran two translational programmes.
Education
PhD in Immunology --- Example University, Boston, MA, USA
| 2014--2020 - GPA: 4.0/4.0 - Thesis prize, 2020
Selected Publications
1. Sample J, Person A. "A synthetic title." Journal, 2024;
   1(2):3--4.
2. Person A, Sample J. "Another title." Journal, 2023.
Teaching
Teaching Assistant, Example University | 2016, 2017\\
- Immunology (2016) and Cancer Biology (2017). - Led weekly recitations.
Fellowships and Funding
- Example Fellowship | 2020--2024 | \\$100,000
"""

MARKDOWN = """# Jordan Sample
jordan.sample@example.org

## Experience
### Scientist — Example Labs | 2020–2024
- Developed **cell assays**.
- Python analysis.

## Education
MSc Biology, Example University, 2018
"""


def test_plain_text_resume_structure():
    parsed = cv.parse(PLAIN)
    assert parsed.name == "Jordan Sample, PhD"
    assert parsed.contact == ["+44 7000 000000 · jordan.sample@example.org"]
    assert parsed.titles == [
        "Profile",
        "Professional Experience",
        "Education",
        "Selected Publications",
        "Teaching",
        "Fellowships and Funding",
    ]
    experience = parsed.sections[1].blocks
    assert (experience[0].kind, experience[0].style) == ("entry", "org")
    assert (experience[0].text, experience[0].right) == ("Example Therapeutics", "London, UK")
    assert (experience[1].text, experience[1].right) == (
        "Principal Scientist",
        "January 2026–present",
    )
    assert experience[1].style == "role"
    assert experience[3].items == [
        "Led a cross-functional team of four, defining strategy and timelines.",
        "Built an in-house screening platform.",
    ]
    wrapped = experience[5]
    assert wrapped.right == "2020–2024" and wrapped.text.startswith("Postdoctoral Fellow")
    assert experience[6].kind == "paragraph"
    education = parsed.sections[2].blocks
    assert education[0].right == "2014–2020"
    assert education[0].text == "PhD in Immunology — Example University, Boston, MA, USA"
    assert education[1].items == ["GPA: 4.0/4.0", "Thesis prize, 2020"]
    publications = parsed.sections[3].blocks[0]
    assert publications.kind == "numbered"
    assert publications.items[0] == (
        "1",
        'Sample J, Person A. "A synthetic title." Journal, 2024; 1(2):3–4.',
    )
    teaching = parsed.sections[4].blocks
    assert teaching[0].right == "2016, 2017"
    assert teaching[1].items == [
        "Immunology (2016) and Cancer Biology (2017).",
        "Led weekly recitations.",
    ]
    assert parsed.sections[5].blocks[0].items == ["Example Fellowship | 2020–2024 | $100,000"]


def test_markdown_resume_round_trips_to_clean_markdown():
    parsed = cv.parse(MARKDOWN)
    assert parsed.name == "Jordan Sample" and parsed.titles == ["Experience", "Education"]
    text = cv.to_markdown(parsed)
    assert text.startswith("# Jordan Sample\n\njordan.sample@example.org\n\n## Experience\n")
    assert "### Scientist — Example Labs | 2020–2024" in text
    assert "- Developed **cell assays**." in text
    assert cv.to_markdown(cv.parse(text)) == text


def test_markdown_from_plain_text_has_no_pandoc_artifacts():
    text = cv.to_markdown(cv.parse(PLAIN))
    assert "---" not in text and "--" not in text and "\\" not in text
    assert "## Professional Experience" in text and "### Example Therapeutics — London, UK" in text
    assert "- Led weekly recitations." in text


def test_role_lines_and_prose_are_not_sections():
    assert cv.section_title("Senior Research Scientist") is None
    assert cv.section_title("Teaching Assistant") is None
    assert cv.section_title("Led a team of four.") is None
    assert cv.section_title("EXPERIENCE") == "EXPERIENCE"
    assert cv.section_title("**Skills**") == "Skills"
    assert cv.section_title("Leadership and Community") == "Leadership and Community"


def checked(tmp_path, make_pdf, lines, *, name="Jordan Sample", email="", resume_md=""):
    cfg = Settings(_env_file=None, resume_pdf_path=tmp_path / "resume.pdf")
    if lines is not None:
        make_pdf(cfg.resume_pdf_path, lines)
    profile = CandidateProfile(name=name, email=email, target_titles=[])
    return resume_pdf.check(cfg, profile, resume_md)


def test_pdf_check_passes_with_name_and_email(tmp_path, make_pdf):
    lines = [("Jordan Sample", 18, 40, 50), ("jordan.sample@example.org", 10, 40, 70)]
    result = checked(tmp_path, make_pdf, lines, resume_md="jordan.sample@example.org")
    assert result.ok, result.problems


def test_pdf_check_blocks_missing_non_pdf_image_only_and_wrong_identity(tmp_path, make_pdf):
    assert "Resume PDF missing" in checked(tmp_path, make_pdf, None).problems[0]
    (tmp_path / "resume.pdf").write_text("plain text, not a PDF")
    cfg = Settings(_env_file=None, resume_pdf_path=tmp_path / "resume.pdf")
    problems = resume_pdf.check(
        cfg, CandidateProfile(name="Jordan Sample", target_titles=[]), ""
    ).problems
    assert "is not a PDF" in problems[0]
    assert "no extractable text" in checked(tmp_path, make_pdf, []).problems[0]
    lines = [("Someone Else", 18, 40, 50), ("someone@example.org", 10, 40, 70)]
    problems = checked(tmp_path, make_pdf, lines, email="jordan.sample@example.org").problems
    assert any("your name" in p for p in problems)
    assert any("jordan.sample@example.org" in p for p in problems)
    lines = [("Jordan Sample", 18, 40, 50), ("A resume without contact details", 10, 40, 70)]
    assert any("No email known" in p for p in checked(tmp_path, make_pdf, lines).problems)


def test_pdf_extraction_recovers_headings_entries_and_bullets(tmp_path, make_pdf):
    path = make_pdf(
        tmp_path / "cv.pdf",
        [
            ("Jordan Sample", 20, 40, 50),
            ("jordan.sample@example.org | London", 9, 40, 70),
            ("Experience", 12, 40, 100),
            ("Example Therapeutics", 10, 40, 118),
            ("2019 - present", 10, 480, 118),
            ("Principal Scientist", 10, 40, 131),
            ("• Developed cell assays for", 10, 50, 146),
            ("screening programmes.", 10, 58, 158),
            ("• Led a team of four.", 10, 50, 171),
            "PAGE",
            ("Education", 12, 40, 60),
            ("BSc Biology, Example University", 10, 40, 78),
            ("2012 - 2015", 10, 480, 78),
        ],
    )
    text = resume_pdf.extract_markdown(path)
    assert text.splitlines()[:3] == ["# Jordan Sample", "", "jordan.sample@example.org | London"]
    assert "## Experience" in text and "## Education" in text
    assert "### Example Therapeutics | 2019 - present" in text
    assert "- Developed cell assays for screening programmes.\n- Led a team of four." in text
    assert "### BSc Biology, Example University | 2012 - 2015" in text

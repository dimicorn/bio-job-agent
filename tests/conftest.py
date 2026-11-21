import json
import socket
from pathlib import Path

import httpx
import pytest

from app.candidate.profile import load_profile
from app.db import connect, migrate
from app.settings import ROOT
from app.sources.config import load_sources

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", help="Explicitly permit marked live HTTP checks"
    )


@pytest.fixture(autouse=True)
def offline_network(request, monkeypatch):
    if request.node.get_closest_marker("live") and request.config.getoption("--live"):
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Network disabled: use recorded fixtures or explicit --live -m live")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def profile():
    return load_profile(ROOT / "config/candidate.example.yaml")


@pytest.fixture
def sources():
    return load_sources(ROOT / "config/sources.yaml")[:2]


@pytest.fixture
def conn(tmp_path):
    with connect(tmp_path / "jobs.sqlite3") as db:
        migrate(db)
        yield db


@pytest.fixture
def transport():
    def handler(request):
        host = request.url.host
        source = "lever" if host == "api.lever.co" else "greenhouse"
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=(FIXTURES / f"{source}_robots.txt").read_text())
        return httpx.Response(200, json=json.loads((FIXTURES / f"{source}.json").read_text()))

    return httpx.MockTransport(handler)


def write_pdf(path, lines, *, page_height=842):
    """A small text PDF without a browser. lines: (text, size, x, y_from_top[, bold])."""
    pages = [[]]
    for line in lines:
        if line == "PAGE":
            pages.append([])
        else:
            pages[-1].append(line)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # page tree, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for page in pages:
        stream = b""
        for text, size, x, top, *bold in page:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            font = b"/F2" if bold and bold[0] else b"/F1"
            stream += b"BT %s %g Tf %g %g Td (%s) Tj ET\n" % (
                font,
                size,
                x,
                page_height - top,
                escaped.encode("cp1252"),
            )
        objects.append(b"<< /Length %d >>\nstream\n%sendstream" % (len(stream), stream))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (page_height, len(objects))
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    Path(path).write_bytes(out)
    return Path(path)


@pytest.fixture
def make_pdf():
    return write_pdf

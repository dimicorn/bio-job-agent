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

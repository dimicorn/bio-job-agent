import json
from pathlib import Path

import httpx

from app.sources.ats.lever import Lever
from app.sources.base import PublicClient

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_lever_pagination(sources):
    fixture = json.loads((FIXTURES / "lever.json").read_text())[0]
    skips = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 2")
        skips.append(request.url.params["skip"])
        return httpx.Response(
            200, json=[fixture] * 100 if request.url.params["skip"] == "0" else [fixture]
        )

    delays = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = PublicClient(http, "BioJobAgent/0.1", pause=delays.append)
        jobs = Lever(sources[0], client).fetch()
    assert len(jobs) == 101
    assert skips == ["0", "100"]
    assert delays and all(0 < delay <= 2 for delay in delays)

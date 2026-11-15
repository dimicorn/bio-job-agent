import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.models.posting import SourceConfig


class AccessRestricted(RuntimeError):
    pass


class RateLimited(RuntimeError):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"HTTP 429; retry after {seconds} seconds")


class PublicClient:
    """Read-only, bounded requests; robots checked once per host per sync; no retries."""

    def __init__(self, client: httpx.Client, user_agent: str, pause=time.sleep):
        self.client = client
        self.user_agent = user_agent
        self.pause = pause
        self.robots = {}
        self.last_request = {}
        self.delays = {}

    def _request(self, url: str):
        host = urlsplit(url).netloc
        remaining = self.delays.get(host, 1) - (time.monotonic() - self.last_request.get(host, 0))
        if remaining > 0:
            self.pause(remaining)
        self.last_request[host] = time.monotonic()
        response = self.client.get(
            url, headers={"User-Agent": self.user_agent}, follow_redirects=False
        )
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "900")
            try:
                seconds = int(retry)
            except ValueError:
                try:
                    seconds = int(
                        (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError):
                    seconds = 900
            raise RateLimited(max(900, seconds))
        if response.is_redirect or response.status_code in {401, 403}:
            raise AccessRestricted(f"Access restricted: HTTP {response.status_code}")
        return response

    def get(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            response = self._request(origin + "/robots.txt")
            robot = RobotFileParser()
            if response.status_code in {404, 410}:
                robot.parse([])
            else:
                response.raise_for_status()
                robot.parse(response.text.splitlines())
            self.robots[origin] = robot
            rate = robot.request_rate(self.user_agent)
            self.delays[parts.netloc] = max(
                1,
                robot.crawl_delay(self.user_agent) or 0,
                rate.seconds / rate.requests if rate else 0,
            )
        if not self.robots[origin].can_fetch(self.user_agent, url):
            raise AccessRestricted("robots.txt disallows this path")
        response = self._request(url)
        response.raise_for_status()
        return response

    def get_json(self, url: str):
        return self.get(url).json()

    def get_text(self, url: str):
        return self.get(url).text


class Adapter:
    def __init__(self, source: SourceConfig, client: PublicClient):
        self.source = source
        self.client = client

    def fetch(self) -> list[dict]:
        raise NotImplementedError

    def parse(self, raw: dict):
        raise NotImplementedError

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


def text(value: str | None) -> str:
    # Greenhouse may double-encode HTML. Never render source HTML in the UI.
    value = html.unescape(html.unescape(value or ""))
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value or "").casefold()))


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"gh_src", "lever-source", "source"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(sorted(query)),
            "",
        )
    )


def location_key(value: str | None) -> str:
    normalized = key(value)
    normalized = re.sub(r"\b(?:united kingdom|great britain|england|uk|gb)\b", "", normalized)
    return " ".join(normalized.split())

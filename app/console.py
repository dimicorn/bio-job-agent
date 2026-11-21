"""One shared terminal console for every command, after the sibling hh-agent.

Run progress goes through ``log``: a dim timestamp, the worker-thread name in bold for parallel
work, then the message, coloured by outcome (green OK, yellow warning, yellow blocked, red
failure, dim detail). ``header`` draws a rule for each phase (sync, score, letters, prepare,
apply, alerts). Output is plain text when stdout is not a terminal or NO_COLOR is set.

Job and employer text is untrusted: the console never parses Rich markup and ``text`` strips
terminal escape and control sequences, so source data cannot style or move the terminal.

``console.sequential`` lets parallel work print in ordered blocks: lines of the current block
print at once, lines of later blocks are held and printed whole (with their original
timestamps) when the previous block ends (``section_done``).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from rich.console import Console
from rich.text import Text

_ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# kind: (symbol, style of the symbol and message)
KINDS = {
    "info": ("", ""),
    "ok": ("✓", "green"),
    "warn": ("!", "yellow"),
    "blocked": ("⊘", "yellow"),
    "fail": ("✗", "red"),
    "detail": ("", "dim"),
}


class Line(str):
    """A message carrying its outcome kind; still an ordinary string for any callback."""

    kind = "info"

    def __new__(cls, value, kind="info"):
        line = super().__new__(cls, value)
        line.kind = kind
        return line


def ok(message) -> Line:
    return Line(message, "ok")


def warn(message) -> Line:
    return Line(message, "warn")


def blocked(message) -> Line:
    return Line(message, "blocked")


def fail(message) -> Line:
    return Line(message, "fail")


def detail(message) -> Line:
    return Line(message, "detail")


def clean(value) -> str:
    """Remove terminal escape and control sequences (newlines and tabs stay)."""
    return _CONTROLS.sub("", _ESCAPES.sub("", str(value or "")))


def text(value, style="") -> Text:
    """Untrusted text as a Rich Text: never markup, never escape sequences."""
    return Text(clean(value), style=style)


def _worker() -> str | None:
    thread = threading.current_thread()
    return None if thread is threading.main_thread() else thread.name


def _plain() -> bool:
    return bool(os.environ.get("NO_COLOR"))


class _Console(Console):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        kwargs.setdefault("highlight", False)
        if _plain():
            kwargs.setdefault("color_system", None)
        super().__init__(*args, **kwargs)
        self._seq_lock = threading.RLock()
        self._order: list[tuple[str, str]] = []
        self._index: dict[str, int] = {}
        self._section_of: Callable[[str], str | None] | None = None
        self._current = 0
        self._done: set[str] = set()
        self._buffers: dict[str, list[tuple[tuple, dict]]] = {}

    def print(self, *objects, **kwargs) -> None:
        name = _worker()
        with self._seq_lock:
            section = self._section_of(name) if name and self._section_of else None
            if self._index.get(section, -1) > self._current:
                # The block has not reached its turn yet: hold its lines.
                self._buffers[section].append((objects, kwargs))
                return
            super().print(*objects, **kwargs)

    def rule(self, title="", **kwargs) -> None:
        if _worker():
            # Workers print a header as an ordinary line so it stays in its block.
            self.print(Text(f"── {clean(title)} ──" if title else "──", style="bold"))
        else:
            super().rule(Text(clean(title), style="bold") if title else "", **kwargs)

    @contextmanager
    def sequential(
        self, order: list[tuple[str, str]], section_of: Callable[[str], str | None]
    ) -> Iterator[None]:
        """order is [(block, header)]; section_of maps a thread name to its block (None: now)."""
        with self._seq_lock:
            self._order, self._section_of, self._current, self._done = (
                list(order),
                section_of,
                0,
                set(),
            )
            self._index = {key: i for i, (key, _) in enumerate(order)}
            self._buffers = {key: [] for key, _ in order}
            if order:
                super().rule(Text(clean(order[0][1]), style="bold"))
        try:
            yield
        finally:
            with self._seq_lock:
                self._done = {key for key, _ in self._order}
                self._advance()
                self._order, self._section_of, self._buffers, self._index = [], None, {}, {}

    def section_done(self, key: str) -> None:
        """A block ended: print the held lines of the next blocks and print them directly."""
        with self._seq_lock:
            self._done.add(key)
            self._advance()

    def _advance(self) -> None:
        while self._current < len(self._order) and self._order[self._current][0] in self._done:
            self._current += 1
            if self._current < len(self._order):
                key, title = self._order[self._current]
                super().rule(Text(clean(title), style="bold"))
                for objects, kwargs in self._buffers.pop(key, []):
                    super().print(*objects, **kwargs)


console = _Console()
err_console = _Console(stderr=True)


def line(message, kind=None) -> Text:
    """A timestamped progress line, with the worker name and outcome style."""
    kind = kind or getattr(message, "kind", "info")
    symbol, style = KINDS.get(kind, KINDS["info"])
    result = Text()
    result.append(f"{datetime.now():%H:%M:%S} ", style="dim")
    name = _worker()
    if name:
        result.append(f"{clean(name)} ", style="bold")
    if symbol:
        result.append(f"{symbol} ", style=f"bold {style}")
    result.append(text(message, style))
    return result


def log(message, kind=None, *, target=None) -> None:
    """Print one run-progress line (message is untrusted text)."""
    (target or console).print(line(message, kind), soft_wrap=True)


def header(title, *, target=None) -> None:
    """A rule naming the phase that starts now."""
    (target or console).rule(title)


def summary(title, parts, *, target=None) -> None:
    """A compact end-of-run line: parts are (text, kind) pairs joined by dots."""
    result = Text()
    result.append(f"{datetime.now():%H:%M:%S} ", style="dim")
    result.append(clean(title), style="bold")
    for index, (part, kind) in enumerate(parts):
        result.append(": " if index == 0 else " · ", style="dim")
        result.append(text(part, KINDS.get(kind, KINDS["info"])[1]))
    (target or console).print(result, soft_wrap=True)


def run_summary(record) -> list[tuple[str, str]]:
    """Counts from a privacy-safe run summary (app.runlog), for ``summary``."""

    def counted(items, key):
        counts = {}
        for item in items:
            counts[item.get(key)] = counts.get(item.get(key), 0) + 1
        return counts

    parts = []
    sources = counted(record.get("sources", []), "status")
    if sources:
        bad = sources.get("error", 0)
        kind = "fail" if bad and bad == sum(sources.values()) else "warn" if bad else "ok"
        parts.append(
            (
                f"{sum(sources.values())} sources ("
                + ", ".join(f"{count} {status}" for status, count in sorted(sources.items()))
                + ")",
                "warn" if kind == "ok" and sources.get("partial") else kind,
            )
        )
    jobs = counted(record.get("jobs", []), "change")
    if jobs:
        parts.append(
            ("jobs " + ", ".join(f"{count} {change}" for change, count in sorted(jobs.items())), "")
        )
    scoring = counted(record.get("scoring", []), "status")
    if scoring:
        failed = scoring.get("failed", 0)
        parts.append(
            (
                f"scored {scoring.get('scored', 0)}" + (f", {failed} failed" if failed else ""),
                "fail" if failed else "ok",
            )
        )
    finals = {}
    for item in record.get("applications", []):
        finals[item.get("id")] = item.get("to")
    applications = counted([{"to": to} for to in finals.values()], "to")
    for state, count in sorted(applications.items()):
        kind = {"submitted": "ok", "manual_required": "blocked", "unknown_result": "fail"}.get(
            state, "warn"
        )
        parts.append((f"{count} {state}", kind))
    for item in record.get("notifications", []):
        if item.get("result") in ("delivered", "failed"):
            parts.append(
                (
                    f"{item.get('channel')} alert {item.get('result')}",
                    "ok" if item.get("result") == "delivered" else "fail",
                )
            )
    return parts or [("nothing to report", "detail")]


class Handler(logging.Handler):
    """Standard-library log records as styled lines on stderr."""

    def emit(self, record) -> None:
        try:
            kind = (
                "fail"
                if record.levelno >= logging.ERROR
                else "warn"
                if record.levelno >= logging.WARNING
                else "detail"
            )
            log(self.format(record), kind, target=err_console)
        except Exception:  # noqa: BLE001 - logging must never break a run.
            self.handleError(record)

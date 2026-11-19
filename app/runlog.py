"""Privacy-safe, local summaries for command runs."""

import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from app.settings import ROOT

_current = ContextVar("run_summary", default=None)


def now():
    return datetime.now(UTC).isoformat()


def record(section, value):
    summary = _current.get()
    if summary is not None:
        summary[section].append(value)


@contextmanager
def run(command, directory=None):
    directory = Path(directory) if directory is not None else ROOT / "run-summaries"
    summary = {
        "id": uuid.uuid4().hex,
        "command": command,
        "started_at": now(),
        "sources": [],
        "jobs": [],
        "scoring": [],
        "applications": [],
        "notifications": [],
        "status": "running",
    }
    token = _current.set(summary)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (summary["id"] + ".json")

    def save():
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, sort_keys=True)
        temporary.replace(path)

    try:
        save()
        yield summary
    except BaseException as exc:
        summary["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        summary["error_category"] = type(exc).__name__
        raise
    else:
        summary["status"] = "failed" if summary.get("exit_code") else "completed"
    finally:
        summary["finished_at"] = now()
        try:
            save()
        finally:
            _current.reset(token)

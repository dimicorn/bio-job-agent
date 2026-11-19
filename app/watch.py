"""One locked sync/score/alert pass, with a durable digest and at-most-once delivery attempts."""

import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app import analyst
from app.pipeline.discover import discover, utcnow
from app.resume import digest, require_reviewed
from app.store import list_jobs

MAX_SEPARATE = 3


@contextmanager
def run_lock(database):
    """Kernel lock: released on process exit, including a crash. Never unlink the inode."""
    path = database.with_suffix(database.suffix + ".watch.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def content_version(row):
    # Stable job facts only. Board IDs, provenance and publication timestamps are not changes.
    from app.models.posting import Posting

    facts = {
        k: row[k]
        for k in Posting.model_fields
        if k not in {"external_id", "source_url", "posted_at"}
    }

    def clean(value):
        if isinstance(value, str):
            return " ".join(value.split())
        if isinstance(value, list):
            return sorted(clean(x) for x in value)
        return value

    return digest({k: clean(v) for k, v in facts.items()})


def to_notify(conn, rows, min_score):
    return [
        row
        for row in rows
        if row["status"] == "open"
        and row["review"] != "rejected"
        and row.get("application_state") != "submitted"
        and row.get("score") is not None
        and row["score"] >= min_score
        and not conn.execute(
            "SELECT 1 FROM job_notifications WHERE job_id=? AND content_hash=?",
            (row["id"], content_version(row)),
        ).fetchone()
    ]


def digest_text(rows):
    lines = ["# New or changed matching jobs", "", "Review: uv run bio-jobs review", ""]
    for row in rows:
        # Plain Markdown; no employer HTML or executable markup is copied.
        title = " ".join(row["title"].split())
        employer = " ".join(row["employer"].split())
        lines += [
            f"- {row['id']}: {employer} — {title} ({row['score']}/100)",
            f"  Location: {'; '.join(row.get('locations') or [row['location'] or 'unknown'])}",
            *[
                f"  {origin['source_url']} | apply: {origin['apply_url']}"
                for origin in row["origins"]
            ],
            "",
        ]
    return "\n".join(lines)


def notify(title, text):
    if sys.platform != "darwin":
        return

    def safe(value):
        return " ".join(value.replace("\\", " ").replace('"', "'").split())[:200]

    result = subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{safe(text)}" with title "{safe(title)}" sound name "Glass"',
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError("macOS notification failed")


def desktop_digest(rows):
    if len(rows) > MAX_SEPARATE:
        top = rows[0]
        notify(
            f"{len(rows)} new or changed matching jobs",
            f"Top: {top['employer']} — {top['title']} ({top['score']}). bio-jobs review",
        )
    else:
        for row in rows:
            notify(
                f"{row['employer']}: {row['score']}",
                f"{row['title']} — {row['location'] or 'unknown'}",
            )


def telegram_digest(cfg, body):
    token = cfg.secret("BIO_TELEGRAM_BOT_TOKEN")
    chat = cfg.secret("BIO_TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise ValueError(
            "Telegram enabled: set BIO_TELEGRAM_BOT_TOKEN and BIO_TELEGRAM_CHAT_ID in .env"
        )
    # Telegram's text limit is 4096 characters. Plain text avoids markup injection.
    for offset in range(0, len(body), 4000):
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urlencode(
                {
                    "chat_id": chat,
                    "text": body[offset : offset + 4000],
                    "disable_web_page_preview": "true",
                }
            ).encode(),
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected digest")


def queue_alerts(cfg, conn, rows):
    """Atomically claim each version and retain its recoverable digest before side effects."""
    if not rows:
        return 0
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Recheck under the transaction even if a caller did not acquire the watch lock.
        rows = to_notify(
            conn,
            rows,
            cfg.watch.min_score if cfg.watch.min_score is not None else cfg.analyst.min_score,
        )
        if not rows:
            conn.commit()
            return 0
        now = utcnow()
        batch = conn.execute(
            "INSERT INTO alert_batches(created_at,body,jobs,desktop_requested,telegram_requested) VALUES (?,?,?,?,?)",
            (now, digest_text(rows), json.dumps(rows), cfg.watch.notify, cfg.watch.telegram),
        ).lastrowid
        conn.executemany(
            "INSERT INTO job_notifications(job_id,content_hash,notified_at,batch_id) VALUES (?,?,?,?)",
            [(row["id"], content_version(row), now, batch) for row in rows],
        )
        conn.commit()
        return len(rows)
    except BaseException:
        conn.rollback()
        raise


def deliver_pending(cfg, conn, output=print):
    """Export committed digests again after a crash; never repeat an uncertain external attempt."""
    directory = cfg.resolved(cfg.watch.digest_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from app.runlog import record

    errors = 0
    for batch in conn.execute("SELECT * FROM alert_batches ORDER BY id").fetchall():
        path = directory / f"{batch['id']:06d}.md"
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                handle.write(batch["body"])
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        for channel in ("desktop", "telegram"):
            if not batch[f"{channel}_requested"] or batch[f"{channel}_attempted_at"]:
                continue
            # Commit BEFORE invoking OS/network: interruption cannot produce duplicate alerts.
            changed = conn.execute(
                f"UPDATE alert_batches SET {channel}_attempted_at=? WHERE id=? AND {channel}_attempted_at IS NULL",
                (utcnow(), batch["id"]),
            ).rowcount
            conn.commit()
            if not changed:
                continue
            try:
                if channel == "desktop":
                    desktop_digest(json.loads(batch["jobs"]))
                elif cfg.watch.telegram:
                    telegram_digest(cfg, batch["body"])
            except Exception as exc:  # noqa: BLE001 - keep the local digest, never expose bot URLs.
                errors += 1
                record(
                    "notifications",
                    {"channel": channel, "result": "failed", "error_category": type(exc).__name__},
                )
                conn.execute(
                    f"UPDATE alert_batches SET {channel}_error=? WHERE id=?",
                    (type(exc).__name__, batch["id"]),
                )
                conn.commit()
                output(f"{channel} alert failed ({type(exc).__name__}); digest: {path}")
            else:
                result = (
                    "unsupported"
                    if channel == "desktop" and sys.platform != "darwin"
                    else "delivered"
                )
                record("notifications", {"channel": channel, "result": result})
    return errors


def run(cfg, conn, profile, sources, *, scheduled=False, output=print, **discovery_options):
    with run_lock(cfg.resolved(cfg.database_path)) as acquired:
        if not acquired:
            output("Another watch pass is running; skipped.")
            return {"skipped": True, "notified": 0}
        require_reviewed(cfg, profile)
        if cfg.watch.telegram and (
            not cfg.secret("BIO_TELEGRAM_BOT_TOKEN") or not cfg.secret("BIO_TELEGRAM_CHAT_ID")
        ):
            raise ValueError(
                "Set BIO_TELEGRAM_BOT_TOKEN and BIO_TELEGRAM_CHAT_ID before enabling watch.telegram"
            )
        from app.runlog import record

        for channel, enabled in (("desktop", cfg.watch.notify), ("telegram", cfg.watch.telegram)):
            record(
                "notifications",
                {"channel": channel, "result": "no_attempt" if enabled else "disabled"},
            )
        delivery_errors = deliver_pending(cfg, conn, output)
        results = discover(
            conn,
            profile,
            sources,
            timeout=cfg.http_timeout,
            user_agent=cfg.user_agent,
            **discovery_options,
        )
        scoring_error = None
        try:
            scored = analyst.analyze_new(cfg, conn, profile, sources)
        except RuntimeError as exc:
            scored, scoring_error = 0, str(exc)
            output(scoring_error)
        ranked = analyst.attach_evaluations(
            conn, cfg, profile, list_jobs(conn, profile, sources, cfg, prospects_only=True)
        )
        notified = queue_alerts(cfg, conn, ranked)
        delivery_errors += deliver_pending(cfg, conn, output)
        if scheduled and cfg.apply.mode == "auto" and not scoring_error:
            from app.autopilot import run as apply_run

            queue = analyst.attach_evaluations(
                conn, cfg, profile, list_jobs(conn, profile, sources, cfg)
            )
            apply_run(cfg, conn, profile, queue, output=output, unattended=True)
        errors = sum(r["status"] in ("error", "partial") for r in results)
        output(
            f"Watch: {len(results)} sources, {errors} source errors/partial; scored {scored}; "
            f"{notified} new/changed alerts; digests: {cfg.resolved(cfg.watch.digest_dir)}"
        )
        return {
            "sources": results,
            "scored": scored,
            "notified": notified,
            "errors": errors + delivery_errors + bool(scoring_error),
        }

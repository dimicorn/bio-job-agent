"""One bounded pass over explicitly approved jobs. No scheduler or notifications."""

import fcntl
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError

from app import answers, applications, materials
from app.applier import FormStop, PartialPlanStop, applier_for
from app.private import private_dir, private_file, private_open
from app.resume import digest


def event(conn, job_id, attempt_id, action, **detail):
    conn.execute(
        "INSERT INTO application_events(job_id,attempt_id,action,detail) VALUES (?,?,?,?)",
        (job_id, attempt_id, action, json.dumps(detail)),
    )
    conn.commit()


def reserve(cfg, conn, profile, row, mode, used=0, now=None, *, unattended=None):
    if unattended is None:
        unattended = mode == "auto"
    """Check integrity before locking; atomically recheck eligibility and reserve a slot."""
    approved = conn.execute(
        "SELECT * FROM application_approvals WHERE job_id=?", (row["id"],)
    ).fetchone()
    if not approved:
        raise ValueError("Materials changed or approval missing; review and approve again")
    _, ids = applications.validate_materials(
        cfg, conn, profile, row, acknowledged_warnings=json.loads(approved["acknowledged_warnings"])
    )
    if json.loads(approved["material_ids"]) != ids:
        raise ValueError("Materials changed or approval missing; review and approve again")
    bank = json.loads(approved["answers_content"])
    if approved["answers_hash"] != digest(answers.load_answers(cfg)):
        raise ValueError("answers.yaml changed after approval; review and approve again")
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        locked_approval = conn.execute(
            "SELECT * FROM application_approvals WHERE job_id=?", (row["id"],)
        ).fetchone()
        if not locked_approval or dict(locked_approval) != dict(approved):
            raise ValueError("Approval changed during execution; review again")
        current = conn.execute(
            "SELECT state FROM applications WHERE job_id=?", (row["id"],)
        ).fetchone()
        if not current or current[0] != "approved":
            raise ValueError("Only approved jobs can be applied")
        if conn.execute(
            "SELECT 1 FROM application_attempts WHERE job_id=? AND "
            "(submission_possible=1 OR status='started')",
            (row["id"],),
        ).fetchone():
            raise ValueError("A prior attempt may have submitted; never retry automatically")
        applications.check_gates(cfg, conn, row, used=used, now=now, unattended=unattended)
        applications.check_company(cfg, conn, row, now=now)
        attempt = conn.execute(
            "INSERT INTO application_attempts(job_id,mode,started_at) VALUES (?,?,?)",
            (row["id"], mode, (now or datetime.now().astimezone()).isoformat()),
        ).lastrowid
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    event(conn, row["id"], attempt, "reserved", mode=mode, materials=ids)
    return attempt, bank, ids


def arm(cfg, conn, row, attempt, mode, now=None):
    # Serialize the final employer check and submission intent across processes.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        applications.check_company(cfg, conn, row, now=now)
        conn.execute("UPDATE application_attempts SET submission_possible=1 WHERE id=?", (attempt,))
        materials.state(conn, row["id"], "submitting", commit=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    from app.runlog import record

    record("applications", {"id": row["id"], "from": "approved", "to": "submitting"})
    event(
        conn, row["id"], attempt, "human_submit_handoff" if mode == "assisted" else "submit_intent"
    )


def finish(conn, row, attempt, status, reason):
    conn.execute("UPDATE application_attempts SET status=? WHERE id=?", (status, attempt))
    conn.commit()
    previous = conn.execute(
        "SELECT state FROM applications WHERE job_id=?", (row["id"],)
    ).fetchone()
    materials.state(conn, row["id"], status)
    from app.runlog import record

    record(
        "applications", {"id": row["id"], "from": previous[0] if previous else None, "to": status}
    )
    row["application_state"] = status
    event(conn, row["id"], attempt, "result", status=status, reason=reason)
    return status


def values_for(conn, profile, ids):
    from app.candidate.profile import require_name

    require_name(profile)
    versions = {
        kind: dict(
            conn.execute("SELECT * FROM material_versions WHERE id=?", (version,)).fetchone()
        )
        for kind, version in ids.items()
    }
    cv = versions["resume"]["content"]
    # Contact values must already exist in the approved resume or explicit answer bank.
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", cv))
    phones = re.findall(
        r"(?m)^\s*(?:Phone|Tel|Mobile)\s*:\s*([+\d][\d ()+.-]{6,})\s*$", cv, re.IGNORECASE
    )
    names = profile.name.split()
    return {
        "first_name": profile.first_name or (names[0] if names else ""),
        "last_name": profile.last_name or " ".join(names[1:]),
        "full_name": profile.name,
        "email": profile.email or (next(iter(emails)) if len(emails) == 1 else ""),
        "phone": profile.phone or (phones[0].strip() if len(phones) == 1 else ""),
        "city": profile.city,
        "linkedin": profile.linkedin,
        "github": profile.github,
        "website": profile.website,
        "resume": versions["resume"]["pdf_path"],
        "letter": versions["letter"]["content"],
    }, versions


def screenshot(page, cfg, row, attempt, name, log):
    directory = cfg.resolved(cfg.apply.audit_dir)
    private_dir(directory)
    path = directory / f"{row['id']}-{attempt}-{name}.png"
    private_file(path)
    page.screenshot(path=str(path), full_page=True)
    log("screenshot", stage=name, path=str(path))
    return path


def apply_one(cfg, conn, profile, row, page, *, used=0, now=None, output=print, unattended=None):
    mode = cfg.apply.mode
    if mode not in ("assisted", "auto"):
        raise ValueError("Browser application requires assisted or auto mode")
    if unattended is None:
        unattended = mode == "auto"
    attempt, bank, ids = reserve(cfg, conn, profile, row, mode, used, now, unattended=unattended)
    log = lambda action, **detail: event(conn, row["id"], attempt, action, **detail)
    possible = False
    page_factory = page if callable(page) else None
    if page_factory:
        page = None
    try:
        if page_factory:
            page = page_factory()
        applier = applier_for(row["apply_url"])(page, cfg, log)
        applier.open(row["apply_url"])
        if applier.confirmed():
            raise FormStop(
                "Acknowledgement already present before this attempt; reconcile manually"
            )
        fields = applier.fields()
        values, versions = values_for(conn, profile, ids)
        # A cover letter upload uses a PDF derived only from the pinned approved text.
        from app.applier import standard_key

        if any(f["kind"] == "file" and standard_key(f) == "letter" for f in fields):
            directory = cfg.resolved(cfg.apply.audit_dir)
            private_dir(directory)
            pdf = directory / f"letter-{versions['letter']['id']}.pdf"
            with ThreadPoolExecutor(1) as pool:
                pool.submit(materials.render_pdf, values["letter"], pdf).result()
            values["letter_file"] = str(pdf)

        def queue(label, options, required):
            answers.add_question(conn, row["id"], label, options, required)
            log("question_discovered", question=label, required=required, options=options)

        deferred = None
        try:
            steps = applier.plan(
                fields,
                values,
                bank,
                row["employer"],
                queue,
                {
                    "resume": versions["resume"]["content"],
                    "facts": [f.model_dump() for f in profile.facts],
                },
            )
        except PartialPlanStop as exc:
            steps, deferred = exc.steps, exc
            for reason in exc.reasons:
                output(f"  Left for person: {reason}")
        if not any(s.source == "resume" for s in steps):
            raise FormStop("Resume upload field not found; inspect the form manually")
        for step in steps:
            output(
                f"  {step.field['label']}: {', '.join(step.values) if step.field['kind'] != 'file' else Path(step.values[0]).name} [{step.source}]"
            )
            if step.source == "llm":
                queue(step.field["label"], step.field["options"], step.field["required"])
        if deferred:
            for step in sorted(steps, key=lambda item: item.field["kind"] != "file"):
                applier.check_safety()
                applier.fill_one(step)
            for step in steps:
                applier.verify_one(step)
            output(str(deferred) + "; leave these questions for the person in the browser.")
            raise deferred
        applier.fill(steps)
        screenshot(page, cfg, row, attempt, "filled", log)
        # Re-read immediately before handoff/click, after asynchronous file parsing and screenshots.
        applier.verify(steps)
        for kind, version in versions.items():
            latest = materials.latest(conn, row["id"], kind)
            if (
                latest["id"] != version["id"]
                or Path(version["path"]).read_text() != version["content"]
            ):
                raise FormStop(
                    f"{kind} file changed while filling; run bio-jobs import {row['id']} --kind {kind} and review again"
                )
        cv = versions["resume"]
        if digest(Path(cv["pdf_path"]).read_bytes().hex()) != cv["pdf_hash"]:
            raise FormStop("Approved resume PDF changed while filling")
        if digest(answers.load_answers(cfg)) != digest(bank):
            raise FormStop("Approved answers changed while filling")
        current = conn.execute(
            "SELECT state FROM applications WHERE job_id=?", (row["id"],)
        ).fetchone()
        if current[0] != "approved":
            raise FormStop("Approval was withdrawn while filling")
        applications.check_gates(
            cfg, conn, row, used=used, now=now, exclude_attempt=attempt, unattended=unattended
        )
        applications.check_company(cfg, conn, row, now=now)
        button = applier.submit_button()
        arm(cfg, conn, row, attempt, mode, now)
        possible = True
        if mode == "assisted":
            page.bring_to_front()
            output(
                f"{row['id']}: Form filled and verified. Review it and press Submit in the "
                f"browser; waiting up to {cfg.apply.submit_wait_minutes:g} minutes."
            )
            seconds = cfg.apply.submit_wait_minutes * 60
        else:
            button.click()
            log("submit_click")
            seconds = cfg.apply.confirmation_timeout_seconds
        log("wait_confirmation", timeout_seconds=seconds)
        try:
            proof = applier.wait_confirmation(seconds)
        except FormStop as exc:
            if mode != "auto" or not applier.visible_challenge():
                raise
            from app.watch import notify

            log("challenge_handoff", reason=str(exc))
            output(
                "A CAPTCHA challenge appeared. Complete it in the browser; waiting for confirmation."
            )
            try:
                notify(
                    "Application needs help", "Complete the CAPTCHA challenge in the open browser."
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as notification_error:
                log("notification_failed", error=type(notification_error).__name__)
            page.bring_to_front()
            proof = applier.wait_confirmation(
                cfg.apply.submit_wait_minutes * 60, allow_challenge=True
            )
        if not proof:
            return finish(
                conn,
                row,
                attempt,
                "unknown_result",
                "No confirmation before timeout; reconcile manually, never retry",
            )
        log("confirmation", **proof)
        try:
            screenshot(page, cfg, row, attempt, "confirmation", log)
        except (PlaywrightError, OSError) as exc:
            log("screenshot_failed", stage="confirmation", error=type(exc).__name__)
        if any(step.source == "llm" for step in steps):
            output("To save accepted LLM answers, use bio-jobs answers and enter them explicitly.")
        return finish(conn, row, attempt, "submitted", proof)
    except FormStop as exc:
        # Challenges always require a person, including when they appear after Submit.
        log("safety_stop", reason=str(exc), submission_possible=possible)
        if mode == "auto" and "CAPTCHA challenge" in str(exc):
            from app.watch import notify

            try:
                notify(
                    "Application needs help",
                    "A CAPTCHA challenge needs your attention in the browser.",
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as notification_error:
                log("notification_failed", error=type(notification_error).__name__)
        status = "manual_required"
        reason = str(exc)
    except BaseException as exc:
        status = "unknown_result" if possible else "manual_required"
        reason = f"{type(exc).__name__}: browser/material action failed; inspect audit events"
        log("error", reason=reason, detail=str(exc)[:1000], submission_possible=possible)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            finish(conn, row, attempt, status, reason)
            raise
    try:
        if page is not None:
            screenshot(page, cfg, row, attempt, "stopped", log)
    except (PlaywrightError, OSError) as exc:
        log("screenshot_failed", stage="stopped", error=type(exc).__name__)
    output(f"{row['id']}: {status}: {reason}. Browser left for manual handling.")
    return finish(conn, row, attempt, status, reason)


class Browser:
    """An ordinary persistent Chromium; disconnect leaves handoff windows open.

    Chromium is an independent local process so an assisted timeout does not close the form.
    The loopback CDP endpoint is used only by Playwright, with no stealth flags/plugins.
    """

    def __init__(self, cfg):
        self.cfg, self.pw, self.browser, self.lock = cfg, None, None, None
        self.reused, self.tab = False, None

    def page(self):
        from playwright.sync_api import sync_playwright

        profile = self.cfg.resolved(self.cfg.apply.profile_dir)
        private_dir(profile)
        self.lock = private_open(profile / ".bio-apply.lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            self.lock = None
            raise ValueError("Another apply process is using this browser profile") from exc
        self.pw = sync_playwright().start()
        port_file = profile / "DevToolsActivePort"

        def endpoint():
            try:
                port = int(port_file.read_text().splitlines()[0])
                if not 1 <= port <= 65535:
                    return None
                with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                    data = json.load(response)
                return data.get("webSocketDebuggerUrl")
            except (OSError, ValueError, URLError, IndexError):
                return None

        address = endpoint()
        self.reused = bool(address)
        if not address:
            port_file.unlink(missing_ok=True)
            executable = self.pw.chromium.executable_path
            if not Path(executable).exists():
                raise ValueError("Install Chromium: uv run playwright install chromium")
            with private_open(profile / "browser.log", "ab") as log:
                subprocess.Popen(
                    [
                        executable,
                        f"--user-data-dir={profile}",
                        "--remote-debugging-port=0",
                        "--remote-debugging-address=127.0.0.1",
                        "about:blank",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 20
            while not address and time.monotonic() < deadline:
                time.sleep(0.1)
                address = endpoint()
            if not address:
                raise ValueError("Chromium did not open; inspect browser.log in apply.profile_dir")
        self.browser = self.pw.chromium.connect_over_cdp(address)
        context = self.browser.contexts[0]
        # Never navigate a previous handoff tab away. Every attempt has a separate tab.
        self.tab = context.new_page()
        return self.tab

    def close(self, *, leave_open=False):
        try:
            if self.browser and not leave_open:
                if self.reused:
                    if self.tab and not self.tab.is_closed():
                        self.tab.close()
                else:
                    self.browser.close()
        finally:
            if self.pw:
                self.pw.stop()
            if self.lock:
                self.lock.close()
            self.pw = self.browser = self.lock = None


def run(
    cfg, conn, profile, rows, *, job=None, output=print, browser_factory=Browser, unattended=False
):
    """Only the approved queue is consumed; discovery/preparation never imply approval."""
    if job and not any(r["id"] == job for r in rows):
        raise ValueError("Unknown or ineligible job ID")
    selected = [
        r
        for r in rows
        if (not job or r["id"] == job)
        and conn.execute(
            "SELECT 1 FROM applications WHERE job_id=? AND state='approved'", (r["id"],)
        ).fetchone()
    ]
    if job:
        row = next(r for r in rows if r["id"] == job)
        if row.get("score") is None or row["score"] < max(
            cfg.apply.min_score, cfg.analyst.min_score
        ):
            raise ValueError(applications.score_gate_message(cfg, row, apply=True))
    if job and not selected:
        raise ValueError("Approve the job in review before applying")
    from app.pipeline.normalize import key

    selected.sort(key=lambda r: r.get("score") if r.get("score") is not None else -1, reverse=True)
    outcomes = []
    employers = set()
    try:
        for row in selected:
            if len(outcomes) >= cfg.apply.max_per_run:
                break
            employer = key(row["employer"])
            if employer in employers:
                continue
            try:
                if row.get("status", "open") != "open":
                    raise ValueError(
                        f"Job {row['id']} is closed; verify it manually before applying"
                    )
                applications.check_company(cfg, conn, row)
                applications.check_gates(cfg, conn, row, used=len(outcomes), unattended=unattended)
            except ValueError as exc:
                if job:
                    raise
                output(str(exc))
                continue
            employers.add(employer)
            if cfg.apply.mode == "manual":
                import webbrowser

                applications.check_gates(cfg, conn, row, used=len(outcomes), unattended=unattended)
                approved = conn.execute(
                    "SELECT * FROM application_approvals WHERE job_id=?", (row["id"],)
                ).fetchone()
                if not approved:
                    raise ValueError("Approval missing; review and approve again")
                _, ids = applications.validate_materials(
                    cfg,
                    conn,
                    profile,
                    row,
                    acknowledged_warnings=json.loads(approved["acknowledged_warnings"]),
                )
                if ids != json.loads(approved["material_ids"]) or approved[
                    "answers_hash"
                ] != digest(answers.load_answers(cfg)):
                    raise ValueError("Materials or answers changed; review and approve again")
                event(conn, row["id"], None, "manual_open", url=row["apply_url"])
                webbrowser.open(row["apply_url"])
                output(
                    f"{row['id']}: Complete the application yourself; use review → t to confirm."
                )
                outcomes.append("manual")
                continue
            browser = browser_factory(cfg)
            status = None
            try:
                status = apply_one(
                    cfg,
                    conn,
                    profile,
                    row,
                    browser.page,
                    used=len(outcomes),
                    output=output,
                    unattended=unattended,
                )
                outcomes.append(status)
            except ValueError as exc:
                event(conn, row["id"], None, "execution_rejected", reason=str(exc))
                if job:
                    raise
                output(str(exc))
                break
            finally:
                browser.close(leave_open=status != "submitted")
            output(f"{row['id']}: {status}")
            if status != "submitted":
                # Preserve the current form for a person; do not open more application windows.
                break
    except KeyboardInterrupt:
        from app import stop

        states = {
            row["id"]: conn.execute(
                "SELECT state FROM applications WHERE job_id=?", (row["id"],)
            ).fetchone()[0]
            for row in selected
        }
        done = [job_id for job_id, state in states.items() if state == "submitted"]
        unfinished = {job_id: state for job_id, state in states.items() if state != "submitted"}
        output(
            f"Interrupted apply: confirmed {done}; unfinished {unfinished}; "
            f"exit code {stop.exit_code()}. Reconcile uncertain submissions manually."
        )
        raise

    output(
        f"Apply pass: {len(outcomes)} attempted, {outcomes.count('submitted')} confirmed submitted."
    )
    return outcomes

import io
import re
import threading

from app import console as shared
from app.console import Line, blocked, fail, line, ok, run_summary, warn


def capture(monkeypatch, *, terminal=False):
    file = io.StringIO()
    target = shared._Console(file=file, width=120, force_terminal=terminal)
    monkeypatch.setattr(shared, "console", target)
    return target, file


def test_lines_are_strings_with_an_outcome_kind():
    assert ok("done") == "done" and ok("done").kind == "ok"
    assert [warn("w").kind, blocked("b").kind, fail("f").kind] == ["warn", "blocked", "fail"]
    assert Line("plain").kind == "info" and isinstance(fail("x"), str)


def test_progress_line_is_timestamped_with_symbol(monkeypatch):
    _, file = capture(monkeypatch)
    shared.log(ok("Scored 2 jobs"))
    shared.log("Plain progress")
    first, second = file.getvalue().splitlines()
    assert re.fullmatch(r"\d\d:\d\d:\d\d ✓ Scored 2 jobs", first)
    assert re.fullmatch(r"\d\d:\d\d:\d\d Plain progress", second)


def test_untrusted_text_never_becomes_markup_or_escapes(monkeypatch):
    target, file = capture(monkeypatch, terminal=True)
    shared.log(fail("[bold red]Acme[/] \x1b[2J\x1b]8;;http://x\x07Lab"))
    target.print(shared.text("[link=http://evil]Job[/link]"))
    output = file.getvalue()
    assert "[bold red]Acme[/]" in output and "[link=http://evil]Job[/link]" in output
    assert "\x1b[2J" not in output and "\x1b]8" not in output
    assert "\x1b[" in output  # the console itself still styles the line in a terminal


def test_no_color_and_redirected_output_are_plain(monkeypatch):
    _, file = capture(monkeypatch)
    shared.log(fail("failed"))
    shared.header("Sync")
    assert "\x1b" not in file.getvalue() and "Sync" in file.getvalue()
    monkeypatch.setenv("NO_COLOR", "1")
    target, file = capture(monkeypatch, terminal=True)
    shared.log(ok("fine"))
    target.rule("Score")
    assert "\x1b" not in file.getvalue() and "✓ fine" in file.getvalue()


def test_worker_lines_carry_thread_name(monkeypatch):
    _, file = capture(monkeypatch)
    thread = threading.Thread(target=lambda: shared.log("batch done"), name="score_0")
    thread.start()
    thread.join()
    assert re.fullmatch(r"\d\d:\d\d:\d\d score_0 batch done\n", file.getvalue())


def test_sequential_blocks_do_not_interleave(monkeypatch):
    target, file = capture(monkeypatch)
    second_done = threading.Event()

    def work(name, lines, after=None):
        def body():
            if after:
                after.wait()
            for text in lines:
                shared.log(text)
            if name == "b":
                second_done.set()

        return threading.Thread(target=body, name=name)

    with target.sequential([("a", "First"), ("b", "Second")], lambda name: name):
        b = work("b", ["b1", "b2"])
        b.start()
        b.join()  # b finished first, but its block waits for a
        a = work("a", ["a1", "a2"])
        a.start()
        a.join()
        target.section_done("a")
    lines = [
        re.sub(r"^\d\d:\d\d:\d\d ", "", item).strip("─ ") for item in file.getvalue().splitlines()
    ]
    assert lines == ["First", "a a1", "a a2", "Second", "b b1", "b b2"]


def test_run_summary_counts_without_personal_data():
    record = {
        "sources": [{"id": "s1", "status": "ok"}, {"id": "s2", "status": "error"}],
        "jobs": [{"id": 1, "change": "new"}, {"id": 2, "change": "new"}],
        "scoring": [{"id": 1, "status": "scored"}, {"id": 2, "status": "failed"}],
        "applications": [
            {"id": 1, "from": "approved", "to": "submitting"},
            {"id": 1, "from": "submitting", "to": "submitted"},
            {"id": 2, "from": "approved", "to": "manual_required"},
        ],
        "notifications": [
            {"channel": "desktop", "result": "no_attempt"},
            {"channel": "desktop", "result": "delivered"},
        ],
    }
    assert run_summary(record) == [
        ("2 sources (1 error, 1 ok)", "warn"),
        ("jobs 2 new", ""),
        ("scored 1, 1 failed", "fail"),
        ("1 manual_required", "blocked"),
        ("1 submitted", "ok"),
        ("desktop alert delivered", "ok"),
    ]
    assert run_summary({}) == [("nothing to report", "detail")]


def test_line_falls_back_to_info_for_plain_strings():
    assert line("hello").plain.endswith(" hello")


def test_only_the_timestamp_is_dim():
    styles = {str(span.style) for span in line(ok("done")).spans}
    assert line(ok("done")).style == "" and "dim" in styles and "green" in styles

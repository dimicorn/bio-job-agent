import os
import signal
import threading

import pytest

from app import stop


def test_cooperative_pool_drains_running_and_leaves_rest(monkeypatch, capsys):
    started = threading.Event()
    release = threading.Event()

    def work(item):
        started.set()
        release.wait(5)
        return item

    def interrupt():
        assert started.wait(5)
        stop._handler(signal.SIGTERM, None)
        release.set()

    with stop.installed():
        thread = threading.Thread(target=interrupt)
        thread.start()
        results = [
            (batch, future.result())
            for batch, future in stop.completed_batches(
                [1, 2, 3], 1, lambda executor, batch: executor.submit(work, batch)
            )
        ]
        thread.join()
        assert results == [(1, 1)]
        with pytest.raises(KeyboardInterrupt):
            stop.interrupted_summary("test", [1], [1, 2, 3])
        assert stop.exit_code() == 143
    assert "unfinished [2, 3]" in capsys.readouterr().err


def test_second_signal_exits_immediately(monkeypatch):
    exits = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    with stop.installed():
        with pytest.raises(KeyboardInterrupt):
            stop._handler(signal.SIGINT, None)
        # Real os._exit never returns.
        with pytest.raises(KeyboardInterrupt):
            stop._handler(signal.SIGINT, None)
    assert exits == [130]


def test_caffeinate_is_process_bound_and_cleaned_up(monkeypatch):
    calls = []

    class Process:
        def terminate(self):
            calls.append("terminate")

        def wait(self):
            calls.append("wait")

    monkeypatch.setattr(stop.sys, "platform", "darwin")
    monkeypatch.setattr(
        stop.subprocess, "Popen", lambda args, **kw: calls.append(args) or Process()
    )
    with stop.prevent_sleep():
        assert calls[0] == ["caffeinate", "-i", "-w", str(os.getpid())]
    assert calls[1:] == ["terminate", "wait"]

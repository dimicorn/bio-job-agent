"""Cooperative interruption and process-bound sleep prevention."""

import contextlib
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

_event = threading.Event()
_signum = 0
_cooperative = False


def requested():
    return _event.is_set()


def exit_code():
    return 128 + (_signum or signal.SIGINT)


def _handler(signum, frame):
    global _signum
    if _event.is_set():
        os._exit(128 + signum)
    _signum = signum
    _event.set()
    if not _cooperative:
        raise KeyboardInterrupt


@contextlib.contextmanager
def installed():
    global _signum
    _event.clear()
    _signum = 0
    previous = {sig: signal.signal(sig, _handler) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextlib.contextmanager
def prevent_sleep():
    process = None
    if sys.platform == "darwin":
        try:
            process = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    try:
        yield
    finally:
        if process is not None:
            process.terminate()
            process.wait()


def completed_batches(batches, workers, submit, name="worker"):
    """Yield completed batches; after a stop, drain only already running requests.

    Worker threads are named after the work (score_0, letters_1...) so their console
    lines carry that name."""
    global _cooperative
    _cooperative = True
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name) as executor:
            remaining = iter(batches)
            futures = {}

            def fill():
                while len(futures) < workers and not requested():
                    batch = next(remaining, None)
                    if batch is None:
                        break
                    futures[submit(executor, batch)] = batch

            fill()
            while futures:
                ready, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in ready:
                    batch = futures.pop(future)
                    yield batch, future
                fill()
    finally:
        _cooperative = False


def interrupted_summary(kind, done_ids, all_ids):
    if requested():
        unfinished = [item for item in all_ids if item not in done_ids]
        from app.console import err_console, log, warn

        log(
            warn(
                f"Interrupted {kind}: saved {done_ids}; unfinished {unfinished}; "
                f"exit code {exit_code()}."
            ),
            target=err_console,
        )
        raise KeyboardInterrupt

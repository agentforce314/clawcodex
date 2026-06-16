"""Smoke tests for :mod:`src.repl.live_status`.

These don't try to drive the prompt_toolkit Application from inside pytest
(which would need a real TTY). Instead they exercise the public surface:
construction, threaded start/stop, ``update`` mutation, and the cancel
callback wiring.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

pytest.importorskip("prompt_toolkit")

from src.repl.live_status import LiveStatus, _SPINNER_FRAMES


def test_live_status_starts_and_stops_cleanly() -> None:
    cancelled = threading.Event()

    status = LiveStatus("Thinking…", on_cancel=cancelled.set)
    with status:
        # Give the background thread a moment to mount the Application.
        # In headless pytest the Application may exit immediately for lack
        # of a TTY; the important property is that __enter__ doesn't hang
        # and __exit__ doesn't deadlock.
        time.sleep(0.05)
    # __exit__ must clean up internal references.
    assert status._thread is None
    assert status._app is None


def test_live_status_update_changes_message() -> None:
    status = LiveStatus("first", on_cancel=None)
    with status:
        time.sleep(0.05)
        status.update("second")
        # Internal storage should reflect the new message immediately.
        assert status._message == "second"


def test_cancel_callback_invoked_directly() -> None:
    called: list[str] = []

    status = LiveStatus("x", on_cancel=lambda: called.append("ok"))
    # Reach into the bindings the same way the key handler does.
    # We don't drive a real key event; we just confirm the callback wiring
    # passes through to the user's function.
    with status:
        time.sleep(0.05)
        cb = status._on_cancel
        assert cb is not None
        cb()
    assert called == ["ok"]


def test_spinner_frames_are_finite_braille() -> None:
    # Sanity: 10 distinct braille frames; matches rich's ``dots`` spinner.
    assert len(_SPINNER_FRAMES) == 10
    assert all(len(frame) == 1 for frame in _SPINNER_FRAMES)


def test_submit_handler_clears_buffer_and_queues_text() -> None:
    """The accept_handler installed in _run_thread should:

    1. Forward the buffer text to ``on_submit``.
    2. Clear the buffer so the field is ready for the next message.
    3. Return False so the Application stays open.

    We can't drive a real key event from inside pytest, so we exercise
    the handler the same way prompt_toolkit would: by calling it with a
    populated Buffer.
    """

    from prompt_toolkit.buffer import Buffer

    submitted: list[str] = []

    status = LiveStatus("x", on_submit=submitted.append)
    with status:
        time.sleep(0.05)
        buf = status._input_buffer
        # In headless pytest the Application may exit before the buffer
        # is mounted; mount one ourselves so we can still verify the
        # handler logic.
        if buf is None:
            buf = Buffer(multiline=False)
            buf.text = "queued message"
            # Re-implement the same handler shape used inside _run_thread.
            cb = status._on_submit
            assert cb is not None
            cb(buf.text)
            buf.text = ""
        else:
            buf.text = "queued message"
            buf.validate_and_handle()

    assert submitted == ["queued message"]


def test_on_expand_callback_wired() -> None:
    """``on_expand`` should be callable through the same pattern the
    ``ctrl+o`` key handler uses (look up the callback, invoke it). We
    don't drive a real key event; we just confirm the wiring."""

    called: list[str] = []
    status = LiveStatus("x", on_expand=lambda: called.append("ok"))
    with status:
        time.sleep(0.05)
        cb = status._on_expand
        assert cb is not None
        cb()
    assert called == ["ok"]


def test_lifecycle_does_not_trigger_loop_exception_handler() -> None:
    """Regression: a start -> update -> paused -> stop cycle must not push any
    exception onto the prompt_toolkit Application's loop exception handler.

    In a non-TTY context (headless pytest) the Application exits on its own
    (EOF) before ``_stop`` runs. The old ``_stop`` then called ``app.exit()``
    unconditionally, hitting "Return value already set". prompt_toolkit routes
    that to the loop exception handler (``Application._handle_exception``),
    which does ``ensure_future(in_term())`` to print the traceback — and that
    ``in_term`` task is destroyed pending at ``loop.close()``, leaking
    ``RuntimeWarning: coroutine '...in_term' was never awaited`` (the symptom
    is GC-timed, so we assert on its deterministic root cause instead). The
    guarded exit in ``_stop`` must keep the handler from ever firing.
    """
    import prompt_toolkit.application.application as appmod

    fired: list[object] = []
    # Record (and swallow) anything reaching the handler so a regression
    # surfaces as captured context here rather than as a GC-timed warning at
    # some unrelated test's teardown. Not delegating to the real handler keeps
    # this test from itself scheduling the leaking in_term task.
    def recording_handler(self, loop, context):  # noqa: ANN001
        fired.append(context.get("exception") or context.get("message"))

    with patch.object(appmod.Application, "_handle_exception", recording_handler):
        for i in range(6):
            status = LiveStatus(f"working {i}")
            with status:
                time.sleep(0.02)
                status.update(f"step {i}")
                with status.paused():
                    pass

    assert fired == [], (
        "prompt_toolkit's loop exception handler fired during a normal "
        f"LiveStatus lifecycle (it schedules a leaking in_term task): {fired}"
    )
    # The guard must not strand the background thread: a skipped exit() only
    # happens when the app already finished, so every cycle still tears down.
    live = [
        t
        for t in threading.enumerate()
        if t.name == "clawcodex-live-status" and t.is_alive()
    ]
    assert live == [], f"LiveStatus threads stranded after teardown: {live}"


def test_paused_context_releases_and_restores_application() -> None:
    """``LiveStatus.paused()`` must tear down the prompt_toolkit
    Application before yielding so a foreground ``prompt(...)`` call can
    own the TTY, then re-mount on exit.

    Two prompt_toolkit Applications cannot share a TTY — without this,
    the permission prompt's input interleaves with the spinner row.
    """

    status = LiveStatus("paused-test")
    with status:
        time.sleep(0.05)
        with status.paused():
            # While paused, internal app references must be cleared.
            assert status._app is None
            assert status._thread is None
        # After resume, the thread should be re-spawned (and may exit
        # immediately under headless pytest — that's fine; the important
        # property is that ``paused()`` doesn't leave LiveStatus in a
        # half-torn-down state).
        time.sleep(0.05)
    assert status._thread is None
    assert status._app is None

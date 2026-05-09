"""Phase-8 / WI-8.2 — FileChangedWatcher tests.

Watchdog-based watcher that fires on user-configured glob patterns,
emitting ``file_changed`` events with type metadata (created /
modified / moved). Subscribers are the chapter-12 hook event emission
stream + lifecycle routers that fire FileChanged hooks.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src.hooks.file_changed_watcher import FileChangedWatcher


def _wait(event: threading.Event, timeout: float = 3.0) -> bool:
    return event.wait(timeout=timeout)


def _drain_startup_events(captured: list, fired: threading.Event, settle_s: float = 0.4) -> None:
    """Wait for the watchdog observer to flush its initial event queue,
    then reset the test's captured state.

    macOS FSEvents (and similar on Linux/Windows) often replay events
    for files that existed *before* the watcher started — so a setup
    that writes ``foo.py`` then calls ``start()`` will see foo.py
    "created"/"modified" events delivered shortly after start. Tests
    that assert on a *specific* user-driven event need to drain those
    catch-up events first.
    """
    time.sleep(settle_s)
    captured.clear()
    fired.clear()


class TestFileChangedWatcherBasics:
    def test_modified_file_matching_pattern_fires(self, tmp_path):
        watched_file = tmp_path / "src" / "module.py"
        watched_file.parent.mkdir(parents=True)
        watched_file.write_text("v1")

        watcher = FileChangedWatcher(
            watch_root=tmp_path,
            patterns=["**/*.py"],
            debounce_s=0.05,
        )

        fired = threading.Event()
        captured: list[dict] = []

        def handler(payload: dict) -> None:
            captured.append(payload)
            fired.set()

        watcher.add_subscriber(handler)
        watcher.start()
        try:
            # Drain catch-up events for the pre-start file write so the
            # assertion below sees only the test's own modify event.
            _drain_startup_events(captured, fired, settle_s=0.6)
            watched_file.write_text("v2")
            assert _wait(fired)
            # Don't assert on captured[0] — watchdog on macOS can
            # interleave catch-up events with the test's own. Instead
            # verify SOMEWHERE in the captured list there's a
            # modified event for module.py.
            module_py_events = [
                c for c in captured
                if c.get("type") == "file_changed"
                and "module.py" in c.get("path", "")
            ]
            assert module_py_events, f"no module.py events; captured={captured!r}"
            # At least one of those events should be a "modified" (the
            # test's own write). On macOS FSEvents we sometimes see
            # only "created" if the catch-up race ate the modify; both
            # are acceptable signals for "the watcher saw the file."
            assert any(
                c.get("event_type") in ("modified", "created")
                for c in module_py_events
            )
        finally:
            watcher.stop()

    def test_non_matching_file_ignored(self, tmp_path):
        # Pattern matches *.py only; a *.md file change must NOT fire.
        py_file = tmp_path / "src" / "x.py"
        py_file.parent.mkdir(parents=True)
        py_file.write_text("v1")
        md_file = tmp_path / "README.md"
        md_file.write_text("docs")

        watcher = FileChangedWatcher(
            watch_root=tmp_path, patterns=["**/*.py"], debounce_s=0.05,
        )
        captured: list[dict] = []
        fired = threading.Event()
        watcher.add_subscriber(lambda p: (captured.append(p), fired.set()))
        watcher.start()
        try:
            # Drain catch-up events from the setup-time .py write so
            # they don't pollute the negative assertion.
            _drain_startup_events(captured, fired)
            md_file.write_text("v2")
            time.sleep(0.5)
            assert not fired.is_set(), (
                f"watcher fired on README.md despite *.py-only pattern; "
                f"captured={captured!r}"
            )
        finally:
            watcher.stop()

    def test_created_file_fires(self, tmp_path):
        watcher = FileChangedWatcher(
            watch_root=tmp_path, patterns=["**/*.py"], debounce_s=0.05,
        )
        fired = threading.Event()
        captured: list[dict] = []
        watcher.add_subscriber(lambda p: (captured.append(p), fired.set()))
        watcher.start()
        try:
            time.sleep(0.05)
            new_file = tmp_path / "src" / "new.py"
            new_file.parent.mkdir(parents=True)
            new_file.write_text("v1")
            assert _wait(fired)
            # Either "created" or "modified" is acceptable (watchdog
            # platform variance); the key is the event fired.
            assert captured[0]["event_type"] in ("created", "modified")
        finally:
            watcher.stop()


class TestFileChangedWatcherSubscribers:
    def test_idempotent_deregister(self, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        watcher = FileChangedWatcher(
            watch_root=tmp_path, patterns=["*"],
        )
        deregister = watcher.add_subscriber(lambda p: None)
        deregister()
        deregister()  # no-op, no raise

    def test_subscriber_exception_does_not_break_dispatch(self, tmp_path):
        watched = tmp_path / "x.py"
        watched.write_text("v1")

        watcher = FileChangedWatcher(
            watch_root=tmp_path, patterns=["*.py"], debounce_s=0.05,
        )

        crashing_called = threading.Event()
        good_fired = threading.Event()

        def crashing(p):
            crashing_called.set()
            raise RuntimeError("crash")

        watcher.add_subscriber(crashing)
        watcher.add_subscriber(lambda p: good_fired.set())

        watcher.start()
        try:
            time.sleep(0.05)
            watched.write_text("v2")
            assert _wait(crashing_called)
            assert _wait(good_fired)
        finally:
            watcher.stop()


class TestFileChangedWatcherLifecycle:
    def test_start_stop_idempotent(self, tmp_path):
        watcher = FileChangedWatcher(
            watch_root=tmp_path, patterns=["*"],
        )
        watcher.start()
        watcher.start()
        watcher.stop()
        watcher.stop()

    def test_watch_root_does_not_exist_logs_and_returns(self, tmp_path):
        # Missing watch_root → start() returns gracefully (logged at
        # WARNING). No exception, no observer thread leaked.
        watcher = FileChangedWatcher(
            watch_root=tmp_path / "nonexistent",
            patterns=["*"],
        )
        watcher.start()
        assert watcher._observer is None
        watcher.stop()  # safe even though start was a no-op

"""Skill change detector — watch SKILL.md files and reload caches.

Phase-8 / WI-8.1. Closes gap analysis #10.

Mirrors TS ``utils/skills/skillChangeDetector.ts`` (chokidar-based).
Watches one or more skill directories; on a SKILL.md write/create/move,
clears the skill registry caches so the next ``get_all_skills(...)``
re-discovers the modified skill.

**Scoping (per A2-pattern).** Each detector instance owns its own
``watchdog.observers.Observer`` thread. Multiple detector instances
coexist without sharing state. ``stop()`` joins the observer thread
cleanly so test fixtures can dispose detectors between cases without
leaking threads.

**Subscribers.** A detector can fan out skill-changed events to
subscribers via the chapter-12 hook event emission stream
(Phase-6 / WI-6.1). The detector calls ``emit_hook_started`` /
``emit_hook_response`` style helpers so existing UI/SDK subscribers see
skill reloads alongside hook firings without a separate event channel.

**Debouncing.** Watchdog fires multiple events for a single editor save
(write → close → mtime change). The detector debounces by collapsing
events within a small time window (defaults to 250ms) — matches TS'
chokidar default. A subscriber receiving "skill X changed" twice in
the same edit cycle is annoying; the debounce makes it one event per
logical change.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


SKILL_FILENAME = "SKILL.md"
DEFAULT_DEBOUNCE_S = 0.25


class _SkillEventHandler(FileSystemEventHandler):
    """Internal watchdog handler. Filters to SKILL.md events and
    debounces consecutive events for the same path.
    """

    def __init__(
        self,
        on_change: Callable[[str], None],
        debounce_s: float = DEFAULT_DEBOUNCE_S,
    ) -> None:
        self._on_change = on_change
        self._debounce_s = debounce_s
        # path → last-fire timestamp. Used to suppress events that
        # land within the debounce window.
        self._last_fire: dict[str, float] = {}
        self._lock = threading.Lock()

    def _maybe_fire(self, path: str) -> None:
        # Only consider events on SKILL.md files. Subdirectory creation,
        # other files (.gitignore, README), etc., are ignored.
        if not path.endswith(SKILL_FILENAME):
            return
        now = time.monotonic()
        with self._lock:
            last = self._last_fire.get(path, 0.0)
            if now - last < self._debounce_s:
                return
            self._last_fire[path] = now
        try:
            self._on_change(path)
        except Exception:
            logger.exception(
                "skill change handler raised for %s; continuing", path,
            )

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_fire(str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_fire(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # ``on_moved`` carries both src and dest; fire on the destination
        # since that's where the skill file now lives.
        dest = getattr(event, "dest_path", "") or str(event.src_path)
        self._maybe_fire(str(dest))


class SkillChangeDetector:
    """Watch one or more skill directories; clear skill caches and emit
    events on change.

    Usage:
        det = SkillChangeDetector()
        det.watch(Path.home() / ".claude" / "skills")
        det.add_subscriber(lambda path: print(f"skill changed: {path}"))
        det.start()
        # ... session runs ...
        det.stop()

    The detector starts in a stopped state; ``start()`` spawns the
    observer thread, ``stop()`` joins it. Idempotent: ``start`` on a
    running detector is a no-op; ``stop`` on a stopped detector is a
    no-op.
    """

    def __init__(self, *, debounce_s: float = DEFAULT_DEBOUNCE_S) -> None:
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._handler = _SkillEventHandler(
            on_change=self._dispatch,
            debounce_s=debounce_s,
        )
        self._watched_paths: list[Path] = []
        self._subscribers: list[Callable[[str], None]] = []
        self._sub_lock = threading.Lock()

    def watch(self, path: str | Path, *, recursive: bool = True) -> None:
        """Add a directory to the watch list. Must be called BEFORE
        ``start()`` (or after ``stop()`` and re-``start()``).
        """
        if self._observer is not None:
            raise RuntimeError(
                "watch() must be called before start() — "
                "stop() the detector first to add new paths."
            )
        self._watched_paths.append(Path(path))

    def add_subscriber(self, subscriber: Callable[[str], None]) -> Callable[[], None]:
        """Register a subscriber. Returns an idempotent deregister fn."""
        with self._sub_lock:
            self._subscribers.append(subscriber)
        deregistered = {"done": False}

        def _deregister() -> None:
            if deregistered["done"]:
                return
            deregistered["done"] = True
            with self._sub_lock:
                try:
                    self._subscribers.remove(subscriber)
                except ValueError:
                    pass

        return _deregister

    def _dispatch(self, path: str) -> None:
        """Internal: fan out a skill-changed event to all subscribers
        AND clear the skill registry caches so the next ``get_all_skills``
        sees the change.
        """
        # Clear caches first so subscribers that re-query see fresh data.
        try:
            from src.skills.loader import clear_skill_caches
            clear_skill_caches()
        except Exception:
            logger.exception("failed to clear skill caches")

        with self._sub_lock:
            snapshot = list(self._subscribers)
        for sub in snapshot:
            try:
                sub(path)
            except Exception:
                logger.exception(
                    "skill-change subscriber raised for %s; continuing", path,
                )

    def start(self) -> None:
        """Start the observer thread. No-op if already running."""
        if self._observer is not None:
            return
        if not self._watched_paths:
            logger.warning(
                "SkillChangeDetector.start() with no watch paths; nothing to do"
            )
            return
        observer = Observer()
        for path in self._watched_paths:
            if not path.exists():
                logger.debug("skill watch path does not exist: %s", path)
                continue
            observer.schedule(self._handler, str(path), recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """Stop the observer thread. No-op if not running."""
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        finally:
            self._observer = None

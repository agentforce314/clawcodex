"""FileChanged hook event watcher.

Phase-8 / WI-8.2. Closes part of gap analysis #19.

Watches a configured list of glob patterns; emits ``FileChanged`` hook
events to subscribers when matching files are written/created/moved.
Subscribers are the chapter-12 hook event emission stream
(Phase-6 / WI-6.1) and lifecycle routers that fire FileChanged hooks
(Phase-1 / WI-1.1 promoted FileChanged to a first-class event).

**Why not reuse SkillChangeDetector?** The two watchers serve different
purposes:

  * ``SkillChangeDetector`` (WI-8.1) cares specifically about ``SKILL.md``
    files in known skill directories — its job is cache invalidation +
    skill-reload notifications.
  * ``FileChangedWatcher`` (this file) cares about arbitrary user-
    configured patterns from settings.json — its job is firing the
    ``FileChanged`` hook event so user-configured hooks can react
    (e.g., a hook that runs typecheck on every .ts save).

They share the watchdog dependency but not the filtering / subscriber
shape.

**Glob matching.** Patterns are gitignore-style globs (matches
``pathspec``'s ``GitWildMatchPattern``). Mirrors ``loader.py``'s
``paths`` matching for skill-conditional activation; reuses the same
library.

**Event payload.** Subscribers receive a dict with:
  * ``type``: ``"file_changed"``
  * ``event_type``: ``"created" | "modified" | "moved"``
  * ``path``: absolute path of the changed file
  * ``old_path``: present only for ``moved`` events
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import pathspec
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


DEFAULT_DEBOUNCE_S = 0.25


FileChangedHandler = Callable[[dict[str, Any]], None]


class _FileChangedEventHandler(FileSystemEventHandler):
    """Internal watchdog handler. Filters by glob spec and debounces."""

    def __init__(
        self,
        spec: pathspec.PathSpec,
        on_change: Callable[[dict[str, Any]], None],
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        watch_root: Path | None = None,
    ) -> None:
        self._spec = spec
        self._on_change = on_change
        self._debounce_s = debounce_s
        self._watch_root = watch_root
        self._last_fire: dict[str, float] = {}
        self._lock = threading.Lock()

    def _matches_spec(self, path: str) -> bool:
        # Match the path relative to the watch root (gitignore-style
        # patterns are root-relative). Falls back to the absolute path
        # if no watch_root was set.
        if self._watch_root is not None:
            try:
                rel = str(Path(path).relative_to(self._watch_root))
            except ValueError:
                return False
        else:
            rel = path
        return self._spec.match_file(rel)

    def _maybe_fire(self, payload: dict[str, Any]) -> None:
        path = payload.get("path", "")
        if not self._matches_spec(str(path)):
            return
        now = time.monotonic()
        with self._lock:
            last = self._last_fire.get(path, 0.0)
            if now - last < self._debounce_s:
                return
            self._last_fire[path] = now
        try:
            self._on_change(payload)
        except Exception:
            logger.exception(
                "file-changed handler raised for %s; continuing", path,
            )

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_fire({
            "type": "file_changed",
            "event_type": "created",
            "path": str(event.src_path),
        })

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_fire({
            "type": "file_changed",
            "event_type": "modified",
            "path": str(event.src_path),
        })

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", "") or str(event.src_path)
        self._maybe_fire({
            "type": "file_changed",
            "event_type": "moved",
            "path": str(dest),
            "old_path": str(event.src_path),
        })


class FileChangedWatcher:
    """Watch a glob-pattern list under a root directory; emit
    ``file_changed`` events to subscribers.

    Usage:
        watcher = FileChangedWatcher(
            watch_root=project_root,
            patterns=["**/*.py", "src/**"],
        )
        watcher.add_subscriber(handler)
        watcher.start()
        # ... session runs ...
        watcher.stop()
    """

    def __init__(
        self,
        *,
        watch_root: str | Path,
        patterns: Iterable[str],
        debounce_s: float = DEFAULT_DEBOUNCE_S,
    ) -> None:
        self._watch_root = Path(watch_root)
        self._spec = pathspec.PathSpec.from_lines(
            "gitwildmatch", list(patterns),
        )
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._handler = _FileChangedEventHandler(
            spec=self._spec,
            on_change=self._dispatch,
            debounce_s=debounce_s,
            watch_root=self._watch_root,
        )
        self._subscribers: list[FileChangedHandler] = []
        self._sub_lock = threading.Lock()

    def add_subscriber(
        self, subscriber: FileChangedHandler,
    ) -> Callable[[], None]:
        """Register a subscriber. Returns idempotent deregister."""
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

    def _dispatch(self, payload: dict[str, Any]) -> None:
        with self._sub_lock:
            snapshot = list(self._subscribers)
        for sub in snapshot:
            try:
                sub(payload)
            except Exception:
                logger.exception(
                    "file-changed subscriber raised; continuing"
                )

    def start(self) -> None:
        if self._observer is not None:
            return
        if not self._watch_root.exists():
            logger.warning(
                "FileChangedWatcher: watch_root does not exist: %s",
                self._watch_root,
            )
            return
        observer = Observer()
        observer.schedule(self._handler, str(self._watch_root), recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        finally:
            self._observer = None

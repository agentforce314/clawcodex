from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AbortSignal:
    _aborted: bool = False
    _reason: str | None = None
    _listeners: list[Callable[[], None]] = field(default_factory=list)

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    def add_listener(
        self,
        callback: Callable[[], None],
        *,
        once: bool = False,
    ) -> Callable[[], None]:
        """Register an abort listener.

        Mirrors TS ``signal.addEventListener('abort', cb, { once: true })``.
        Returns the *registered* callback so callers can pass it to
        ``remove_listener`` later. When ``once=True`` the wrapper removes
        itself after firing — without this the listener list grows
        unboundedly during long sessions.
        """
        if once:
            wrapper: Callable[[], None]

            def wrapper() -> None:  # type: ignore[no-redef]
                # Detach first so re-entrant fires don't double-invoke.
                self.remove_listener(wrapper)
                callback()

            self._listeners.append(wrapper)
            return wrapper
        self._listeners.append(callback)
        return callback

    def remove_listener(self, callback: Callable[[], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _fire(self, reason: str | None) -> None:
        self._aborted = True
        self._reason = reason
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                pass

    def throw_if_aborted(self) -> None:
        if self._aborted:
            raise AbortError(self._reason or "aborted")


class AbortError(Exception):
    def __init__(self, reason: str = "aborted"):
        super().__init__(reason)
        self.reason = reason


class AbortController:
    def __init__(self) -> None:
        self.signal = AbortSignal()

    def abort(self, reason: str | None = "aborted") -> None:
        if not self.signal.aborted:
            self.signal._fire(reason)


def create_abort_controller() -> AbortController:
    return AbortController()


def create_child_abort_controller(parent: AbortController) -> AbortController:
    """Create a child controller that aborts when its parent does.

    Aborting the child does NOT propagate up to the parent — that's the
    one-way semantic the streaming executor relies on (sibling abort
    cancels in-flight tools without ending the turn).

    Cleanup: the parent listener is registered ``once=True`` so a single
    fire detaches it. Additionally, when the child aborts on its own
    (e.g. permission rejection), we proactively remove the parent
    listener — otherwise long-lived parents accumulate one dead listener
    per child, and the streaming executor creates one child per tool.
    """
    child = AbortController()

    if parent.signal.aborted:
        child.abort(parent.signal.reason)
        return child

    def _on_parent_abort() -> None:
        child.abort(parent.signal.reason)

    registered = parent.signal.add_listener(_on_parent_abort, once=True)

    def _detach_parent_listener() -> None:
        parent.signal.remove_listener(registered)

    child.signal.add_listener(_detach_parent_listener, once=True)
    return child

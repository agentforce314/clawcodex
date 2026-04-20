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

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

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
    child = AbortController()

    if parent.signal.aborted:
        child.abort(parent.signal.reason)
        return child

    def _on_parent_abort() -> None:
        child.abort(parent.signal.reason)

    parent.signal.add_listener(_on_parent_abort)
    return child

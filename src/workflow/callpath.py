"""Deterministic call-path keys for resume (fixes the spawn-order bug).

The journal must key each ``agent()`` result by *where the call is in the
orchestration tree*, NOT by the order subagents happen to start — otherwise a
second round of fan-out whose timing depends on the first round (adversarial
verify, judge panels, loop-until-dry) assigns indices by completion order,
which is non-deterministic, breaking resume and risking returning a sibling's
cached result.

Each *branch* of execution carries a ``path`` (a tuple of ints) and a private
counter. Within a branch, ``agent`` / ``parallel`` / ``pipeline`` are awaited
sequentially, so each takes the next counter slot deterministically. ``parallel``
and ``pipeline`` give every item its own child branch (``path + (slot, item)``)
with a fresh counter, so concurrency only ever happens *across* branches — never
on a shared counter. The current branch is held in a ``ContextVar``, which
asyncio copies per task, so sibling fan-out branches stay isolated.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

CallKey = tuple[int, ...]


@dataclass
class Branch:
    path: CallKey = ()
    _counter: int = 0

    def next_slot(self) -> int:
        slot = self._counter
        self._counter += 1
        return slot


_current: contextvars.ContextVar[Branch] = contextvars.ContextVar("workflow_branch")


def current_branch() -> Branch:
    """The branch for the running task. ``run_workflow`` always sets a base
    branch before executing the script, so this is defined during a run."""
    return _current.get()


def use_branch(path: CallKey):
    """Enter a child branch (with a fresh counter); returns a reset token."""
    return _current.set(Branch(path))


def reset_branch(token) -> None:
    _current.reset(token)


def key_to_str(key: CallKey) -> str:
    return ".".join(str(part) for part in key)


def key_from_str(text: str) -> CallKey:
    return tuple(int(part) for part in text.split(".")) if text else ()

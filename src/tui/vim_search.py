"""Regex search for vim mode — ``/`` and ``?`` plus ``n`` / ``N`` repeat.

Phase-4 wave-2 of the ch13 refactor (gap #4 sub-item). Operates on
:class:`VimBuffer` and returns cursor positions a host widget can jump
to. The search state is held in :class:`VimSearchState` so ``n`` / ``N``
can repeat the most-recent search.

Pattern semantics: Python ``re`` syntax (not exactly vim's ``magic`` /
``nomagic`` modes — close enough for the common ``/foo``, ``/\\d+``,
``/^class\\s`` use cases, and Python's regex engine has fewer
surprises than vim's). Multiline matches are supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .vim_buffer import Cursor, VimBuffer


class SearchDirection(str, Enum):
    FORWARD = "/"
    BACKWARD = "?"


@dataclass
class SearchHit:
    """One match: cursor position + the matched text."""

    at: Cursor
    text: str


class InvalidPattern(ValueError):
    """Raised when a search pattern is not a valid regex."""


def find_next(
    buffer: VimBuffer,
    *,
    pattern: str,
    after: Cursor,
    direction: SearchDirection = SearchDirection.FORWARD,
    wrap: bool = True,
) -> SearchHit | None:
    """Find the next match of ``pattern`` from ``after``.

    Args:
        buffer: source.
        pattern: regex pattern; raises :class:`InvalidPattern` on bad regex.
        after: cursor position to start AFTER (forward) or BEFORE (backward).
        direction: ``/`` forward (default) or ``?`` backward.
        wrap: when ``True``, wrap around the buffer ends. Vim's
            default. Pass ``False`` to disable.

    Returns the first :class:`SearchHit` found, or ``None`` when the
    pattern has no match.
    """

    if not pattern:
        return None
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise InvalidPattern(str(exc)) from None

    if direction is SearchDirection.FORWARD:
        return _find_forward(buffer, rx, after, wrap=wrap)
    return _find_backward(buffer, rx, after, wrap=wrap)


def _find_forward(
    buffer: VimBuffer, rx: re.Pattern[str], after: Cursor, *, wrap: bool
) -> SearchHit | None:
    # Walk from after.row, after.col forward; first hit wins.
    n_lines = buffer.line_count
    if n_lines == 0:
        return None
    # Search from after position to end of buffer.
    for row in range(after.row, n_lines):
        line = buffer.line(row)
        start_col = after.col + 1 if row == after.row else 0
        if start_col >= len(line):
            # On the start row past the end — try matching empty position
            # only if start_col == len(line) and pattern is empty-matching;
            # otherwise skip.
            if start_col != len(line):
                continue
        m = rx.search(line, start_col)
        if m is not None:
            return SearchHit(at=Cursor(row, m.start()), text=m.group(0))
    if not wrap:
        return None
    # Wrap to the start of the buffer up to the original after.row.
    for row in range(0, after.row + 1):
        line = buffer.line(row)
        end_col = after.col if row == after.row else len(line)
        m = rx.search(line[:end_col])
        if m is not None:
            return SearchHit(at=Cursor(row, m.start()), text=m.group(0))
    return None


def _find_backward(
    buffer: VimBuffer, rx: re.Pattern[str], after: Cursor, *, wrap: bool
) -> SearchHit | None:
    n_lines = buffer.line_count
    if n_lines == 0:
        return None
    # Walk from after row backwards; on each line, find the last match
    # whose START is before the cursor column (vim's "previous match"
    # semantic — a match whose start is < cursor.col qualifies even if
    # its end extends past the cursor).
    for row in range(after.row, -1, -1):
        line = buffer.line(row)
        if row == after.row:
            cutoff = after.col
        else:
            cutoff = len(line) + 1  # any start <= len(line) qualifies
        last_match = None
        for m in rx.finditer(line):
            if m.start() < cutoff:
                last_match = m
            else:
                break
        if last_match is not None:
            return SearchHit(
                at=Cursor(row, last_match.start()), text=last_match.group(0)
            )
    if not wrap:
        return None
    # Wrap to the end and walk back to after.row, INCLUDING after.row's
    # tail (matches whose start is at or after after.col on after.row).
    for row in range(n_lines - 1, after.row - 1, -1):
        line = buffer.line(row)
        if row == after.row:
            # On wrap pass, after.row's "before-cursor" matches were
            # already considered in the first pass; include only matches
            # whose start is >= after.col (the tail).
            min_start = after.col
        else:
            min_start = 0
        last_match = None
        for m in rx.finditer(line):
            if m.start() >= min_start:
                last_match = m
        if last_match is not None:
            return SearchHit(
                at=Cursor(row, last_match.start()), text=last_match.group(0)
            )
    return None


@dataclass
class VimSearchState:
    """Track the most-recent search for ``n`` / ``N`` repeat.

    Use::

        state = VimSearchState()
        hit = state.search(buffer, pattern="foo", at=cursor)  # "/foo"
        next_hit = state.repeat(buffer, at=cursor)            # "n"
        prev_hit = state.repeat(buffer, at=cursor, reverse=True)  # "N"
    """

    pattern: str = ""
    direction: SearchDirection = SearchDirection.FORWARD

    def search(
        self,
        buffer: VimBuffer,
        *,
        pattern: str,
        at: Cursor,
        direction: SearchDirection = SearchDirection.FORWARD,
        wrap: bool = True,
    ) -> SearchHit | None:
        """Run a fresh search and remember the pattern for ``n``-repeat."""

        self.pattern = pattern
        self.direction = direction
        return find_next(
            buffer,
            pattern=pattern,
            after=at,
            direction=direction,
            wrap=wrap,
        )

    def repeat(
        self,
        buffer: VimBuffer,
        *,
        at: Cursor,
        reverse: bool = False,
        wrap: bool = True,
    ) -> SearchHit | None:
        """``n`` (forward in last direction) / ``N`` (reverse)."""

        if not self.pattern:
            return None
        direction = self.direction
        if reverse:
            direction = (
                SearchDirection.BACKWARD
                if direction is SearchDirection.FORWARD
                else SearchDirection.FORWARD
            )
        return find_next(
            buffer,
            pattern=self.pattern,
            after=at,
            direction=direction,
            wrap=wrap,
        )

    def is_armed(self) -> bool:
        return bool(self.pattern)


__all__ = [
    "InvalidPattern",
    "SearchDirection",
    "SearchHit",
    "VimSearchState",
    "find_next",
]

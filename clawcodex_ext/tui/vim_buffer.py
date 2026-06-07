"""Multi-line buffer model + cursor coordinate type for vim mode.

Phase-4 wave-1 of the ch13 refactor (gap #4). The chapter's vim mode
operates on a multi-line buffer; the existing ``src/tui/vim.py`` is a
single-line state machine bound to a Textual ``Input`` widget. This
module ports the multi-line buffer model so ``vim_text_objects`` and
``vim_operators`` (also wave-1) have a stable substrate to operate on
without touching the existing single-line vim integration.

Wave-2 work (WI-4.1 Input → TextArea swap, WI-4.5 Visual mode, WI-4.6
regex search) builds on top of this. WI-4.1 in particular plumbs
:class:`VimBuffer` into ``prompt_input.py`` once the multi-line widget
swap is decided.

Public surface:

* :class:`Cursor` — frozen ``(row, col)`` coordinate.
* :class:`Range` — frozen ``[start, end)`` half-open span.
* :class:`VimBuffer` — multi-line buffer with cursor + helpers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cursor:
    """Zero-indexed buffer coordinate.

    ``row`` is the line index; ``col`` is the column within the line.
    Both are clamped to the buffer dimensions by :class:`VimBuffer` —
    free-standing ``Cursor`` instances are not validated, so callers
    that construct them outside the buffer must clamp themselves.
    """

    row: int = 0
    col: int = 0


@dataclass(frozen=True)
class Range:
    """Half-open ``[start, end)`` selection range.

    ``start`` is inclusive, ``end`` is exclusive. ``end`` may be
    "beyond the end of the line" — i.e. one past the last character of
    a line — so a range covering the trailing newline is representable.
    """

    start: Cursor
    end: Cursor

    @property
    def is_empty(self) -> bool:
        return self.start == self.end

    def normalised(self) -> "Range":
        """Return the range with start ≤ end (in buffer order)."""

        if (self.start.row, self.start.col) <= (self.end.row, self.end.col):
            return self
        return Range(start=self.end, end=self.start)


class VimBuffer:
    """Mutable multi-line buffer with a cursor + simple text operations.

    Storage is a list of strings, one per line. Trailing newlines are
    NOT stored — they are an implicit separator between lines. So a
    file ``"a\\nb\\n"`` becomes ``["a", "b"]`` with no extra trailing
    empty line, and ``"a\\nb"`` becomes the same. This matches
    ``str.splitlines`` behavior, which is what most external
    text-source clients (Textual ``TextArea`` etc.) emit.

    Out-of-range cursor coordinates are clamped on assignment via
    :meth:`set_cursor` so the cursor invariant
    (``0 <= row < line_count`` and ``0 <= col <= len(lines[row])``) is
    always preserved.
    """

    def __init__(self, text: str = "") -> None:
        self._lines: list[str] = text.splitlines() if text else [""]
        if not self._lines:
            self._lines = [""]
        self._cursor = Cursor(0, 0)

    # ---- accessors ----
    @property
    def cursor(self) -> Cursor:
        return self._cursor

    def set_cursor(self, row: int, col: int) -> None:
        row = max(0, min(row, len(self._lines) - 1))
        line = self._lines[row]
        col = max(0, min(col, len(line)))
        self._cursor = Cursor(row=row, col=col)

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def line(self, row: int) -> str:
        if row < 0 or row >= len(self._lines):
            raise IndexError(f"row {row} out of range")
        return self._lines[row]

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    def is_empty(self) -> bool:
        return self._lines == [""]

    # ---- char/range access ----
    def char_at(self, row: int, col: int) -> str:
        """Return the character at ``(row, col)`` or ``""`` past line end."""

        if row < 0 or row >= len(self._lines):
            return ""
        line = self._lines[row]
        if col < 0 or col >= len(line):
            return ""
        return line[col]

    def text_in(self, range_: Range) -> str:
        """Extract the substring under ``range_`` (normalised)."""

        r = range_.normalised()
        if r.is_empty:
            return ""
        if r.start.row == r.end.row:
            line = self._lines[r.start.row]
            return line[r.start.col : r.end.col]
        # Multi-line: head + middle lines + tail.
        parts: list[str] = [self._lines[r.start.row][r.start.col :]]
        for row in range(r.start.row + 1, r.end.row):
            parts.append(self._lines[row])
        if r.end.col > 0 and r.end.row < len(self._lines):
            parts.append(self._lines[r.end.row][: r.end.col])
        else:
            parts.append("")
        return "\n".join(parts)

    # ---- mutations ----
    def replace(self, range_: Range, replacement: str) -> str:
        """Replace ``range_`` with ``replacement``; return the removed text.

        The cursor is parked at ``range_.start`` (i.e. the position the
        replaced span used to start at). Callers that want a different
        cursor position update it after.
        """

        r = range_.normalised()
        removed = self.text_in(r)
        before = self._lines[r.start.row][: r.start.col]
        if r.end.row < len(self._lines):
            after = self._lines[r.end.row][r.end.col :]
        else:
            after = ""

        new_block = before + replacement + after
        new_block_lines = new_block.splitlines() or [""]

        # Rebuild ``self._lines`` with the spliced block in place of
        # ``range_``.
        self._lines = (
            self._lines[: r.start.row]
            + new_block_lines
            + self._lines[r.end.row + 1 :]
        )
        if not self._lines:
            self._lines = [""]
        # Park cursor at the start of the replaced span (callers re-park
        # if they want a different position).
        self.set_cursor(r.start.row, r.start.col)
        return removed

    def delete(self, range_: Range) -> str:
        """Convenience: replace ``range_`` with empty string."""

        return self.replace(range_, "")

    def insert(self, text: str) -> None:
        """Insert ``text`` at the cursor and advance the cursor past it."""

        if not text:
            return
        cur = self._cursor
        line = self._lines[cur.row]
        new_block = line[: cur.col] + text + line[cur.col :]
        new_lines = new_block.splitlines()
        if not new_lines:
            new_lines = [""]
        self._lines = (
            self._lines[: cur.row] + new_lines + self._lines[cur.row + 1 :]
        )
        # Advance cursor past the inserted text.
        if "\n" in text:
            tail = text.split("\n")[-1]
            self.set_cursor(cur.row + text.count("\n"), len(tail))
        else:
            self.set_cursor(cur.row, cur.col + len(text))

    # ---- motion helpers ----
    def move_relative(self, drow: int, dcol: int) -> None:
        cur = self._cursor
        self.set_cursor(cur.row + drow, cur.col + dcol)

    def move_to_line_start(self) -> None:
        self.set_cursor(self._cursor.row, 0)

    def move_to_line_end(self) -> None:
        self.set_cursor(self._cursor.row, len(self._lines[self._cursor.row]))

    def move_to_buffer_start(self) -> None:
        self.set_cursor(0, 0)

    def move_to_buffer_end(self) -> None:
        last_row = len(self._lines) - 1
        self.set_cursor(last_row, len(self._lines[last_row]))


__all__ = ["Cursor", "Range", "VimBuffer"]

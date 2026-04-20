"""Structured diff renderer.

Port of ``components/StructuredDiff.tsx`` — renders a single diff
"hunk" with ``+``/``-`` gutter, line numbers, and background-shaded
add/remove rows so the viewer can scan changes quickly.

The parser only needs to understand unified-diff output (``git diff``
or :mod:`difflib` unified format) because that's what the agent's
file-editing tools emit. We intentionally skip the syntax-aware
intra-line coloring from the TS reference — that relies on an
external tokenizer and adds more complexity than it's worth for the
Phase 3 milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rich.text import Text
from textual.widgets import Static


@dataclass
class DiffLine:
    """One line in a parsed unified diff."""

    kind: str  # one of: "context", "add", "remove", "hunk", "meta"
    text: str
    old_lineno: int | None = None
    new_lineno: int | None = None


def parse_unified_diff(patch: str) -> list[DiffLine]:
    """Parse unified-diff text into a flat list of :class:`DiffLine`.

    Assumes ``patch`` follows the standard ``diff --git`` /
    ``---``/``+++``/``@@`` preamble + hunks. Robust to leading/trailing
    whitespace and missing hunk headers — non-standard input simply
    produces a run of ``context`` lines.
    """

    lines: list[DiffLine] = []
    old_lineno = 0
    new_lineno = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            lines.append(DiffLine(kind="hunk", text=raw))
            # Parse hunk header to seed line numbers.
            # Example: ``@@ -12,5 +14,7 @@``
            try:
                header = raw.split("@@")[1].strip()
                old_part, new_part = header.split(" ")
                old_lineno = int(old_part.split(",")[0].lstrip("-"))
                new_lineno = int(new_part.split(",")[0].lstrip("+"))
            except (IndexError, ValueError):
                old_lineno = new_lineno = 0
            continue
        if raw.startswith("+++") or raw.startswith("---") or raw.startswith("diff "):
            lines.append(DiffLine(kind="meta", text=raw))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            lines.append(
                DiffLine(
                    kind="add",
                    text=raw[1:],
                    old_lineno=None,
                    new_lineno=new_lineno,
                )
            )
            new_lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            lines.append(
                DiffLine(
                    kind="remove",
                    text=raw[1:],
                    old_lineno=old_lineno,
                    new_lineno=None,
                )
            )
            old_lineno += 1
        elif raw.startswith(" ") or raw == "":
            # Context line.
            body = raw[1:] if raw.startswith(" ") else ""
            lines.append(
                DiffLine(
                    kind="context",
                    text=body,
                    old_lineno=old_lineno,
                    new_lineno=new_lineno,
                )
            )
            old_lineno += 1
            new_lineno += 1
        else:
            lines.append(DiffLine(kind="meta", text=raw))
    return lines


def parse_structured_patch(hunks: Iterable[dict]) -> list[DiffLine]:
    """Convert structured-patch hunks (as emitted by Edit/Write tools) into
    a flat list of :class:`DiffLine`.

    Each hunk dict has the shape ``{oldStart, oldLines, newStart, newLines,
    lines}`` (see :func:`src.tool_system.diff_utils.unified_diff_hunks`).
    Line numbers are threaded from each hunk's ``oldStart`` / ``newStart``
    counters, mirroring what :func:`parse_unified_diff` does when it sees
    an ``@@`` header.
    """

    out: list[DiffLine] = []
    for hunk in hunks:
        old_start = int(hunk.get("oldStart", 0) or 0)
        old_count = int(hunk.get("oldLines", 0) or 0)
        new_start = int(hunk.get("newStart", 0) or 0)
        new_count = int(hunk.get("newLines", 0) or 0)
        header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
        out.append(DiffLine(kind="hunk", text=header))
        old_lineno = old_start
        new_lineno = new_start
        for raw in hunk.get("lines") or []:
            # Edit's structuredPatch preserves the source line terminator
            # (it runs splitlines(keepends=True) before unified_diff). Strip
            # it here so render_diff's own ``\n`` doesn't produce blank rows
            # between every entry.
            raw = raw.rstrip("\n").rstrip("\r")
            if raw.startswith("+"):
                out.append(
                    DiffLine(
                        kind="add",
                        text=raw[1:],
                        old_lineno=None,
                        new_lineno=new_lineno,
                    )
                )
                new_lineno += 1
            elif raw.startswith("-"):
                out.append(
                    DiffLine(
                        kind="remove",
                        text=raw[1:],
                        old_lineno=old_lineno,
                        new_lineno=None,
                    )
                )
                old_lineno += 1
            else:
                body = raw[1:] if raw.startswith(" ") else raw
                out.append(
                    DiffLine(
                        kind="context",
                        text=body,
                        old_lineno=old_lineno,
                        new_lineno=new_lineno,
                    )
                )
                old_lineno += 1
                new_lineno += 1
    return out


def count_changes(lines: Iterable[DiffLine]) -> tuple[int, int]:
    """Return ``(additions, removals)`` — useful for status-line badges."""

    add = remove = 0
    for line in lines:
        if line.kind == "add":
            add += 1
        elif line.kind == "remove":
            remove += 1
    return add, remove


def render_diff(lines: Iterable[DiffLine], *, width_hint: int = 100) -> Text:
    """Render the parsed diff to a Rich :class:`Text` renderable.

    The ``width_hint`` is used to right-pad added/removed lines so the
    background shading fills the row even when the source line is
    shorter than the viewport. Phase 3 wraps overlong lines instead of
    truncating them so users don't lose trailing characters.
    """

    out = Text()
    for line in lines:
        if line.kind == "meta":
            out.append(line.text, style="dim italic")
            out.append("\n")
            continue
        if line.kind == "hunk":
            out.append(line.text, style="bold cyan")
            out.append("\n")
            continue
        if line.kind == "add":
            prefix = _fmt_line_no(None, line.new_lineno)
            out.append(prefix, style="green")
            out.append("+ ", style="bold green")
            out.append(line.text, style="green on #0f2314")
            out.append("\n")
        elif line.kind == "remove":
            prefix = _fmt_line_no(line.old_lineno, None)
            out.append(prefix, style="red")
            out.append("- ", style="bold red")
            out.append(line.text, style="red on #2a1414")
            out.append("\n")
        else:  # context
            prefix = _fmt_line_no(line.old_lineno, line.new_lineno)
            out.append(prefix, style="dim")
            out.append("  ", style="dim")
            out.append(line.text, style="")
            out.append("\n")
    return out


def _fmt_line_no(old: int | None, new: int | None) -> str:
    """Return a 9-char gutter ``'  12  15 '`` / ``'      15 '`` etc."""

    def _cell(n: int | None) -> str:
        if n is None:
            return "    "
        return f"{n:4d}"

    return f"{_cell(old)} {_cell(new)} "


class StructuredDiff(Static):
    """Single-hunk diff widget.

    Pass either a parsed list of :class:`DiffLine` (``lines=``) or the
    raw patch text (``patch=``) — the widget parses the latter on
    construction.
    """

    DEFAULT_CSS = """
    StructuredDiff {
        padding: 0 1;
        background: $surface;
    }
    """

    def __init__(
        self,
        *,
        patch: str | None = None,
        lines: list[DiffLine] | None = None,
    ) -> None:
        if lines is None:
            lines = parse_unified_diff(patch or "")
        self._lines = lines
        super().__init__(render_diff(lines), markup=False)

    @property
    def diff_lines(self) -> list[DiffLine]:
        return list(self._lines)

    @property
    def stats(self) -> tuple[int, int]:
        return count_changes(self._lines)

    def set_patch(self, patch: str) -> None:
        """Replace the rendered content with a new parsed patch."""

        self._lines = parse_unified_diff(patch)
        self.update(render_diff(self._lines))

    def set_lines(self, lines: list[DiffLine]) -> None:
        self._lines = list(lines)
        self.update(render_diff(self._lines))


__all__ = [
    "DiffLine",
    "StructuredDiff",
    "parse_unified_diff",
    "parse_structured_patch",
    "render_diff",
    "count_changes",
]

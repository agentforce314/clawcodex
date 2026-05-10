"""Standalone tool result row.

Used for code paths where the agent loop emits a ``tool_result`` event
without a matching ``tool_use`` (e.g. replayed history). Normal in-turn
tool execution routes through :class:`AssistantToolUseMessage` instead.

Port of ``typescript/src/components/messages/AssistantToolResultMessage.tsx``
reduced to the fields the Python agent loop actually emits.

Phase-10 close-out (gap #15): when the rendered text contains paths
that look like file references, wrap them in OSC 8 hyperlinks so
modern terminals make them clickable.
"""

from __future__ import annotations

import re

from rich.markup import escape as escape_markup
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from src.tui.hyperlinks import is_hyperlink_supported

from .base import BaseRow, RowHeader


# Heuristic: match POSIX-ish absolute paths and explicit relative paths
# (``./foo``, ``../foo``, ``~/foo``). Bare-word relative paths
# (``src/x.py``) are also matched when they have at least one ``/``
# separator. Word-boundary anchors prevent eating the trailing
# punctuation of a sentence (Critic-flagged: ``check src/main.py.``
# previously captured the trailing period).
_PATH_BODY_CHARS = r"\w./\-+"  # alnum, underscore, dot, slash, hyphen, plus
_PATH_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s\(\[]))"  # start of string OR preceding whitespace/bracket
    r"(?P<path>"
    rf"(?:~|\.{{1,2}})?/[{_PATH_BODY_CHARS}]+"  # ``/abs``, ``./rel``, ``~/rel``
    rf"|[\w.\-]+(?:/[{_PATH_BODY_CHARS}]+)+"  # ``src/x.py`` style w/ ≥1 slash
    r")"
    # Trim trailing sentence punctuation. The character class includes
    # ``.`` (file extensions need it), so ``main.py.`` greedily eats
    # the period; we trim with a lookbehind that asserts the FINAL
    # character is not punctuation. Vim-grade fidelity isn't required;
    # we just need the OSC 8 URL to not include ``,;:!?.`` at the end.
    r"(?<![.,;:!?])"
)


def _wrap_paths_with_hyperlinks(body: str) -> Text:
    """Render ``body`` with file-path-looking substrings as OSC 8 links.

    Falls back to plain text on terminals that don't support hyperlinks
    (per :func:`is_hyperlink_supported`).
    """

    if not is_hyperlink_supported():
        return Text(body)
    parts: list[tuple[str, str | None]] = []
    last = 0
    for match in _PATH_RE.finditer(body):
        if match.start() > last:
            parts.append((body[last : match.start()], None))
        path = match.group("path")
        parts.append((path, path))
        last = match.end()
    if last < len(body):
        parts.append((body[last:], None))
    if not parts:
        return Text(body)
    out = Text()
    for piece, link in parts:
        if link is None:
            out.append(piece)
        else:
            # Use Rich's markup syntax — ``Text.from_markup`` is the
            # idiomatic emit path; we escape the visible portion so a
            # user-supplied path containing ``[`` doesn't get parsed.
            href = link if link.startswith(("/", "~", ".")) else link
            url = (
                href if href.startswith("file://") else f"file://{href}"
            )
            out.append_text(
                Text.from_markup(f"[link={url}]{escape_markup(piece)}[/link]")
            )
    return out


class ToolResultRow(BaseRow):
    DEFAULT_CSS = """
    ToolResultRow {
        height: auto;
    }
    ToolResultRow > Static.-body {
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        tool_name: str,
        summary: str,
        body: str | None = None,
        is_error: bool = False,
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._summary = summary
        self._body = body or ""
        self._is_error = is_error

    def compose(self) -> ComposeResult:
        glyph = "✗" if self._is_error else "✓"
        # Wrap the summary's file paths (if any) in OSC 8 hyperlinks.
        # Header and body both go through the same heuristic so a file
        # path that appears in either position is clickable.
        header_text = _wrap_paths_with_hyperlinks(
            f"{glyph} {self._summary or self._tool_name}"
        )
        header = RowHeader(header_text, markup=False)
        header.add_class("-tool-error" if self._is_error else "-tool-success")
        yield header
        if self._body.strip():
            yield Static(
                _wrap_paths_with_hyperlinks(self._body),
                markup=False,
                classes="-body",
            )

    def snapshot(self) -> Text:
        """Return a Rich :class:`Text` for post-exit scrollback dump."""

        glyph = "✗" if self._is_error else "✓"
        style = "bold red" if self._is_error else "bold green"
        out = Text(f"{glyph} {self._summary or self._tool_name}", style=style)
        if self._body.strip():
            out.append("\n")
            out.append(self._body)
        return out

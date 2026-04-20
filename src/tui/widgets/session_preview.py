"""Read-only session transcript preview.

Port of ``components/SessionPreview.tsx`` and
``components/tasks/RemoteSessionProgress.tsx``:

* :class:`SessionPreview` is a simple read-only renderer that walks a
  list of message dicts and prints them with the same role-based
  styling the live transcript uses. Callers hand it the output of the
  resume/history log loader.
* :class:`RemoteSessionProgressLine` is a compact, animated status
  line for an in-flight remote agent session (rainbow-ish step
  counter + elapsed time).

Both widgets are :class:`textual.widgets.Static` subclasses so they
embed cleanly inside modals, background-task panels, or the
transcript itself without extra containers.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Sequence

from rich.text import Text
from textual.widgets import Static


def _role_style(role: str) -> str:
    if role == "user":
        return "bold #8ab4f8"
    if role == "assistant":
        return "bold #c58af9"
    if role == "system":
        return "dim"
    if role == "tool":
        return "#f5c451"
    return ""


class SessionPreview(Static):
    """Read-only transcript snapshot for session previews.

    ``messages`` is a list of dicts with at least ``role`` and
    ``content`` (string or list-of-blocks); lists of blocks are
    flattened to plain text.
    """

    DEFAULT_CSS = """
    SessionPreview {
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(self, *, messages: Sequence[dict[str, Any]] | None = None) -> None:
        self._messages = list(messages or [])
        super().__init__(self._build_text(), markup=False)

    def set_messages(self, messages: Sequence[dict[str, Any]]) -> None:
        self._messages = list(messages)
        self.update(self._build_text())

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def _build_text(self) -> Text:
        out = Text()
        for msg in self._messages:
            role = str(msg.get("role") or "").lower()
            body = _flatten_content(msg.get("content"))
            if not body:
                continue
            style = _role_style(role)
            prefix = _role_prefix(role)
            out.append(prefix, style=style)
            out.append(body)
            if not body.endswith("\n"):
                out.append("\n")
        return out


def _role_prefix(role: str) -> str:
    if role == "user":
        return "❯ "
    if role == "assistant":
        return "☞ "
    if role == "system":
        return "• "
    if role == "tool":
        return "⎔ "
    return ""


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", None):
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "tool_use":
                    parts.append(f"[tool_use: {item.get('name') or ''}]")
                elif item.get("type") == "tool_result":
                    parts.append("[tool_result]")
                else:
                    parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


_SPINNER_FRAMES = list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


class RemoteSessionProgressLine(Static):
    """One-liner progress indicator for a remote agent session."""

    DEFAULT_CSS = """
    RemoteSessionProgressLine {
        padding: 0 1;
        height: 1;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        started_at: float | None = None,
        step: int = 0,
    ) -> None:
        self._title = title
        self._started_at = started_at if started_at is not None else time.time()
        self._step = step
        self._frames = itertools.cycle(_SPINNER_FRAMES)
        self._current_frame = next(self._frames)
        super().__init__(self._build_text(), markup=False)

    def tick(self) -> None:
        self._current_frame = next(self._frames)
        self.update(self._build_text())

    def set_step(self, step: int) -> None:
        self._step = step
        self.update(self._build_text())

    def _build_text(self) -> Text:
        elapsed = max(0, int(time.time() - self._started_at))
        out = Text()
        out.append(self._current_frame, style="bold magenta")
        out.append(" ")
        out.append(self._title, style="bold")
        out.append("  step ", style="dim")
        out.append(str(self._step), style="cyan")
        out.append("  ")
        out.append(_fmt_elapsed(elapsed), style="dim")
        return out


def _fmt_elapsed(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


__all__ = [
    "RemoteSessionProgressLine",
    "SessionPreview",
]

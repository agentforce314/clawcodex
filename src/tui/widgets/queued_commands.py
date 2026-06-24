"""Dim preview of prompts queued while the agent is busy.

Parity with ``components/PromptInput/PromptInputQueuedCommands.tsx``:
when the user types a prompt while a run is in flight, it is *queued*
for the next turn (``AppState.queued_prompts``) rather than dropped.
This widget renders, directly above the prompt input, a dim header
(``"N message(s) queued for next turn"``) followed by one dim line per
queued prompt so the user can see *what* is pending — not just the
``"queued N"`` count pill on the status line.

Scope note (deliberately reduced vs TS): TS ``PromptInputQueuedCommands``
iterates typed ``QueuedCommand`` records with a ``mode``
(prompt / bash / task-notification), caps task-notifications, folds idle
hints, and renders each through the full ``<Message>`` component. Python's
``queued_prompts`` is a plain ``list[str]`` of raw *prompt* text (slash /
bash / memory inputs never reach the queue — see
``REPLScreen.on_prompt_submitted``), so the faithful slice is the dim
header + per-prompt text line. The capping / folding / multi-mode system
has no Python producer and is intentionally out of scope.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

# U+2026 HORIZONTAL ELLIPSIS — one cell wide, matches the truncation
# marker used elsewhere in the prompt chrome (prompt_input.py).
_ELLIPSIS = "…"


def _truncate(text: str, width: int) -> str:
    """Clip ``text`` to ``width`` columns, ending in an ellipsis.

    Uses character count as a column proxy (consistent with the rest of
    the prompt chrome). ``width <= 0`` is treated as "no bound known"
    and returns the text unchanged — the widget falls back to a sane
    default width, and the Rich ``overflow="ellipsis"`` safety net on
    the renderable clips anything that still exceeds the real console.
    """

    if width <= 0 or len(text) <= width:
        return text
    if width == 1:
        return _ELLIPSIS
    return text[: width - 1] + _ELLIPSIS


def format_queued_preview(prompts: list[str], width: int) -> Text:
    """Build the dim renderable for the queued-prompts preview.

    Pure + deterministic so it can be unit-tested without a live layout.
    Each prompt is reduced to its **first line**, whitespace-collapsed,
    and truncated to ``width`` with an ellipsis so a multi-line or huge
    pasted prompt can never blow up the footer. Returns an empty
    ``Text`` when the queue is empty (the widget hides itself in that
    case via CSS).
    """

    count = len(prompts)
    if count == 0:
        return Text("")
    header = (
        "1 message queued for next turn"
        if count == 1
        else f"{count} messages queued for next turn"
    )
    # Whole renderable is dim; ``no_wrap`` + ``overflow`` is a safety net
    # for any line that still exceeds the real console width at render.
    out = Text(no_wrap=True, overflow="ellipsis", style="dim")
    out.append(_truncate(header, width))
    for prompt in prompts:
        first_line = prompt.split("\n", 1)[0]
        collapsed = " ".join(first_line.split())
        out.append("\n")
        out.append(_truncate(collapsed, width))
    return out


class QueuedCommands(Static):
    """Footer widget showing prompts queued for the next turn.

    Hidden (``display: none``) while the queue is empty; the
    ``-has-queue`` state class flips it visible. Re-renders on resize
    (``render`` recomputes from the live content width) and on
    :meth:`set_prompts`.
    """

    DEFAULT_CSS = """
    QueuedCommands {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    QueuedCommands.-has-queue {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._prompts: list[str] = []

    def set_prompts(self, prompts: list[str]) -> None:
        """Replace the queued-prompt list and refresh the preview."""

        self._prompts = list(prompts)
        # State class drives visibility — no manual height juggling.
        self.set_class(bool(self._prompts), "-has-queue")
        self.refresh(layout=True)

    def render(self) -> Text:
        if not self._prompts:
            return Text("")
        # content_size is (0, 0) until first layout; fall back so the
        # very first paint still truncates to something sane.
        width = self.content_size.width or 80
        return format_queued_preview(self._prompts, width)


__all__ = ["QueuedCommands", "format_queued_preview"]

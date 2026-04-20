"""Transcript message-selector screen.

Port of ``components/MessageSelector.tsx`` — the "rewind" view that
lists every user message in the current conversation so the user can
jump back to, resubmit, or delete-forward from a specific turn.

Ink opens it via double-Esc from the :class:`PromptInput`; we follow
the same keybinding. The screen is a ``ModalScreen`` so it stacks
cleanly on top of the REPL and consumes keyboard focus while active.

Phase 3 ships "pick a user message, dismiss with the chosen prompt
text". Actually rewinding the agent's conversation state
(`clearMessagesAfter`) is left to a later session so we don't
destabilise the agent-loop thread before it's wired into the bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, Literal, Sequence

from textual.widget import Widget

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


SelectorAction = Literal["restore", "summarize", "cancel"]


@dataclass
class TranscriptMessage:
    """A message row shown in the selector.

    ``kind`` is "user" or "assistant"; in Phase 3 we only surface
    user messages because those are the rewind anchors, but
    assistant rows are accepted so callers can re-use the type for
    preview rendering.
    """

    index: int
    kind: str
    text: str
    timestamp: float = 0.0


class MessageSelectorScreen(DialogScreen[tuple[int, SelectorAction]]):
    """Modal selector for transcript navigation / rewind."""

    title_text = "Rewind to message"
    subtitle_text = "Pick a previous prompt to restore or summarise."
    footer_hint = "Enter to restore · s to summarize · Esc to cancel"

    def __init__(
        self,
        *,
        messages: Sequence[TranscriptMessage],
        on_choice: Callable[[int, str], None] | None = None,
    ) -> None:
        super().__init__()
        self._messages = [m for m in messages if m.kind == "user" and m.text.strip()]
        self._on_choice = on_choice

    def build_body(self) -> Iterator[Widget]:
        options: list[SelectOption] = []
        for msg in self._messages:
            label = msg.text.splitlines()[0] if msg.text else ""
            if len(label) > 80:
                label = label[:77] + "…"
            options.append(
                SelectOption(
                    label=label or "(empty)",
                    value=msg.index,
                    description=f"#{msg.index}",
                )
            )
        if not options:
            options = [
                SelectOption(
                    label="(no user messages yet)",
                    value=-1,
                    disabled=True,
                )
            ]
        self._select = SelectList(options, allow_cancel=True)
        yield self._select

    def _post_mount(self) -> None:
        self._select.focus()
        # Cursor starts at the most recent user message, mirroring ink.
        if self._messages:
            self._select.cursor = len(self._messages) - 1

    # ---- key bindings ----
    async def on_key(self, event) -> None:  # type: ignore[override]
        if event.key == "s" and self._select.current is not None:
            idx = int(self._select.current.value)
            self._resolve(idx, "summarize")
            event.stop()

    # ---- select events ----
    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        idx = int(event.option.value)
        if idx < 0:
            self.dismiss((idx, "cancel"))
            return
        self._resolve(idx, "restore")

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss((-1, "cancel"))

    def _resolve(self, index: int, action: SelectorAction) -> None:
        if self._on_choice is not None:
            try:
                self._on_choice(index, action)
            except Exception:
                pass
        self.dismiss((index, action))


__all__ = ["MessageSelectorScreen", "TranscriptMessage", "SelectorAction"]

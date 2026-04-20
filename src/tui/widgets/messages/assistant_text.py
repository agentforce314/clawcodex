"""Assistant turn row with live streaming and deferred Markdown rendering.

Port of ``typescript/src/components/messages/AssistantTextMessage.tsx``.
The key behavioural difference from the Phase 0 Rich REPL is that this
widget mutates **in place** as chunks arrive instead of buffering until
end-of-turn — matching the ink reference where ``streamingText``
updates the same DOM node every frame.

Because RichMarkdown is brittle across partial tokens (``**Cla`` …
``ude**``), we stream as **plain text** and only swap to a
``rich.markdown.Markdown`` render when :meth:`finalise` is called with
the authoritative full turn text.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static

from .base import BaseRow, RowHeader


class AssistantTextMessage(BaseRow):
    """Live-updating assistant turn."""

    DEFAULT_CSS = """
    AssistantTextMessage {
        height: auto;
    }
    AssistantTextMessage > Static.-body {
        padding: 0 1;
    }
    AssistantTextMessage.-streaming > Static.-body {
        color: $text;
    }
    """

    streaming_text: reactive[str] = reactive("", layout=True)

    def __init__(self) -> None:
        super().__init__()
        self._finalised = False
        self._final_text = ""
        self.add_class("-streaming")

    # ---- composition ----
    def compose(self) -> ComposeResult:
        header = RowHeader(Text("assistant", style="bold"), markup=False)
        header.add_class("-assistant")
        yield header
        yield Static(Text(""), markup=False, classes="-body")

    # ---- streaming ----
    def append_chunk(self, chunk: str) -> None:
        """Append a streamed chunk and trigger a re-render.

        No-op after :meth:`finalise` — chunks that arrive after
        end-of-turn are discarded by design (the server already sent the
        full message).
        """
        if self._finalised or not chunk:
            return
        self.streaming_text += chunk
        self._refresh_body()

    def finalise(self, text: str) -> None:
        """Replace the streamed plain text with a rendered Markdown block.

        Swaps the body ``Static`` contents in place so the scroll
        position stays anchored (matching ink's re-render-by-identity
        semantics).
        """

        self._final_text = text or self.streaming_text
        self._finalised = True
        self.remove_class("-streaming")
        body = self._body_widget()
        if body is None:
            return
        if not self._final_text.strip():
            body.update(Text(""))
            return
        try:
            body.update(Markdown(self._final_text))
        except Exception:
            body.update(Text(self._final_text))

    # ---- internals ----
    def _body_widget(self) -> Static | None:
        try:
            for static in self.query(Static):
                if static.has_class("-body"):
                    return static
        except Exception:
            return None
        return None

    def _refresh_body(self) -> None:
        body = self._body_widget()
        if body is None:
            return
        body.update(Text(self.streaming_text))

    def snapshot(self):
        """Return a Rich renderable for post-exit scrollback dump.

        Prefers the final text (rendered as Markdown to match what the
        user saw) and falls back to the streamed plain text when the
        turn didn't finalise (e.g. aborted mid-stream).
        """

        text = self._final_text if self._finalised else self.streaming_text
        header = Text("assistant\n", style="bold #c58af9")
        if not (text or "").strip():
            return header
        try:
            return (header, Markdown(text))
        except Exception:
            return header + Text(text)

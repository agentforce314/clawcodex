"""Banner above the input announcing a stashed draft from a prior session.

Mirrors ``typescript/src/components/PromptInput/PromptInputStashNotice.tsx``.
When the user exits the TUI mid-edit (``/exit`` with non-empty draft),
the unsent text is written to a per-project stash file. On next boot,
:class:`PromptInputStashNotice` surfaces the available stash and gives
the user a key to recover it.

Persistence policy (refactoring-plan WI-3.7):

* Stash file lives at ``~/.claude/projects/<workspace-hash>/.tui_stash``
  so each project gets its own stash. The legacy REPL has its own stash
  at a different path; the two do not share content.
* :func:`write_stash` is called at exit when the prompt input has a
  non-empty draft.
* :func:`read_stash` is called on boot. The widget hides itself if the
  stash is empty / missing / unreadable.
* :func:`clear_stash` removes the file (called when the user actions
  ``Recover`` or otherwise explicitly drops it).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


_logger = logging.getLogger(__name__)


def _stash_dir(workspace_root: Path | str | None = None) -> Path:
    """Return the per-project stash directory; create it on demand."""

    if workspace_root is None:
        workspace_root = Path.cwd()
    digest = hashlib.sha256(
        str(Path(workspace_root).resolve()).encode("utf-8")
    ).hexdigest()[:16]
    base = Path.home() / ".claude" / "projects" / digest
    return base


def _stash_path(workspace_root: Path | str | None = None) -> Path:
    return _stash_dir(workspace_root) / ".tui_stash"


def write_stash(text: str, workspace_root: Path | str | None = None) -> None:
    """Persist ``text`` as the recovery stash. Empty text deletes the stash."""

    target = _stash_path(workspace_root)
    if not text or not text.strip():
        clear_stash(workspace_root)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        _logger.warning("failed to write stash at %s: %s", target, exc)


def read_stash(workspace_root: Path | str | None = None) -> str:
    """Return the stash contents, or empty string when absent / unreadable."""

    target = _stash_path(workspace_root)
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning("failed to read stash at %s: %s", target, exc)
        return ""


def clear_stash(workspace_root: Path | str | None = None) -> None:
    """Remove the stash file silently."""

    target = _stash_path(workspace_root)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("failed to clear stash at %s: %s", target, exc)


class PromptInputStashNotice(Widget):
    """One-line banner shown only when a non-empty stash exists."""

    DEFAULT_CSS = """
    PromptInputStashNotice {
        height: auto;
        max-height: 1;
        padding: 0 1;
        color: $warning;
    }
    PromptInputStashNotice.-hidden {
        display: none;
    }
    """

    has_stash: reactive[bool] = reactive(False, layout=True)
    recover_key: reactive[str] = reactive("Ctrl+R", layout=True)

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="-row", markup=False)

    def on_mount(self) -> None:
        self._refresh()

    def watch_has_stash(self, _old: bool, _new: bool) -> None:
        self._refresh()

    def watch_recover_key(self, _old: str, _new: str) -> None:
        self._refresh()

    def announce_stash(self, present: bool, *, recover_key: str = "Ctrl+R") -> None:
        """Atomic update — set both ``has_stash`` and ``recover_key`` together."""

        self.has_stash = present
        self.recover_key = recover_key

    # ---- internals ----
    def _refresh(self) -> None:
        row = self._row()
        if row is None:
            return
        if not self.has_stash:
            self.add_class("-hidden")
            row.update(Text(""))
            return
        self.remove_class("-hidden")
        rendered = Text("stashed draft available — press ", style="dim")
        rendered.append(self.recover_key, style="bold")
        rendered.append(" to recover", style="dim")
        row.update(rendered)

    def _row(self) -> Static | None:
        try:
            for static in self.query(Static):
                if static.has_class("-row"):
                    return static
        except Exception:
            return None
        return None


__all__ = [
    "PromptInputStashNotice",
    "clear_stash",
    "read_stash",
    "write_stash",
]

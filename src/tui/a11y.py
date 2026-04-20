"""Accessibility helpers for the Claw Codex TUI.

Terminal UIs don't have a native ARIA model, but terminal-based
screen readers (emacspeak, Orca in terminals, VoiceOver over SSH)
still read whatever text the renderer emits. This module gives the
app three building blocks that together raise the floor for
screen-reader / high-contrast usability:

1. :class:`LiveRegion` — a single-line :class:`textual.widgets.Static`
   that mirrors the ARIA "live region" pattern. Mounted near the top
   of the main screen so assistive tech re-reads it whenever its
   content changes. Intentionally ``height: 1`` with no borders so it
   doesn't steal visual real estate when idle.

2. :class:`Announcer` — pushes short text messages to the live region
   **and** to Textual's toast system (:meth:`App.notify`) so users who
   don't rely on AT still get the cue. An in-memory ring buffer of
   the last 50 announcements is exposed for tests and for a future
   "review announcements" dialog.

3. :func:`describe_option` and :func:`describe_status` — string
   normalisers that turn visual cues (disabled rows, status glyphs)
   into text prefixes screen readers can speak unambiguously. For
   example, a disabled option renders with a ``dim strike`` style,
   which is invisible to screen readers; passing its label through
   :func:`describe_option` produces ``"[disabled] label"``.

Placing these behind a thin facade makes it trivial to disable the
feature (an env flag) or upgrade the channel (e.g. write to a named
pipe for external screen readers) later without touching the call
sites.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Literal

from rich.text import Text
from textual.app import App
from textual.widgets import Static


AnnouncementLevel = Literal["polite", "assertive"]


@dataclass(frozen=True)
class Announcement:
    """One announcement record, kept in :class:`Announcer`'s history."""

    message: str
    level: AnnouncementLevel = "polite"


class LiveRegion(Static):
    """Invisible-until-announced live region.

    Mount this once near the top of the screen; :class:`Announcer`
    updates its text each time an announcement is made. The widget is
    a plain :class:`Static` so Textual re-renders it synchronously,
    which matters for terminal screen readers that poll the viewport.

    Set ``aria_label`` at construction time to prefix every announcement
    with a stable tag (e.g. ``"Status:"``). Leave as ``None`` for a
    raw mirror of the announced text.
    """

    DEFAULT_CSS = """
    LiveRegion {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    LiveRegion.-assertive {
        color: $warning;
        text-style: bold;
    }
    """

    def __init__(self, *, aria_label: str | None = None) -> None:
        super().__init__("", markup=False)
        self._aria_label = aria_label
        self.can_focus = False

    def announce(
        self,
        message: str,
        *,
        level: AnnouncementLevel = "polite",
    ) -> None:
        """Replace the region's text with ``message``.

        The ``assertive`` level toggles the ``-assertive`` CSS class
        so the text is visually emphasised (bold / warning colour) in
        addition to being re-read by any AT that polls the viewport.
        """

        prefix = f"{self._aria_label} " if self._aria_label else ""
        self.update(Text(f"{prefix}{message}", style="" if level == "polite" else "bold"))
        if level == "assertive":
            self.add_class("-assertive")
        else:
            self.remove_class("-assertive")


class Announcer:
    """Fan out screen-reader-friendly announcements.

    The announcer is owned by :class:`src.tui.app.ClawCodexTUI` and is
    picked up by any screen that wants to talk to AT — including the
    modal dialogs, since they can reach the app via
    :attr:`ModalScreen.app`. When no live region is attached, the
    announcer still fires ``app.notify`` so keyboard-only users get a
    visible toast.
    """

    HISTORY_LIMIT = 50

    def __init__(self, app: App) -> None:
        self._app = app
        self._live_region: LiveRegion | None = None
        self._history: collections.deque[Announcement] = collections.deque(
            maxlen=self.HISTORY_LIMIT
        )
        self._enabled = True

    # ---- configuration ----
    def bind_region(self, region: LiveRegion | None) -> None:
        """Attach (or detach) the :class:`LiveRegion` widget."""

        self._live_region = region

    def set_enabled(self, enabled: bool) -> None:
        """Disable the announcer (e.g. when AT support is unwanted)."""

        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def history(self) -> list[Announcement]:
        """Return a snapshot of the last :attr:`HISTORY_LIMIT` announcements."""

        return list(self._history)

    # ---- emission ----
    def announce(
        self,
        message: str,
        *,
        level: AnnouncementLevel = "polite",
        notify: bool = True,
    ) -> None:
        """Announce ``message`` to every attached channel.

        ``notify=False`` suppresses the Textual toast — useful for
        very frequent announcements (e.g. streaming-chunk keepalives)
        that would otherwise spam the notification drawer. The live
        region is always updated.
        """

        if not self._enabled or not message:
            return
        self._history.append(Announcement(message=message, level=level))
        if self._live_region is not None:
            try:
                self._live_region.announce(message, level=level)
            except Exception:
                pass
        if notify:
            try:
                severity = "warning" if level == "assertive" else "information"
                self._app.notify(message, severity=severity, timeout=3.0)
            except Exception:
                pass


# ---- string normalisers ----


def describe_option(
    label: str,
    *,
    disabled: bool = False,
    selected: bool = False,
    description: str | None = None,
) -> str:
    """Return a screen-reader-friendly description of a select row.

    ``disabled`` and ``selected`` are conveyed via text prefixes
    because screen readers can't see the CSS-driven dim/strike
    styling that :class:`SelectList` uses. ``description`` is appended
    after an em-dash if present.
    """

    parts: list[str] = []
    if disabled:
        parts.append("[disabled]")
    if selected:
        parts.append("[selected]")
    parts.append(label or "(empty)")
    if description:
        parts.append(f"— {description}")
    return " ".join(parts)


def describe_status(kind: str, *, verb: str | None = None) -> str:
    """Map a tab-status kind (``idle``/``busy``/``waiting``) to text."""

    if kind == "busy":
        return f"Agent busy: {verb}" if verb else "Agent busy"
    if kind == "waiting":
        return "Waiting for input"
    return "Agent idle"


def aria_label(widget, label: str) -> None:
    """Best-effort ARIA-label shim for a Textual widget.

    Textual doesn't have a formal ARIA API, so we use:

    * :attr:`Widget.tooltip` — shown on hover in the inspector and
      exposed to any MCP-based AT readers that inspect the widget tree.
    * :attr:`Widget.border_title` — visible label on bordered widgets.

    Both assignments are wrapped in ``try`` so callers don't have to
    know which widget flavour they're tagging.
    """

    try:
        widget.tooltip = label
    except Exception:
        pass
    try:
        widget.border_title = label
    except Exception:
        pass


__all__ = [
    "Announcement",
    "AnnouncementLevel",
    "Announcer",
    "LiveRegion",
    "aria_label",
    "describe_option",
    "describe_status",
]

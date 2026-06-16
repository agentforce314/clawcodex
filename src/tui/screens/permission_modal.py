"""Permission request modal screen with per-tool specialized previews.

Phase 7 of the ch13 refactor (gap #8) closes the gap between the prior
unified preview and chapter
``typescript/src/components/permissions/PermissionRequest.tsx`` which
routes to a per-tool body (Bash → command + matched rule; Edit →
inline diff; Write → file path + content preview; etc.).

C1 (components-folder parity) upgraded the decision surface from binary
Allow/Deny to the TS ``PermissionPrompt.tsx`` option list:

* **Allow once** (``y``)
* **Allow for the whole session** (``a``) — the per-tool option mirroring the
  original (``allow all edits in <dir>/ during this session`` for file edits,
  ``allow reading from <dir>/ …`` for reads, ``don't ask again for <rule>`` for
  bash/other tools). The accepted updates (``setMode:acceptEdits`` /
  ``addDirectories`` / ``addRules``) are applied to the live context and the
  persistable ones saved; label text comes from
  :func:`src.permissions.updates.session_option_label`.
* **Deny** (``n`` / ``Esc``)
* **Deny with feedback** (``d``) — opens a one-line input; the note
  reaches the model in the tool error (TS "No, and tell Claude what to
  do differently")

The modal resolves by calling ``request.decide`` with a
:class:`src.permissions.types.PermissionAskReply`.

Architecture per refactoring-plan A9: a single :class:`PermissionModal`
screen with a tool-name-keyed dispatcher to specialized render
functions. Avoids a class-per-tool hierarchy that would make adding
tools verbose. Per the ``mouse=False`` design constraint (gap analysis
§1 read #2 (c)), all interactive elements are reachable via keyboard
only; the buttons are decorative — the actual decisions fire from the
keybindings registered on the modal.
"""

from __future__ import annotations

from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from src.permissions.updates import session_option_label

from ..messages import PermissionResolved
from ..state import PendingPermission


class PermissionModal(ModalScreen[bool]):
    """Modal that blocks input until the user decides on a tool call."""

    BINDINGS = [
        Binding("y", "allow", "Allow once", show=False),
        Binding("a", "allow_always", "Allow always", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("d", "deny_feedback", "Deny with feedback", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    PermissionModal > Middle > Center > #panel {
        width: 72;
        max-width: 90%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    PermissionModal #title {
        color: $warning;
        text-style: bold;
        margin-bottom: 1;
    }
    PermissionModal #buttons {
        height: auto;
        margin-top: 1;
    }
    PermissionModal Button {
        min-width: 10;
        margin-right: 2;
    }
    PermissionModal Button.-allow {
        background: $success;
        color: $background;
    }
    PermissionModal Button.-deny {
        background: $error;
        color: $background;
    }
    """

    def __init__(self, request: PendingPermission) -> None:
        super().__init__()
        self._request = request
        self._always_label = session_option_label(
            getattr(request, "suggestions", ()) or (),
            getattr(request, "tool_name", None),
            getattr(request, "tool_input", None),
        )
        self._feedback_open = False
        self._resolved = False

    # ---- composition ----
    def compose(self) -> ComposeResult:
        panel = Vertical(id="panel")
        panel.border_title = "[ Permission required ]"
        with Middle():
            with Center():
                yield panel

    def on_mount(self) -> None:
        try:
            panel = self.query_one("#panel", Vertical)
        except Exception:
            return
        panel.mount(
            Static(
                Text(f"⚠  {self._request.tool_name}", style="bold"),
                id="title",
                markup=False,
            )
        )
        panel.mount(Static(Text(self._request.message), markup=False))
        # Phase-7: dispatch to per-tool renderer (Bash → command + rule;
        # Edit → inline diff; etc.). Falls back to a generic preview for
        # unknown tools.
        input_preview = preview_for_tool(
            self._request.tool_name, self._request.tool_input
        )
        if input_preview is not None:
            panel.mount(Static(input_preview, markup=False))
        buttons = Vertical(id="buttons")
        panel.mount(buttons)
        buttons.mount(Button("Allow once (y)", id="allow", classes="-allow"))
        if self._always_label:
            buttons.mount(
                Button(
                    f"Yes, {self._always_label} (a)",
                    id="allow-always",
                    classes="-allow",
                )
            )
        buttons.mount(Button("Deny (n / esc)", id="deny", classes="-deny"))
        buttons.mount(
            Button(
                "Deny, tell Claude what to do differently (d)",
                id="deny-feedback",
                classes="-deny",
            )
        )
        feedback = Input(
            placeholder="Tell Claude what to do differently… (enter to send)",
            id="feedback-input",
        )
        feedback.display = False
        panel.mount(feedback)

    # ---- events ----
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow":
            self.action_allow()
        elif event.button.id == "allow-always":
            self.action_allow_always()
        elif event.button.id == "deny":
            self.action_deny()
        elif event.button.id == "deny-feedback":
            self.action_deny_feedback()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "feedback-input":
            self._resolve(allowed=False, feedback=event.value.strip() or None)

    def action_allow(self) -> None:
        if self._feedback_open:
            return
        self._resolve(allowed=True)

    def action_allow_always(self) -> None:
        if self._feedback_open or not self._always_label:
            return
        self._resolve(allowed=True, always=True)

    def action_deny(self) -> None:
        # Esc while the feedback input is open backs out to plain deny —
        # same end state, so just resolve.
        self._resolve(allowed=False)

    def action_deny_feedback(self) -> None:
        if self._feedback_open:
            return
        self._feedback_open = True
        try:
            feedback = self.query_one("#feedback-input", Input)
        except Exception:
            self._resolve(allowed=False)
            return
        feedback.display = True
        feedback.focus()

    # ---- internals ----
    def _resolve(
        self,
        *,
        allowed: bool,
        always: bool = False,
        feedback: str | None = None,
    ) -> None:
        if self._resolved:
            return
        self._resolved = True
        from src.permissions.types import PermissionAskReply

        if allowed:
            chosen = (
                tuple(getattr(self._request, "suggestions", ()) or ())
                if always
                else ()
            )
            reply = PermissionAskReply(behavior="allow", chosen_updates=chosen)
        else:
            reply = PermissionAskReply(behavior="deny", message=feedback)
        try:
            self._request.decide(reply)
        except Exception:
            pass
        # Post the decision to the app so status-line / state observers
        # can react (e.g. drain the next queued permission).
        self.app.post_message(
            PermissionResolved(
                request_id=self._request.request_id,
                allowed=allowed,
                always=always,
                feedback=feedback,
            )
        )
        self.dismiss(allowed)


def preview_for_tool(
    tool_name: str | None, tool_input: Any
) -> RenderableType | None:
    """Render a permission preview for ``tool_input`` under ``tool_name``.

    Specialized renderers live in ``_TOOL_RENDERERS``; unknown tools fall
    through to :func:`_render_generic_input`. Callers may pass
    ``tool_name=None`` to force the generic path (back-compat with code
    that doesn't know the tool name).
    """

    if not tool_input:
        return None
    handler = _TOOL_RENDERERS.get(tool_name) if tool_name else None
    try:
        rendered = handler(tool_input) if handler else None
    except Exception:
        rendered = None
    if rendered is None:
        rendered = _render_generic_input(tool_input)
    return rendered


def _render_generic_input(tool_input: Any) -> RenderableType | None:
    """Default preview — pretty-prints the input dict, capped per-key."""

    if isinstance(tool_input, dict):
        lines: list[str] = []
        for key, value in tool_input.items():
            if value is None:
                continue
            sv = str(value)
            if len(sv) > 200:
                sv = sv[:197] + "…"
            lines.append(f"{key}: {sv}")
            if len(lines) >= 6:
                break
        if not lines:
            return None
        body = "\n".join(lines)
    else:
        body = str(tool_input)
        if len(body) > 400:
            body = body[:397] + "…"
    return Panel(
        Text(body),
        border_style="bright_black",
        padding=(0, 1),
        title=escape("input"),
    )


# ---- per-tool renderers -----------------------------------------------------


def _render_bash(tool_input: Any) -> RenderableType | None:
    """Bash command + (when available) the matching permission rule."""

    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not command:
        return None
    body_lines: list[Text] = []
    body_lines.append(Text("$ ", style="bold"))
    body_lines[-1].append(str(command), style="default")

    rule = tool_input.get("matched_permission_rule") or tool_input.get("rule")
    if rule:
        body_lines.append(Text())
        body_lines.append(
            Text("rule: ", style="dim").append(str(rule), style="bold cyan")
        )

    description = tool_input.get("description")
    if description:
        body_lines.append(Text())
        body_lines.append(Text(str(description), style="italic dim"))

    return Panel(
        Group(*body_lines),
        border_style="yellow",
        padding=(0, 1),
        title=escape("bash"),
    )


def _render_edit(tool_input: Any) -> RenderableType | None:
    """File-path + inline old/new diff (line-oriented)."""

    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path") or tool_input.get("path")
    old_string = tool_input.get("old_string", "") or ""
    new_string = tool_input.get("new_string", "") or ""

    parts: list[RenderableType] = []
    if file_path:
        parts.append(
            Text("file: ", style="dim").append(str(file_path), style="bold")
        )
    if old_string or new_string:
        diff = _format_inline_diff(old_string, new_string)
        if diff is not None:
            parts.append(diff)
    if not parts:
        return None
    return Panel(
        Group(*parts),
        border_style="yellow",
        padding=(0, 1),
        title=escape("edit"),
    )


def _render_write(tool_input: Any) -> RenderableType | None:
    """File path + content preview (truncated)."""

    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path") or tool_input.get("path")
    content = tool_input.get("content", "") or ""
    if not file_path and not content:
        return None
    parts: list[RenderableType] = []
    if file_path:
        parts.append(
            Text("write: ", style="dim").append(str(file_path), style="bold")
        )
    if content:
        preview = str(content)
        if len(preview) > 400:
            preview = preview[:397] + "…"
        parts.append(Text(preview))
    return Panel(
        Group(*parts),
        border_style="yellow",
        padding=(0, 1),
        title=escape("write"),
    )


def _render_read(tool_input: Any) -> RenderableType | None:
    """File path + optional limit/offset."""

    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path") or tool_input.get("path")
    if not file_path:
        return None
    parts: list[RenderableType] = [
        Text("read: ", style="dim").append(str(file_path), style="bold")
    ]
    extras = []
    if tool_input.get("offset"):
        extras.append(f"offset={tool_input.get('offset')}")
    if tool_input.get("limit"):
        extras.append(f"limit={tool_input.get('limit')}")
    if extras:
        parts.append(Text(" ".join(extras), style="dim"))
    return Panel(
        Group(*parts),
        border_style="yellow",
        padding=(0, 1),
        title=escape("read"),
    )


def _format_inline_diff(
    old_string: str, new_string: str, *, max_lines: int = 12
) -> RenderableType | None:
    """Render a minus/plus inline diff capped at ``max_lines``."""

    old_lines = old_string.splitlines() or ([""] if old_string else [])
    new_lines = new_string.splitlines() or ([""] if new_string else [])
    out: list[Text] = []
    for line in old_lines[:max_lines]:
        out.append(Text("- ", style="bold red").append(line, style="red"))
    if len(old_lines) > max_lines:
        out.append(
            Text(f"  … {len(old_lines) - max_lines} more removed lines", style="dim")
        )
    for line in new_lines[:max_lines]:
        out.append(Text("+ ", style="bold green").append(line, style="green"))
    if len(new_lines) > max_lines:
        out.append(
            Text(f"  … {len(new_lines) - max_lines} more added lines", style="dim")
        )
    if not out:
        return None
    return Group(*out)


# Tool name → render function. Add new tools by appending here; the
# fallback (`_render_generic_input`) catches everything else.
_TOOL_RENDERERS: dict[str, Callable[[Any], RenderableType | None]] = {
    "Bash": _render_bash,
    "Edit": _render_edit,
    "Write": _render_write,
    "Read": _render_read,
    # Common alias variants — match either capitalization the tool
    # registry might use.
    "bash": _render_bash,
    "edit": _render_edit,
    "write": _render_write,
    "read": _render_read,
}


__all__ = [
    "PermissionModal",
    "preview_for_tool",
]

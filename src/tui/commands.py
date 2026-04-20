"""Slash-command adapter for the Textual TUI.

The legacy :class:`src.repl.core.ClawcodexREPL` has its own slash-command
dispatcher that mixes local built-ins (``/exit``, ``/tools``, …) with the
newer :mod:`src.command_system` registry (``/help``, ``/clear``, …) and
``PromptCommand`` slash commands like ``/init`` that expand into model
prompts. The TUI reuses the same :mod:`src.command_system` registry so
the two UIs stay feature-aligned; this module is the thin adapter that
glues the registry to Textual's message pump without pulling
``prompt_toolkit`` into the TUI import graph.

Built-ins that are not yet covered by the command registry (``/exit``,
``/quit``, ``/repl``, ``/clear``, ``/tools``, ``/help``, ``/tui``) stay
in-app so the TUI continues to work when the command registry fails to
initialise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.agent import Session


# Local slash commands (resolved inside the Textual app, never go through
# the command registry). Keep aligned with
# :attr:`src.repl.core.ClawcodexREPL._original_built_ins` so the two REPLs
# advertise the same surface.
LOCAL_BUILTINS: tuple[str, ...] = (
    "/help",
    "/exit",
    "/quit",
    "/q",
    "/repl",
    "/clear",
    "/tools",
    "/stream",
    "/render-last",
    "/skills",
    # Phase 2 dialogs:
    "/model",
    "/effort",
    "/history",
    "/cost",
    "/idle",
    "/theme",
    # Phase 3 dialogs:
    "/diff",
    "/mcp",
    "/tasks",
    "/rewind",
)


@dataclass
class CommandDispatchResult:
    """What the TUI should do after a slash command was handled.

    * ``handled`` — command was fully resolved locally, no agent call.
    * ``prompt_text`` — the command resolved into a prompt (e.g. ``/init``)
      that should be forwarded to the agent as a user turn.
    * ``system_text`` — text to append to the transcript as a system
      message (from commands that emit a textual response).
    * ``open_dialog`` — name of a Phase 2 dialog screen to push
      (``model``, ``effort``, ``history``, ``cost``, ``idle``, ``exit``,
      ``theme``). The app resolves the name to a concrete
      :class:`DialogScreen` subclass.
    * ``error`` — surfaced to the user as a red system line.
    """

    handled: bool
    prompt_text: str | None = None
    system_text: str | None = None
    open_dialog: str | None = None
    error: str | None = None


def build_command_words(
    workspace_root: Path,
    tool_context: Any | None = None,
) -> list[str]:
    """Return the flat list of slash words shown in the completion popup.

    Sources, in order:
      * Local built-ins from :data:`LOCAL_BUILTINS`.
      * Every command registered with :func:`register_builtin_commands`
        plus any aliases.
      * Discovered skill names (mirrors the REPL behavior).
    """

    words: list[str] = list(LOCAL_BUILTINS)

    try:
        from src.command_system.builtins import register_builtin_commands
        from src.command_system.registry import CommandRegistry, get_command_registry

        # The global registry may not have been seeded yet (first TUI
        # boot), so ensure at least the built-ins are present.
        register_builtin_commands(None)
        registry: CommandRegistry = get_command_registry()
        for cmd in registry.list_commands():
            words.append(f"/{cmd.name}")
            for alias in getattr(cmd, "aliases", []) or []:
                words.append(f"/{alias}")
    except Exception:
        pass

    try:
        from src.skills.loader import get_all_skills

        cwd = getattr(tool_context, "cwd", None) or workspace_root
        for skill in get_all_skills(project_root=cwd):
            words.append(f"/{skill.name}")
    except Exception:
        pass

    seen: set[str] = set()
    deduped: list[str] = []
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(word)
    return deduped


def dispatch_local_command(
    text: str,
    *,
    session: "Session",
    workspace_root: Path,
    tool_registry: Any,
) -> CommandDispatchResult:
    """Resolve a slash command that the TUI handles directly.

    Returns a :class:`CommandDispatchResult` describing whether the
    command was handled and how the screen should react. Commands that
    need async dispatch (PromptCommand like ``/init``, or any
    registry-backed command) are NOT handled here — callers should fall
    through to :func:`dispatch_registry_command` for those.
    """

    raw = text.strip()
    if not raw.startswith("/"):
        return CommandDispatchResult(handled=False)

    name = raw.split(" ", 1)[0].lower()

    if name in ("/exit", "/quit", "/q", "/repl"):
        return CommandDispatchResult(handled=True, system_text="__exit__")
    if name == "/clear":
        return CommandDispatchResult(handled=True, system_text="__clear__")
    if name == "/help":
        lines = [
            "Slash commands:",
            "  " + "  ".join(LOCAL_BUILTINS),
            "Tip: press `/` in the prompt to open the palette.",
            "Exit with /exit or Ctrl+D. /repl is an alias kept for parity.",
        ]
        return CommandDispatchResult(handled=True, system_text="\n".join(lines))
    if name == "/tools":
        try:
            names = sorted(t.name for t in tool_registry.list_tools())
            return CommandDispatchResult(
                handled=True,
                system_text="Available tools: " + ", ".join(names),
            )
        except Exception as exc:
            return CommandDispatchResult(handled=True, error=f"/tools: {exc}")
    if name == "/tui":
        # Already in TUI — no-op message instead of silently handing off.
        return CommandDispatchResult(
            handled=True,
            system_text="Already running in Textual TUI.",
        )

    # Phase 2 dialogs: the command itself has no state to resolve here,
    # it just asks the app to push the corresponding modal screen.
    if name in ("/model", "/models"):
        return CommandDispatchResult(handled=True, open_dialog="model")
    if name == "/effort":
        return CommandDispatchResult(handled=True, open_dialog="effort")
    if name in ("/history", "/hist"):
        return CommandDispatchResult(handled=True, open_dialog="history")
    if name == "/cost":
        return CommandDispatchResult(handled=True, open_dialog="cost")
    if name == "/idle":
        return CommandDispatchResult(handled=True, open_dialog="idle")
    if name == "/theme":
        return CommandDispatchResult(handled=True, open_dialog="theme")
    if name == "/diff":
        return CommandDispatchResult(handled=True, open_dialog="diff")
    if name == "/mcp":
        return CommandDispatchResult(handled=True, open_dialog="mcp")
    if name == "/tasks":
        return CommandDispatchResult(handled=True, open_dialog="tasks")
    if name == "/rewind":
        return CommandDispatchResult(handled=True, open_dialog="rewind")

    return CommandDispatchResult(handled=False)


async def dispatch_registry_command(
    text: str,
    *,
    command_context: Any,
) -> CommandDispatchResult:
    """Resolve a slash command through the :mod:`src.command_system` async
    path. Returns ``handled=False`` when the command is unknown so the
    TUI can fall back to forwarding the text as a regular prompt.
    """

    raw = text.strip()
    if not raw.startswith("/"):
        return CommandDispatchResult(handled=False)

    parts = raw[1:].split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    try:
        from src.command_system.builtins import execute_command_async
        from src.command_system.types import CommandResult  # noqa: F401

        result = await execute_command_async(name, args, command_context)
    except Exception as exc:
        return CommandDispatchResult(handled=False, error=f"/{name}: {exc}")

    if not result.success:
        if result.error and "Unknown command" in result.error:
            return CommandDispatchResult(handled=False)
        return CommandDispatchResult(handled=True, error=result.error)

    if result.result_type == "text":
        return CommandDispatchResult(handled=True, system_text=result.text or "")

    if result.result_type == "prompt":
        prompt_text = ""
        for item in result.prompt_content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                prompt_text = item.get("text", "") or ""
                break
        return CommandDispatchResult(
            handled=True,
            prompt_text=prompt_text or None,
        )

    if result.result_type == "skip":
        return CommandDispatchResult(handled=True)

    return CommandDispatchResult(handled=True)

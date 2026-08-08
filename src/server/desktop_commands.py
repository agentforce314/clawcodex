"""Built-in slash-command catalog for the desktop gateway.

The TUI owns its command list client-side (``ui-tui/src/gatewayClient.ts``
``SLASHES``); the desktop renderer instead expects the SERVER to return the
catalog via the ``commands.catalog`` RPC, then filters it against its own
known-command allowlist. This is the server-side source of that catalog:
the built-in ClawCodex commands, to which the gateway appends live skills
(``list_skills``) and dynamic workflow commands (``list_workflow_commands``).

Kept as data so it stays in step with the TUI's SLASHES by inspection.
"""

from __future__ import annotations

from typing import Any

# (name, description) — the built-in command set, mirroring the TUI's SLASHES.
BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show available commands"),
    ("/clear", "Clear the conversation"),
    ("/model", "Switch the model"),
    ("/output-style", "Set the output style"),
    ("/logo", "Change the startup logo color scheme"),
    ("/permissions", "Choose what ClawCodex is allowed to do"),
    ("/compact", "Compact the conversation to save context"),
    ("/context", "Show context-window usage"),
    ("/cost", "Show the total cost and duration of the current session"),
    ("/eco", "Toggle Bash-output token compression (RTK-style)"),
    ("/rewind", "Undo recent turns"),
    ("/thinking", "Toggle extended thinking"),
    ("/effort", "Set reasoning effort (or \"ultracode\" workflow mode)"),
    ("/provider", "Switch the provider"),
    ("/advisor", "Configure the advisor reviewer model"),
    ("/fusion", "Give a text-only model vision by fusing it with a multimodal one"),
    ("/vision", "Set the vision model the vision_analyze tool asks about images"),
    ("/workflows", "List running and recent dynamic workflows"),
    ("/knowledge", "Search / manage the knowledge base"),
    ("/memory", "Edit memory files, or manage the bounded memory store"),
    ("/skills", "Browse and inspect available skills"),
    ("/plan", "Enable plan mode or view the current session plan"),
    ("/goal", "Set a completion condition ClawCodex keeps working toward"),
    ("/subgoal", "Add or manage extra criteria on the active goal"),
    ("/loop", "Run a prompt repeatedly on a schedule"),
    ("/insights", "Generate session insights"),
    ("/bg", "List or start background agents"),
    ("/resume", "Resume a past session"),
    ("/rename", "Rename this session"),
]

# Argument hints, keyed by command — the desktop popover shows these after the
# command name when completing an argument.
COMMAND_HINTS: dict[str, str] = {
    "/output-style": "[<name>]",
    "/eco": "[on|off|status]",
    "/rewind": "[<turns>]",
    "/thinking": "[on|off|toggle]",
    "/effort": "[low|medium|high|xhigh|max|auto|ultracode]",
    "/provider": "[<provider>]",
    "/knowledge": "[status|list|clear|enable|disable]",
    "/memory": "[status|pending|approve <id|all>|reject <id|all>]",
    "/skills": "[list | inspect <name> | search <query>]",
    "/plan": "[<description>]",
    "/goal": "[<condition> | status | clear | pause | resume]",
    "/subgoal": "[<text> | remove <n> | clear]",
    "/loop": "[interval] [prompt]",
    "/rename": "<name>",
}


def build_catalog(
    skills: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the ``commands.catalog`` payload the desktop renderer reads.

    Shape (``CommandsCatalogLike`` in ui-desktop): ``pairs`` (flat
    [name, desc] list), ``hints``, ``skills`` (per-command ranking map),
    ``skill_count``. The desktop's ``filterDesktopCommandsCatalog`` narrows
    ``pairs`` to the commands it can actually fulfil.
    """
    pairs: list[list[str]] = [[name, desc] for name, desc in BUILTIN_COMMANDS]
    hints: dict[str, str] = dict(COMMAND_HINTS)
    seen = {name for name, _ in BUILTIN_COMMANDS}
    skill_map: dict[str, dict[str, Any]] = {}

    for wf in workflows or []:
        name = wf.get("name")
        if not name:
            continue
        slash = name if str(name).startswith("/") else f"/{name}"
        if slash in seen:
            continue
        seen.add(slash)
        pairs.append([slash, wf.get("description") or "Run a dynamic workflow"])
        hint = wf.get("argument_hint")
        if hint:
            hints[slash] = hint

    for skill in skills or []:
        name = skill.get("name")
        if not name:
            continue
        slash = name if str(name).startswith("/") else f"/{name}"
        if slash not in seen:
            seen.add(slash)
            pairs.append([slash, skill.get("description") or "Run a skill"])
        origin = skill.get("provenance") or skill.get("origin")
        skill_map[slash] = {
            "origin": "local" if origin == "agent" else origin,
            "usage": skill.get("usage", 0),
        }

    return {
        "pairs": pairs,
        "hints": hints,
        "skills": skill_map,
        "skill_count": len(skill_map),
        "categories": [],
    }


def complete(text: str, catalog: dict[str, Any]) -> dict[str, Any]:
    """Prefix-filter the catalog for ``complete.slash`` (``/mo`` → /model…)."""
    needle = (text or "/").lower()
    items = [
        {
            "text": name,
            "display": name,
            "meta": desc,
            "hint": catalog.get("hints", {}).get(name),
        }
        for name, desc in catalog.get("pairs", [])
        if name.lower().startswith(needle)
    ]
    return {"items": items, "replace_from": 1}


__all__ = ["BUILTIN_COMMANDS", "build_catalog", "complete"]

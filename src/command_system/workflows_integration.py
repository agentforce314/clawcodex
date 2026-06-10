"""Contribute saved + bundled workflows as slash commands.

Discovers ``.claude/workflows/*.py`` (project) and ``~/.claude/workflows/*.py``
(personal), plus the bundled ``/deep-research``, and turns each into a
``PromptCommand`` (``kind="workflow"``) that directs the model to launch it via
the Workflow tool. Project workflows win over personal ones on a name clash.

Mirrors the skills-integration pattern (``skills_integration.py``); failures
degrade to fewer commands rather than breaking command listing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.workflow.bundled import bundled_workflow_path
from src.workflow.gating import is_workflows_enabled
from src.workflow.sandbox import extract_meta

from .types import Command, PromptCommand

logger = logging.getLogger(__name__)


def _directive(name: str, path: str) -> str:
    return (
        f'Launch the dynamic workflow "{name}" — do NOT do the work yourself. '
        f"Call the Workflow tool with `script_path` set to \"{path}\" and pass the "
        f"user's input as `args` (parse it as JSON if it looks structured, otherwise "
        f"pass it as a string):\n\n$ARGUMENTS"
    )


def _workflow_to_command(path: Path, loaded_from: str) -> Optional[PromptCommand]:
    try:
        meta = extract_meta(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a bad file shouldn't break listing
        logger.debug("skipping invalid workflow %s: %s", path, exc)
        return None
    name = path.stem
    return PromptCommand(
        name=name,
        description=meta.description,
        kind="workflow",
        loaded_from=loaded_from,
        source=loaded_from,
        is_enabled=is_workflows_enabled,
        argument_hint="[args]",
        when_to_use=meta.when_to_use or None,
        markdown_content=_directive(name, str(path)),
    )


def _deep_research_command() -> Optional[PromptCommand]:
    path = bundled_workflow_path("deep_research")
    try:
        meta = extract_meta(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("bundled deep-research unavailable: %s", exc)
        return None
    return PromptCommand(
        name="deep-research",
        description=meta.description,
        kind="workflow",
        loaded_from="bundled",
        source="bundled",
        is_enabled=is_workflows_enabled,
        argument_hint="<question>",
        markdown_content=_directive("deep-research", str(path)),
    )


def _discover_dir(directory: Path, loaded_from: str) -> list[PromptCommand]:
    commands: list[PromptCommand] = []
    try:
        if not directory.is_dir():
            return commands
        for path in sorted(directory.glob("*.py")):
            cmd = _workflow_to_command(path, loaded_from)
            if cmd is not None:
                commands.append(cmd)
    except OSError as exc:
        logger.debug("workflow discovery failed for %s: %s", directory, exc)
    return commands


def load_workflow_commands(cwd: str) -> list[Command]:
    """The ``/workflows`` view + bundled + project + personal workflow commands
    (project wins over personal on a name clash)."""
    from .workflows_command import WORKFLOWS_COMMAND

    commands: list[Command] = [WORKFLOWS_COMMAND]
    deep = _deep_research_command()
    if deep is not None:
        commands.append(deep)

    # Project first so it reserves the name before the personal copy.
    project = _discover_dir(Path(cwd) / ".claude" / "workflows", "project")
    personal = _discover_dir(Path.home() / ".claude" / "workflows", "user")

    seen = {c.name for c in commands}
    for cmd in [*project, *personal]:
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        commands.append(cmd)
    return commands

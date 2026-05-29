"""
Unified command aggregator.

Python port of getCommands(cwd) / loadAllCommands(cwd) from
typescript/src/commands.ts:473-541 — the single entry point every consumer should call
to get the live, availability-filtered command set.

Phase 1 merges builtin commands + filesystem skills (NOT plugins/workflows/dynamic
skills — those subsystems are unported; see commands-gap-analysis.md §4.3). It reads
builtins and skills directly (not the global registry), matching TS.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from .builtins import get_builtin_commands
from .skills_integration import skill_to_prompt_command
from .types import Command, is_command_enabled, meets_availability_requirement

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _load_skill_commands_cached(cwd: str) -> tuple[Command, ...]:
    """
    Load filesystem skills for ``cwd`` and convert them to PromptCommands.

    Cached by cwd because skill discovery does disk I/O + frontmatter parsing.
    Builtins are intentionally NOT cached here (they're cheap and their is_enabled
    gates must stay fresh). Failures degrade to an empty tuple — skills are
    non-critical and must never break command listing (mirrors TS).
    """
    try:
        from ..skills.loader import get_all_skills
        skills = get_all_skills(project_root=cwd)
        return tuple(skill_to_prompt_command(s) for s in skills)
    except Exception as exc:  # noqa: BLE001 — skills are non-critical
        logger.debug("skill loading failed for %s: %s", cwd, exc)
        return ()


def get_commands(
    cwd: str | Path | None = None,
    *,
    is_claude_ai_subscriber: bool = False,
    is_console_user: bool = False,
) -> list[Command]:
    """
    Return commands available to the current user for ``cwd``.

    Merges builtins (fresh) + filesystem skills (cached by cwd), then filters by
    availability + is_enabled FRESH on every call so auth/flag changes take effect
    immediately. De-duplicated by name (builtins own their names — see below).

    Port of commands.ts:500 getCommands(cwd).
    """
    cwd_key = str(cwd) if cwd is not None else str(Path.cwd())

    # Builtins fresh (re-evaluates conditional appends); skills cached by cwd.
    all_commands: list[Command] = [
        *get_builtin_commands(),
        *_load_skill_commands_cached(cwd_key),
    ]

    seen: set[str] = set()
    result: list[Command] = []
    for cmd in all_commands:
        if cmd.name in seen:
            continue  # name already claimed by an earlier command
        # Reserve the name BEFORE filtering: a builtin owns its name even when it is
        # disabled/unavailable, so a same-named skill (enumerated later) can never
        # shadow it. Builtins are enumerated first -> builtins win. This diverges
        # from TS's filter-then-dedupe order on purpose (prevents a user skill named
        # `help`/`clear` from shadowing a core builtin).
        #
        # This assumes filter-gated builtins (is_enabled / availability) are never
        # meant to be *replaced* by a same-named skill. A command that should yield
        # to a skill must instead be omitted from the source list entirely (an
        # "append-gate", like buddy's is_buddy_command_enabled()), so it never
        # reaches this loop and never reserves its name.
        seen.add(cmd.name)
        if not meets_availability_requirement(
            cmd, is_claude_ai_subscriber, is_console_user
        ):
            continue
        if not is_command_enabled(cmd):
            continue
        result.append(cmd)
    return result


def clear_commands_cache() -> None:
    """Clear the memoized skill-command cache. Port of clearCommandsCache()."""
    _load_skill_commands_cached.cache_clear()

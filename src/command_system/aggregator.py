"""
Unified command aggregator.

Python port of getCommands(cwd) / loadAllCommands(cwd) from
typescript/src/commands.ts:473-541 — the single entry point every consumer should call
to get the live, availability-filtered command set.

Phase 1 merges builtin commands + filesystem skills (NOT plugins/workflows/dynamic
skills — those subsystems are unported; see commands-gap-analysis.md §4.3). It reads
builtins and skills directly (not the global registry), matching TS.

Because ``get_commands`` already merges skills into the unified set, it ALSO
provides the "skills are in the unified command set" guarantee that TS's
bootstrap ``load_and_register_skills`` exists to provide — which is why P0-6 is
satisfied here (Option A) without wiring that call into the execution registries.
See ``load_and_register_skills`` and the Phase 3 plan §3 D-6 for the full
rationale.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from .builtins import get_builtin_commands
from .skills_integration import skill_to_prompt_command
from .types import (
    Command,
    CommandType,
    is_command_enabled,
    meets_availability_requirement,
)

logger = logging.getLogger(__name__)


# D-1a (Phase 3 / P0-4 — model-tool exposure). The Python skill loader emits a
# *more granular* ``loaded_from`` than TS: ``user`` / ``project`` / ``managed`` /
# ``plugin`` / ``mcp`` / ``bundled`` (``loader.py:_SOURCE_TO_LOADED_FROM``), where
# TS tags *all* disk-skill-dir commands ``'skills'`` and only policy-managed
# ``'managed'``. So Python's ``user`` and ``project`` are the equivalents of TS's
# ``'skills'``. A *literal* port of ``loadedFrom === 'skills'`` would wrongly drop
# the common-case user/project disk skills that have only an auto-derived
# first-line description. We bridge the vocabulary HERE (inside the two views),
# NOT in the loader — whose ``loaded_from`` other display/logic reads — so the
# blast radius is just these functions. See
# my-docs/get-parity-by-folder/commands-phase3-model-tool-exposure-plan.md §3 D-1a.
SKILLS_DIR_BUCKET: frozenset[str] = frozenset({"skills", "user", "project"})


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


@lru_cache(maxsize=32)
def get_skill_tool_commands(cwd: str | None = None) -> tuple[Command, ...]:
    """ALL prompt-based commands the model may invoke.

    This is the source for the model-facing "# Available Skills" system-prompt
    listing (wired into ``build_full_system_prompt_blocks(skills=...)`` at
    ``src/query/engine.py``). Port of ``getSkillToolCommands(cwd)``
    (``typescript/src/commands.ts:587-605``).

    Filters :func:`get_commands` to prompt commands that are model-invocable,
    non-builtin, and either live in a skill-dir / bundled / commands_DEPRECATED
    bucket OR carry an author-written description / ``when_to_use`` (the
    managed/mcp/plugin escape hatch — see §2 of the plan doc). Uses the D-1a
    :data:`SKILLS_DIR_BUCKET` bridge, not the raw TS ``'skills'`` literal.

    Unlike :func:`get_slash_command_tool_skills`, this is NOT wrapped in
    try/except (matching TS): :func:`get_commands` already swallows skill-load
    failures, and the system-prompt assembler wraps the whole call.

    cwd-memoized (the listing is stable within a session; R3 accepts staleness as
    TS-parity). :func:`clear_commands_cache` resets THIS view's cwd cache. Note
    the *rendered* "# Available Skills" prose is cached one layer down too —
    ``_prompt_cache["skills"]`` (SESSION scope, in ``context_system``), cleared
    by ``clear_context_caches()``. A full mid-session refresh of what the model
    actually sees needs BOTH; ``clear_commands_cache`` alone is not sufficient.
    """
    result: list[Command] = []
    for cmd in get_commands(cwd):
        if cmd.command_type != CommandType.PROMPT:
            continue
        if getattr(cmd, "disable_model_invocation", False):
            continue
        if getattr(cmd, "source", "builtin") == "builtin":
            continue
        loaded_from = getattr(cmd, "loaded_from", "builtin")
        in_bucket = loaded_from in SKILLS_DIR_BUCKET or loaded_from in (
            "bundled",
            "commands_DEPRECATED",
        )
        if (
            in_bucket
            or getattr(cmd, "has_user_specified_description", False)
            or getattr(cmd, "when_to_use", None)
        ):
            result.append(cmd)
    return tuple(result)


@lru_cache(maxsize=32)
def get_slash_command_tool_skills(cwd: str | None = None) -> tuple[Command, ...]:
    """Skills-only subset used for SlashCommand-tool counts / init (NOT a tool).

    Port of ``getSlashCommandToolSkills(cwd)``
    (``typescript/src/commands.ts:610-632``). Note the deliberate TS asymmetries
    vs :func:`get_skill_tool_commands`, preserved verbatim:

    * does **not** exclude ``disable_model_invocation`` — there it is an
      *inclusion* clause (a disabled-for-model skill still counts here);
    * **always** requires ``has_user_specified_description or when_to_use``;
    * the bucket adds ``'plugin'`` but omits ``'commands_DEPRECATED'``;
    * the whole body is wrapped in try/except returning ``()`` (skills are
      non-critical — matches the ``_load_skill_commands_cached`` house style of
      degrading to empty on failure).

    cwd-memoized; cleared by :func:`clear_commands_cache`.
    """
    try:
        result: list[Command] = []
        for cmd in get_commands(cwd):
            if cmd.command_type != CommandType.PROMPT:
                continue
            if getattr(cmd, "source", "builtin") == "builtin":
                continue
            if not (
                getattr(cmd, "has_user_specified_description", False)
                or getattr(cmd, "when_to_use", None)
            ):
                continue
            loaded_from = getattr(cmd, "loaded_from", "builtin")
            in_bucket = loaded_from in SKILLS_DIR_BUCKET or loaded_from in (
                "plugin",
                "bundled",
            )
            if in_bucket or getattr(cmd, "disable_model_invocation", False):
                result.append(cmd)
        return tuple(result)
    except Exception as exc:  # noqa: BLE001 — skills are non-critical (TS parity)
        logger.debug("get_slash_command_tool_skills failed for %s: %s", cwd, exc)
        return ()


def clear_commands_cache() -> None:
    """Clear all memoized command caches. Port of ``clearCommandsCache()``.

    Covers the skill-load cache AND the two P0-4 model-tool views (R3: their
    cwd-memoization is why a mid-session skill change needs this to show up).

    Scope boundary: this clears only the *command-aggregation* caches in this
    module. It does NOT touch the downstream prompt-assembly session cache
    (``_prompt_cache["skills"]`` in ``context_system``) that holds the rendered
    "# Available Skills" prose. Refreshing the model-facing listing mid-session
    requires this call PLUS ``clear_context_caches()`` — kept decoupled on
    purpose so the aggregator doesn't reach across into the prompt layer.
    """
    _load_skill_commands_cached.cache_clear()
    get_skill_tool_commands.cache_clear()
    get_slash_command_tool_skills.cache_clear()

"""Bundled ``/loop`` skill — port of ``bundled/loop.ts``.

Pure argument-parsing logic + two prompt builders. Three modes:
``fixed-prompt``, ``fixed-maintenance``, ``dynamic-prompt``,
``dynamic-maintenance``. Routes to ``buildFixedPrompt`` or
``buildDynamicPrompt`` based on whether the user supplied an interval.

The TS feature gate (``isKairosCronEnabled``) has no Python equivalent
yet; the ``is_enabled`` callback returns True so the skill is always
visible. TODO: wire to a real Python feature gate when one lands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill


# Cron-tool name constants. The Python tool registry exposes
# ``CronCreate`` / ``CronDelete`` (see ``src.tool_system.tools.cron``)
# which is the same naming the prompt body references.
_CRON_CREATE_TOOL_NAME = "CronCreate"
_CRON_DELETE_TOOL_NAME = "CronDelete"

# Recurring-job auto-expiration window (mirrors TS DEFAULT_MAX_AGE_DAYS).
_DEFAULT_MAX_AGE_DAYS = 7

_DYNAMIC_MIN_DELAY = "1 minute"
_DYNAMIC_MAX_DELAY = "1 hour"


_MAINTENANCE_PROMPT = """Scheduled maintenance loop iteration.

If .claude/loop.md exists, read it and follow it.
Otherwise, if ~/.claude/loop.md exists, read it and follow it.
Otherwise:
- continue any unfinished work from the conversation
- tend to the current branch's pull request: review comments, failed CI runs, merge conflicts
- run cleanup passes such as bug hunts or simplification when nothing else is pending

Do not start new initiatives outside that scope.
Irreversible actions such as pushing or deleting only proceed when they continue something the transcript already authorized."""


@dataclass(frozen=True)
class ParsedLoopArgs:
    mode: str  # "dynamic-prompt" | "dynamic-maintenance" | "fixed-prompt" | "fixed-maintenance"
    interval: Optional[str] = None
    prompt: Optional[str] = None


# ----------------------------------------------------------------------
# Argument parser (verbatim port of TS parseLoopArgs et al.)
# ----------------------------------------------------------------------


def _normalize_interval_unit(raw_unit: str) -> str | None:
    unit = raw_unit.lower()
    if unit in {"s", "sec", "secs", "second", "seconds"}:
        return "s"
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return "m"
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return "h"
    if unit in {"d", "day", "days"}:
        return "d"
    return None


def _parse_interval_token(token: str) -> str | None:
    """Match ``<int><unit>`` (e.g. ``5m``, ``2hours``); return canonical form."""
    m = re.match(r"^(\d+)\s*([a-zA-Z]+)$", token.strip())
    if not m:
        return None
    try:
        value = int(m.group(1), 10)
    except ValueError:
        return None
    if value < 1:
        return None
    unit = _normalize_interval_unit(m.group(2))
    if unit is None:
        return None
    return f"{value}{unit}"


def _parse_trailing_every_clause(text: str) -> tuple[str, str] | None:
    """Match ``... every <int> <unit>`` and return ``(prompt, interval)``."""
    m = re.match(r"^(.*?)(?:\s+every\s+)(\d+)\s*([a-zA-Z]+)\s*$", text, re.IGNORECASE)
    if not m:
        return None
    interval = _parse_interval_token(f"{m.group(2)}{m.group(3)}")
    if not interval:
        return None
    return m.group(1).strip(), interval


def parse_loop_args(args: str) -> ParsedLoopArgs:
    """Port of TS ``parseLoopArgs``.

    Routing:
    - empty args                   → ``dynamic-maintenance``
    - bare interval (``5m``)       → ``fixed-maintenance``
    - ``<interval> <prompt>``      → ``fixed-prompt``
    - ``<prompt> every <interval>`` → ``fixed-prompt``
    - anything else                → ``dynamic-prompt``
    """
    trimmed = args.strip()
    if not trimmed:
        return ParsedLoopArgs(mode="dynamic-maintenance")

    bare = _parse_interval_token(trimmed)
    if bare:
        return ParsedLoopArgs(mode="fixed-maintenance", interval=bare)

    tokens = trimmed.split()
    first_token, rest_tokens = tokens[0], tokens[1:]
    leading = _parse_interval_token(first_token)
    if leading:
        prompt = " ".join(rest_tokens).strip()
        if not prompt:
            return ParsedLoopArgs(mode="fixed-maintenance", interval=leading)
        return ParsedLoopArgs(mode="fixed-prompt", interval=leading, prompt=prompt)

    trailing = _parse_trailing_every_clause(trimmed)
    if trailing:
        prompt, interval = trailing
        if not prompt:
            return ParsedLoopArgs(mode="fixed-maintenance", interval=interval)
        return ParsedLoopArgs(mode="fixed-prompt", interval=interval, prompt=prompt)

    return ParsedLoopArgs(mode="dynamic-prompt", prompt=trimmed)


# ----------------------------------------------------------------------
# Prompt builders (verbatim ports of buildFixedPrompt / buildDynamicPrompt)
# ----------------------------------------------------------------------


def _build_fixed_prompt(parsed: ParsedLoopArgs) -> str:
    if parsed.prompt:
        target_instructions = (
            "Use this prompt verbatim for both the immediate run and the recurring scheduled task:\n"
            "\n--- BEGIN PROMPT ---\n"
            f"{parsed.prompt}\n"
            "--- END PROMPT ---\n"
        )
    else:
        target_instructions = (
            "This is a maintenance loop with no explicit prompt.\n"
            "\nFor the recurring scheduled task, use this exact maintenance prompt body:\n"
            "\n--- BEGIN MAINTENANCE PROMPT ---\n"
            f"{_MAINTENANCE_PROMPT}\n"
            "--- END MAINTENANCE PROMPT ---\n"
        )

    return (
        "# /loop — fixed recurring interval\n"
        "\n"
        "The user invoked /loop with a fixed interval.\n"
        "\n"
        f"Requested interval: {parsed.interval}\n"
        "\n"
        f"{target_instructions}\n"
        "## Instructions\n"
        "\n"
        "1. Convert the requested interval to a recurring cron expression.\n"
        "   - Supported suffixes: s, m, h, d.\n"
        "   - Seconds must be rounded up to the nearest minute because cron has minute granularity.\n"
        "   - If the requested interval does not map cleanly to cron cadence, choose the nearest clean recurring interval and tell the user what you picked.\n"
        f"2. Call {_CRON_CREATE_TOOL_NAME} with:\n"
        "   - the recurring cron expression\n"
        "   - the effective prompt body above\n"
        "   - recurring: true\n"
        "   - durable: false\n"
        f"3. Briefly confirm what was scheduled, the cron expression, the human cadence, that recurring tasks auto-expire after {_DEFAULT_MAX_AGE_DAYS} days, and that the user can cancel sooner with {_CRON_DELETE_TOOL_NAME} using the returned job ID.\n"
        "4. Immediately execute the effective prompt now — do not wait for the first cron fire.\n"
        "   - If the effective prompt starts with a slash command, invoke it via the Skill tool.\n"
        "   - Otherwise, act on it directly.\n"
    )


def _build_dynamic_prompt(parsed: ParsedLoopArgs) -> str:
    if parsed.prompt:
        effective_instructions = (
            "Use this prompt verbatim as the effective prompt for this iteration:\n"
            "\n--- BEGIN PROMPT ---\n"
            f"{parsed.prompt}\n"
            "--- END PROMPT ---\n"
        )
    else:
        effective_instructions = (
            "This is a maintenance loop with no explicit prompt.\n"
            "\nDetermine the effective prompt in this order:\n"
            "1. If .claude/loop.md exists, read it and use it.\n"
            "2. Otherwise, if ~/.claude/loop.md exists, read it and use it.\n"
            "3. Otherwise, use this built-in maintenance prompt:\n"
            "\n--- BEGIN MAINTENANCE PROMPT ---\n"
            f"{_MAINTENANCE_PROMPT}\n"
            "--- END MAINTENANCE PROMPT ---\n"
        )

    reschedule_prompt = f"/loop {parsed.prompt}" if parsed.prompt else "/loop"

    return (
        "# /loop — dynamic rescheduling\n"
        "\n"
        "The user invoked /loop without a fixed interval.\n"
        "\n"
        f"{effective_instructions}\n"
        "## Instructions\n"
        "\n"
        "1. Execute the effective prompt now.\n"
        "   - If it starts with a slash command, invoke it via the Skill tool.\n"
        "   - Otherwise, act on it directly.\n"
        f"2. After the work finishes, choose the next delay dynamically between {_DYNAMIC_MIN_DELAY} and {_DYNAMIC_MAX_DELAY}.\n"
        "   - Use shorter delays while active work is progressing or likely to change soon.\n"
        "   - Use longer delays when the situation is quiet or stable.\n"
        "3. Briefly tell the user the chosen delay and the reason.\n"
        f"4. Schedule exactly one session-only follow-up run with {_CRON_CREATE_TOOL_NAME}.\n"
        "   - Use recurring: false.\n"
        "   - Use durable: false.\n"
        "   - Pin the cron expression to a specific future local-time minute that matches the chosen delay.\n"
        "   - Set the scheduled prompt to this exact text so the next iteration stays in dynamic mode:\n"
        "\n--- BEGIN SCHEDULED PROMPT ---\n"
        f"{reschedule_prompt}\n"
        "--- END SCHEDULED PROMPT ---\n"
        "\n"
        "5. Confirm the next run time and the returned job ID.\n"
        "6. Do not create a recurring cron for this mode.\n"
    )


def _build_loop_prompt(args: str) -> str:
    parsed = parse_loop_args(args)
    if parsed.mode in ("fixed-prompt", "fixed-maintenance"):
        return _build_fixed_prompt(parsed)
    return _build_dynamic_prompt(parsed)


def _is_loop_enabled() -> bool:
    """Stub for TS' ``isKairosCronEnabled``.

    Returns True so /loop is always discoverable in the catalogue.
    TODO: wire to a real Python feature gate (e.g., a settings flag or
    env-var driven check) when the runtime gains one.
    """
    return True


def register_loop_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="loop",
            description=(
                "Run a prompt on a fixed interval or dynamically reschedule "
                "it, including bare maintenance-mode loops."
            ),
            when_to_use=(
                "When the user wants to poll for status, babysit a workflow, "
                "run recurring maintenance, or keep re-running a prompt "
                "within the current session."
            ),
            argument_hint="[interval] [prompt]",
            user_invocable=True,
            is_enabled=_is_loop_enabled,
            get_prompt_for_command=_build_loop_prompt,
        )
    )

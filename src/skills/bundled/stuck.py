"""Bundled ``/stuck`` skill — minimal "you're stuck, change tactics" prompt.

The TS module (``bundled/stuck.ts``) is currently a no-op stub. The
Python port keeps a small but useful prompt so the skill is discoverable
in the catalogue and gives the user something actionable when invoked.
"""

from __future__ import annotations

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill


_STUCK_PROMPT = """# /stuck — Reset and Re-Approach

You appear to be stuck. Pause the current line of work and reassess.

## Step 1: Diagnose
- Summarize what you were trying to do, what you tried, and what failed.
- Identify the *first* assumption that turned out to be wrong.

## Step 2: Pivot
- Pick a different approach. Examples: read files you haven't read, run a different diagnostic command, ask the user a clarifying question, or search for prior art in the codebase.
- Avoid retrying the same thing with minor variations.

## Step 3: Report
- Tell the user concisely: what was failing, what new approach you're taking, and why.

If the user supplied additional context with /stuck, treat it as guidance for the pivot.
"""


def _build_stuck_prompt(args: str) -> str:
    if not args:
        return _STUCK_PROMPT
    return f"{_STUCK_PROMPT}\n\n## User Context\n\n{args}\n"


def register_stuck_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="stuck",
            description=(
                "Reset when stuck: diagnose what failed, pivot to a different "
                "approach, and report back."
            ),
            user_invocable=True,
            get_prompt_for_command=_build_stuck_prompt,
        )
    )

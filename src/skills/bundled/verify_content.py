"""Bundled ``/verify-content`` skill — sanity-check the user's intent
against the most recent edits.

The TS module (``bundled/verifyContent.ts``) is build-time inlined .md
content; the Python port keeps a concise actionable prompt here so the
skill is discoverable in the catalogue without requiring extra .md
asset shipping.
"""

from __future__ import annotations

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill


_VERIFY_PROMPT = """# /verify-content — Verify Recent Edits Match Intent

Review the most recent changes (or the files the user names) and verify
they match what was actually requested.

## Step 1: Recall the request
- State, in your own words, what the user asked for.
- If multiple iterations occurred, focus on the latest stated goal.

## Step 2: Read the changes
- Read each touched file fresh. Do not rely on what you remember writing.
- For each diff hunk, ask: does this implement (or move toward) the goal?

## Step 3: Surface mismatches
- Note any change that doesn't serve the stated goal — accidental edits, half-completed transformations, leftover debug code, comments that no longer match the code.
- Note any part of the request that is *not* yet implemented.

## Step 4: Report
- Lead with a one-line verdict: matches / partial / mismatch.
- Then list the specific file/line locations of any mismatches and what should be done about each.

If the user supplied additional context with /verify-content, treat it as the authoritative description of intent.
"""


def _build_verify_prompt(args: str) -> str:
    if not args:
        return _VERIFY_PROMPT
    return f"{_VERIFY_PROMPT}\n\n## User-Supplied Intent\n\n{args}\n"


def register_verify_content_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="verify-content",
            description=(
                "Verify the most recent edits actually match the user's "
                "stated intent; surface mismatches and gaps."
            ),
            user_invocable=True,
            get_prompt_for_command=_build_verify_prompt,
        )
    )

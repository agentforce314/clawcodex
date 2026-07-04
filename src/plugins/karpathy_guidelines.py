"""Bundled built-in plugin: karpathy-guidelines (PLUGINS-1).

Port of ``typescript/src/plugins/bundled/karpathyGuidelines.ts``. The prompt
is VERBATIM (model-facing, eval-tuned prose — extracted mechanically from
the TS source; do not paraphrase). ``defaultEnabled`` is False: the plugin
appears in the built-in registry but its skill command is exposed only when
the user enables it.
"""

from __future__ import annotations

from src.skills.model import Skill

from .builtin_plugins import register_builtin_plugin
from .types import BuiltinPluginDefinition

KARPATHY_GUIDELINES_PROMPT = '# CLAUDE.md\n\nBehavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.\n\n**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.\n\n## 1. Think Before Coding\n\n**Don\'t assume. Don\'t hide confusion. Surface tradeoffs.**\n\nBefore implementing:\n- State your assumptions explicitly. If uncertain, ask.\n- If multiple interpretations exist, present them - don\'t pick silently.\n- If a simpler approach exists, say so. Push back when warranted.\n- If something is unclear, stop. Name what\'s confusing. Ask.\n\n## 2. Simplicity First\n\n**Minimum code that solves the problem. Nothing speculative.**\n\n- No features beyond what was asked.\n- No abstractions for single-use code.\n- No "flexibility" or "configurability" that wasn\'t requested.\n- No error handling for impossible scenarios.\n- If you write 200 lines and it could be 50, rewrite it.\n\nAsk yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.\n\n## 3. Surgical Changes\n\n**Touch only what you must. Clean up only your own mess.**\n\nWhen editing existing code:\n- Don\'t "improve" adjacent code, comments, or formatting.\n- Don\'t refactor things that aren\'t broken.\n- Match existing style, even if you\'d do it differently.\n- If you notice unrelated dead code, mention it - don\'t delete it.\n\nWhen your changes create orphans:\n- Remove imports/variables/functions that YOUR changes made unused.\n- Don\'t remove pre-existing dead code unless asked.\n\nThe test: Every changed line should trace directly to the user\'s request.\n\n## 4. Goal-Driven Execution\n\n**Define success criteria. Loop until verified.**\n\nTransform tasks into verifiable goals:\n- "Add validation" -> "Write tests for invalid inputs, then make them pass"\n- "Fix the bug" -> "Write a test that reproduces it, then make it pass"\n- "Refactor X" -> "Ensure tests pass before and after"\n\nFor multi-step tasks, state a brief plan:\n```\n1. [Step] -> verify: [check]\n2. [Step] -> verify: [check]\n3. [Step] -> verify: [check]\n```\n\nStrong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.\n\n---\n\n**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.\n'


def register_karpathy_guidelines_plugin() -> None:
    """The registerKarpathyGuidelinesPlugin analog."""
    register_builtin_plugin(BuiltinPluginDefinition(
        name="karpathy-guidelines",
        description=(
            "Optional coding guidelines that favor simple, surgical, "
            "verifiable changes."
        ),
        version="1.0.0",
        default_enabled=False,
        skills=[
            # A real Skill instance — get_builtin_plugin_skill_commands
            # filters with isinstance(skill, Skill); dict defs are skipped.
            Skill(
                name="karpathy-guidelines",
                description=(
                    "Apply coding guidelines that reduce common LLM "
                    "implementation mistakes."
                ),
                content=KARPATHY_GUIDELINES_PROMPT,
                source="karpathy-guidelines@builtin",
                loaded_from="plugin",
                user_invocable=True,
                when_to_use=(
                    "Use when writing, reviewing, or refactoring code, "
                    "especially when a task could become overcomplicated or "
                    "needs careful verification."
                ),
                version="1.0.0",
            )
        ],
    ))

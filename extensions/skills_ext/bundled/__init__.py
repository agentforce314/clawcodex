"""
Bundled Skill Extension - POS to Agent Converter

Mirrors the src/skills/bundled/ pattern for clawcodex-specific bundled skills.
POS to Agent conversion skill registered here for skills_ext integration.
"""

from __future__ import annotations

from typing import Any, Callable

from extensions.skills.bundled_skills import BundledSkillDefinition, register_bundled_skill
from extensions.pos_converter.convert_pos_skill import get_prompt_for_command


def register_convert_pos_skill() -> None:
    """Register the convert-pos-to-agent bundled skill."""
    register_bundled_skill(
        BundledSkillDefinition(
            name="convert-pos-to-agent",
            description=(
                "Convert a professional workflow system (POS) into a reusable Agent. "
                "Takes SDK specifications and business requirements, then produces "
                "an AgentDefinition with grouped Skills, SKILL.md files, and optional "
                "agent persistence file for long-term use."
            ),
            get_prompt_for_command=get_prompt_for_command,
            aliases=["pos-to-agent"],
            when_to_use=(
                "When you need to convert a POS workflow into an agent. "
                "Input: SDK spec (OpenAPI URL/JSON or method list) + requirements."
            ),
            argument_hint="<sdk_spec> [--requirements '<requirements>']",
            allowed_tools=[],
            user_invocable=True,
            context="inline",
        )
    )


__all__ = [
    "register_convert_pos_skill",
]
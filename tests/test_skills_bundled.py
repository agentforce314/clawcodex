from __future__ import annotations

import pytest
from src.skills.bundled_skills import (
    BundledSkillDefinition,
    clear_bundled_skills,
    get_bundled_skills,
    register_bundled_skill,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    clear_bundled_skills()
    yield  # type: ignore[misc]
    clear_bundled_skills()


class TestBundledSkills:
    def test_register_and_get(self) -> None:
        register_bundled_skill(
            BundledSkillDefinition(
                name="test-skill",
                description="A test skill",
                get_prompt_for_command=lambda args: f"prompt: {args}",
            )
        )
        skills = get_bundled_skills()
        assert len(skills) == 1
        assert skills[0].name == "test-skill"
        assert skills[0].description == "A test skill"
        assert skills[0].source == "bundled"
        assert skills[0].loaded_from == "bundled"

    def test_multiple_skills(self) -> None:
        for i in range(3):
            register_bundled_skill(
                BundledSkillDefinition(
                    name=f"skill-{i}",
                    description=f"Skill {i}",
                    get_prompt_for_command=lambda args: args,
                )
            )
        assert len(get_bundled_skills()) == 3

    def test_clear(self) -> None:
        register_bundled_skill(
            BundledSkillDefinition(
                name="temp",
                description="temp",
                get_prompt_for_command=lambda a: a,
            )
        )
        assert len(get_bundled_skills()) == 1
        clear_bundled_skills()
        assert len(get_bundled_skills()) == 0

    def test_get_returns_copy(self) -> None:
        register_bundled_skill(
            BundledSkillDefinition(
                name="s1",
                description="d",
                get_prompt_for_command=lambda a: a,
            )
        )
        skills1 = get_bundled_skills()
        skills2 = get_bundled_skills()
        assert skills1 is not skills2

    def test_skill_properties(self) -> None:
        register_bundled_skill(
            BundledSkillDefinition(
                name="advanced",
                description="Advanced skill",
                get_prompt_for_command=lambda a: "prompt",
                aliases=["adv"],
                when_to_use="when testing",
                argument_hint="<arg>",
                allowed_tools=["Bash"],
                disable_model_invocation=True,
                user_invocable=False,
            )
        )
        skill = get_bundled_skills()[0]
        assert skill.when_to_use == "when testing"
        assert skill.argument_hint == "<arg>"
        assert "Bash" in skill.allowed_tools
        assert skill.disable_model_invocation is True
        assert skill.user_invocable is False
        assert skill.is_hidden is True

    def test_get_prompt(self) -> None:
        register_bundled_skill(
            BundledSkillDefinition(
                name="prompt-test",
                description="test",
                get_prompt_for_command=lambda args: f"Hello {args}",
            )
        )
        skill = get_bundled_skills()[0]
        assert skill.get_prompt("world") == "Hello world"

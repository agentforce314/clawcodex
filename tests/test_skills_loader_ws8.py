from __future__ import annotations

import os
import pytest
from pathlib import Path

from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    create_skill_command,
    get_conditional_skill_count,
    get_dynamic_skills,
    get_skill_dir_commands,
    load_skills_from_skills_dir,
    parse_skill_frontmatter_fields,
)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    clear_skill_caches()
    clear_dynamic_skills()
    yield  # type: ignore[misc]
    clear_skill_caches()
    clear_dynamic_skills()


class TestParseSkillFrontmatterFields:
    def test_basic_fields(self) -> None:
        fm = {"description": "A skill", "user-invocable": True}
        result = parse_skill_frontmatter_fields(fm, "body", "test-skill")
        assert result["description"] == "A skill"
        assert result["user_invocable"] is True

    def test_default_description(self) -> None:
        result = parse_skill_frontmatter_fields({}, "", "my-skill")
        assert "my-skill" in result["description"]

    def test_disable_model_invocation(self) -> None:
        fm = {"disable-model-invocation": "true"}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert result["disable_model_invocation"] is True

    def test_allowed_tools_list(self) -> None:
        fm = {"allowed-tools": ["Bash", "Read"]}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert result["allowed_tools"] == ["Bash", "Read"]

    def test_allowed_tools_string(self) -> None:
        fm = {"allowed-tools": "Bash, Read"}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert "Bash" in result["allowed_tools"]
        assert "Read" in result["allowed_tools"]

    def test_paths_parsing(self) -> None:
        fm = {"paths": ["src/**", "lib/**"]}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert result["paths"] is not None
        assert "src" in result["paths"]

    def test_model_inherit(self) -> None:
        fm = {"model": "inherit"}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert result["model"] is None

    def test_fork_context(self) -> None:
        fm = {"context": "fork"}
        result = parse_skill_frontmatter_fields(fm, "", "s")
        assert result["execution_context"] == "fork"


class TestCreateSkillCommand:
    def test_creates_skill(self) -> None:
        skill = create_skill_command(
            skill_name="test",
            display_name=None,
            description="Test skill",
            has_user_specified_description=True,
            markdown_content="# Test\nDo stuff",
            allowed_tools=[],
            argument_hint=None,
            argument_names=[],
            when_to_use=None,
            version=None,
            model=None,
            disable_model_invocation=False,
            user_invocable=True,
            source="projectSettings",
            base_dir="/path/to/skill",
            loaded_from="skills",
        )
        assert skill.name == "test"
        assert skill.description == "Test skill"
        assert skill.content_length > 0
        assert skill.loaded_from == "skills"
        assert skill.is_hidden is False
        assert skill.base_dir == "/path/to/skill"


class TestLoadSkillsFromSkillsDir:
    def test_loads_skills(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Test skill\n---\n# My Skill\nDo stuff"
        )
        skills = load_skills_from_skills_dir(str(tmp_path), "projectSettings")
        assert len(skills) == 1
        assert skills[0].name == "my-skill"
        assert skills[0].description == "Test skill"

    def test_nested_skills(self, tmp_path: Path) -> None:
        category = tmp_path / "category" / "nested-skill"
        category.mkdir(parents=True)
        (category / "SKILL.md").write_text(
            "---\ndescription: Nested\n---\nNested content"
        )
        skills = load_skills_from_skills_dir(str(tmp_path), "projectSettings")
        assert len(skills) == 1
        assert "nested-skill" in skills[0].name

    def test_empty_dir(self, tmp_path: Path) -> None:
        skills = load_skills_from_skills_dir(str(tmp_path), "projectSettings")
        assert len(skills) == 0

    def test_nonexistent_dir(self) -> None:
        skills = load_skills_from_skills_dir("/nonexistent/path", "projectSettings")
        assert len(skills) == 0


class TestGetSkillDirCommands:
    def test_caching(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "s1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\ndescription: S1\n---\nContent")

        skills1 = get_skill_dir_commands(str(tmp_path))
        skills2 = get_skill_dir_commands(str(tmp_path))
        assert len(skills1) == len(skills2)

    def test_conditional_skills_excluded(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "conditional"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Conditional\npaths:\n  - src/*.py\n---\nConditional"
        )
        skills = get_skill_dir_commands(str(tmp_path))
        assert all(not s.is_conditional for s in skills)
        assert get_conditional_skill_count() >= 0


class TestDynamicSkills:
    def test_initially_empty(self) -> None:
        assert get_dynamic_skills() == []

    def test_clear_dynamic(self) -> None:
        clear_dynamic_skills()
        assert get_dynamic_skills() == []

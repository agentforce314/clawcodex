import pytest

from src.skills.bundled_skills import (
    BundledSkillDefinition,
    SkillValidationError,
    clear_bundled_skills,
    get_bundled_skill_by_name,
    get_bundled_skills,
    register_bundled_skill,
    skill_from_mcp_tool,
    validate_skill,
    validate_skill_definition,
)
from src.skills.model import Skill


@pytest.fixture(autouse=True)
def _clean():
    clear_bundled_skills()
    yield
    clear_bundled_skills()


class TestValidateSkillDefinition:
    def test_valid(self):
        defn = BundledSkillDefinition(
            name="my-skill",
            description="A good skill",
            get_prompt_for_command=lambda a: a,
        )
        errors = validate_skill_definition(defn)
        assert errors == []

    def test_empty_name(self):
        defn = BundledSkillDefinition(
            name="",
            description="desc",
            get_prompt_for_command=lambda a: a,
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "name" for e in errors)

    def test_invalid_name_chars(self):
        defn = BundledSkillDefinition(
            name="invalid name!",
            description="desc",
            get_prompt_for_command=lambda a: a,
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "name" for e in errors)

    def test_name_starts_with_digit(self):
        defn = BundledSkillDefinition(
            name="1bad",
            description="desc",
            get_prompt_for_command=lambda a: a,
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "name" for e in errors)

    def test_name_with_colons(self):
        defn = BundledSkillDefinition(
            name="ns:sub:skill",
            description="desc",
            get_prompt_for_command=lambda a: a,
        )
        assert validate_skill_definition(defn) == []

    def test_empty_description(self):
        defn = BundledSkillDefinition(
            name="my-skill",
            description="",
            get_prompt_for_command=lambda a: a,
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "description" for e in errors)

    def test_invalid_context(self):
        defn = BundledSkillDefinition(
            name="my-skill",
            description="desc",
            get_prompt_for_command=lambda a: a,
            context="invalid",
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "context" for e in errors)

    def test_valid_fork_context(self):
        defn = BundledSkillDefinition(
            name="my-skill",
            description="desc",
            get_prompt_for_command=lambda a: a,
            context="fork",
        )
        assert validate_skill_definition(defn) == []

    def test_empty_alias(self):
        defn = BundledSkillDefinition(
            name="my-skill",
            description="desc",
            get_prompt_for_command=lambda a: a,
            aliases=["good", ""],
        )
        errors = validate_skill_definition(defn)
        assert any(e.field == "aliases" for e in errors)


class TestValidateSkill:
    def test_valid(self):
        skill = Skill(name="test", description="desc")
        assert validate_skill(skill) == []

    def test_empty_name(self):
        skill = Skill(name="", description="desc")
        errors = validate_skill(skill)
        assert any(e.field == "name" for e in errors)

    def test_empty_description(self):
        skill = Skill(name="test", description="")
        errors = validate_skill(skill)
        assert any(e.field == "description" for e in errors)

    def test_bad_context(self):
        skill = Skill(name="test", description="desc", context="bad")
        errors = validate_skill(skill)
        assert any(e.field == "context" for e in errors)


class TestSkillFromMcpTool:
    def test_basic(self):
        skill = skill_from_mcp_tool("myserver", "read_file", "Reads a file")
        assert skill.name == "mcp:myserver:read_file"
        assert skill.source == "mcp:myserver"
        assert skill.loaded_from == "mcp"
        assert "mcp__myserver__read_file" in skill.allowed_tools

    def test_prompt(self):
        skill = skill_from_mcp_tool("srv", "tool1", "Does things")
        prompt = skill.get_prompt("some args")
        assert "tool1" in prompt
        assert "srv" in prompt
        assert "some args" in prompt

    def test_empty_description(self):
        skill = skill_from_mcp_tool("srv", "tool1", "")
        assert "MCP tool" in skill.description

    def test_input_schema(self):
        schema = {
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "required": ["path"],
        }
        skill = skill_from_mcp_tool("fs", "read", "Read file", input_schema=schema)
        assert skill.argument_hint is not None
        assert "<path>" in skill.argument_hint
        assert "[encoding]" in skill.argument_hint
        assert "path" in skill.argument_names
        assert "encoding" in skill.argument_names

    def test_no_schema(self):
        skill = skill_from_mcp_tool("srv", "tool", "desc", input_schema=None)
        assert skill.argument_hint is None
        assert skill.argument_names == []

    def test_empty_schema(self):
        skill = skill_from_mcp_tool("srv", "tool", "desc", input_schema={})
        assert skill.argument_hint is None


class TestGetBundledSkillByName:
    def test_find_by_name(self):
        register_bundled_skill(BundledSkillDefinition(
            name="finder",
            description="Find things",
            get_prompt_for_command=lambda a: a,
        ))
        skill = get_bundled_skill_by_name("finder")
        assert skill is not None
        assert skill.name == "finder"

    def test_find_by_alias(self):
        register_bundled_skill(BundledSkillDefinition(
            name="finder",
            description="Find things",
            get_prompt_for_command=lambda a: a,
            aliases=["find", "search"],
        ))
        skill = get_bundled_skill_by_name("search")
        assert skill is not None
        assert skill.name == "finder"

    def test_not_found(self):
        assert get_bundled_skill_by_name("nonexistent") is None

"""Tests for R2-WS-5: Full system prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from src.context_system.prompt_assembly import (
    _IDENTITY_PROMPT,
    _INTRO_SECTION,
    _SYSTEM_SECTION,
    _DOING_TASKS_SECTION,
    _ACTIONS_SECTION,
    _USING_TOOLS_SECTION,
    _TONE_STYLE_SECTION,
    _OUTPUT_EFFICIENCY_SECTION,
    _PLAN_MODE_PROMPT,
    _NON_INTERACTIVE_PROMPT,
    build_full_system_prompt,
    get_system_prompt_cache,
)


@dataclass
class MockTool:
    name: str = "ReadFile"
    description: str = "Read a file from disk"

    def prompt(self) -> str:
        return f"Use {self.name} to read files."


@dataclass
class MockAgent:
    agent_type: str = "general-purpose"
    when_to_use: str = "For general purpose tasks"


@dataclass
class MockSkill:
    name: str = "echo-arg"
    description: str = "Echo the argument"


@dataclass
class MockMcpServer:
    name: str = "filesystem"


class TestBuildFullSystemPrompt:
    def setup_method(self):
        cache = get_system_prompt_cache()
        cache.invalidate_all()

    def test_basic_prompt_has_intro(self):
        """Module 1: Intro section matches TS getSimpleIntroSection()."""
        prompt = build_full_system_prompt(use_cache=False)
        assert "interactive agent" in prompt
        assert "software engineering tasks" in prompt

    def test_identity_prompt_backward_compat(self):
        """_IDENTITY_PROMPT is an alias for _INTRO_SECTION."""
        assert _IDENTITY_PROMPT is _INTRO_SECTION

    def test_has_all_seven_static_modules(self):
        """All 7 TS system prompt modules are present."""
        prompt = build_full_system_prompt(use_cache=False)
        # Module 1: Intro
        assert "interactive agent" in prompt
        # Module 2: System
        assert "# System" in prompt
        # Module 3: Doing tasks
        assert "# Doing tasks" in prompt
        # Module 4: Actions
        assert "# Executing actions with care" in prompt
        # Module 5: Using tools
        assert "# Using your tools" in prompt
        assert "Read instead of cat" in prompt
        # Module 6: Tone and style
        assert "# Tone and style" in prompt
        # Module 7: Communicating with the user
        assert "# Communicating with the user" in prompt

    def test_basic_prompt_has_environment(self):
        prompt = build_full_system_prompt(cwd="/tmp/test", use_cache=False)
        assert "/tmp/test" in prompt
        assert "OS:" in prompt
        assert "Date:" in prompt

    def test_custom_system_prompt_overrides(self):
        prompt = build_full_system_prompt(custom_system_prompt="Custom prompt only")
        assert prompt == "Custom prompt only"
        assert "Claude" not in prompt

    def test_custom_system_prompt_with_append(self):
        prompt = build_full_system_prompt(
            custom_system_prompt="Custom",
            append_system_prompt="Extra",
        )
        assert "Custom" in prompt
        assert "Extra" in prompt

    def test_append_system_prompt(self):
        prompt = build_full_system_prompt(
            append_system_prompt="Additional instructions",
            use_cache=False,
        )
        assert "Additional instructions" in prompt

    def test_with_tools(self):
        tools = [MockTool(name="ReadFile"), MockTool(name="WriteFile")]
        prompt = build_full_system_prompt(tools=tools, use_cache=False)
        assert "ReadFile" in prompt
        assert "WriteFile" in prompt
        assert "Available Tools" in prompt

    def test_with_agents(self):
        agents = [MockAgent()]
        prompt = build_full_system_prompt(agents=agents, use_cache=False)
        assert "general-purpose" in prompt
        assert "Available Agents" in prompt

    def test_with_skills(self):
        skills = [MockSkill()]
        prompt = build_full_system_prompt(skills=skills, use_cache=False)
        assert "echo-arg" in prompt
        assert "Available Skills" in prompt

    def test_with_mcp_servers(self):
        servers = [MockMcpServer()]
        prompt = build_full_system_prompt(mcp_servers=servers, use_cache=False)
        assert "filesystem" in prompt
        assert "MCP Servers" in prompt

    def test_plan_mode(self):
        prompt = build_full_system_prompt(plan_mode=True, use_cache=False)
        assert "PLAN MODE" in prompt

    def test_no_plan_mode_by_default(self):
        prompt = build_full_system_prompt(use_cache=False)
        assert "PLAN MODE" not in prompt

    def test_non_interactive_mode(self):
        prompt = build_full_system_prompt(non_interactive=True, use_cache=False)
        assert "Non-Interactive" in prompt

    def test_tool_restrictions(self):
        prompt = build_full_system_prompt(
            tool_restrictions=["Bash", "FileWrite"],
            use_cache=False,
        )
        assert "Bash" in prompt
        assert "FileWrite" in prompt
        assert "NOT available" in prompt

    def test_output_style_concise(self):
        prompt = build_full_system_prompt(output_style="concise", use_cache=False)
        assert "concise" in prompt.lower()

    def test_output_style_default_no_section(self):
        prompt = build_full_system_prompt(output_style="default", use_cache=False)
        assert "Output Style" not in prompt

    def test_static_modules_ordered(self):
        """Static modules follow TS getSystemPrompt() order."""
        prompt = build_full_system_prompt(use_cache=False)
        intro_pos = prompt.index("interactive agent")
        system_pos = prompt.index("# System")
        tasks_pos = prompt.index("# Doing tasks")
        actions_pos = prompt.index("# Executing actions with care")
        tools_pos = prompt.index("# Using your tools")
        tone_pos = prompt.index("# Tone and style")
        efficiency_pos = prompt.index("# Communicating with the user")
        assert intro_pos < system_pos < tasks_pos < actions_pos < tools_pos < tone_pos < efficiency_pos

    def test_dynamic_sections_after_static(self):
        """Dynamic sections (agents, MCP, etc.) come after static modules."""
        prompt = build_full_system_prompt(
            agents=[MockAgent()],
            mcp_servers=[MockMcpServer()],
            use_cache=False,
        )
        efficiency_pos = prompt.index("# Communicating with the user")
        agents_pos = prompt.index("Available Agents")
        mcp_pos = prompt.index("MCP Servers")
        assert efficiency_pos < agents_pos
        assert efficiency_pos < mcp_pos

    def test_all_sections_together(self):
        prompt = build_full_system_prompt(
            cwd="/test",
            tools=[MockTool()],
            agents=[MockAgent()],
            skills=[MockSkill()],
            mcp_servers=[MockMcpServer()],
            output_style="verbose",
            plan_mode=True,
            non_interactive=True,
            tool_restrictions=["Bash"],
            append_system_prompt="Final note",
            use_cache=False,
        )
        # Static modules
        assert "interactive agent" in prompt
        assert "# System" in prompt
        assert "# Doing tasks" in prompt
        assert "# Executing actions with care" in prompt
        assert "# Using your tools" in prompt
        assert "# Tone and style" in prompt
        assert "# Communicating with the user" in prompt
        # Dynamic sections
        assert "ReadFile" in prompt  # tool docs
        assert "/test" in prompt  # environment
        assert "filesystem" in prompt  # MCP
        assert "general-purpose" in prompt  # agents
        assert "echo-arg" in prompt  # skills
        assert "PLAN MODE" in prompt
        assert "Non-Interactive" in prompt
        assert "Final note" in prompt

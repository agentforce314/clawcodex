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
    _USING_TOOLS_SECTION_TEMPLATE,
    _TONE_STYLE_SECTION,
    _OUTPUT_EFFICIENCY_SECTION,
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

    def test_task_prompt_audits_requirements_without_a_quantifier_trigger(self):
        """Keep the requirement audit; drop the quantifier-keyed clause.

        The removed sentence told the model to search for additional
        results "when the user asks for all, every, multiple…" — keying off
        bare quantifiers, which appear in ordinary task descriptions
        ("each line of the file", "in all years"). Paired with the
        exhaustive-audit nudge it drove verification rounds the latest
        Claude Code does not spend: 1 of its 89 terminal-bench 2.1 trials
        writes more than two verification files.

        A genuine "give me every match" request is an explicit requirement,
        so the surviving sentence already covers it.
        """
        prompt = build_full_system_prompt(use_cache=False)
        assert "audit the result against every explicit requirement" in prompt
        assert "plausible result as proof" in prompt
        assert "all, every, multiple, or an exhaustive set" not in prompt
        assert "stopping after the first one" not in prompt

    def test_task_prompt_prioritizes_evidence_and_cheap_discriminating_checks(self):
        prompt = build_full_system_prompt(use_cache=False)
        assert "small set of plausible hypotheses" in prompt
        assert "cheapest check that can distinguish" in prompt
        assert "Broaden the investigation only when results rule them out" in prompt
        assert "exhaustive search of the environment" in prompt

    def test_task_prompt_uses_proportionate_verification_and_stops(self):
        prompt = build_full_system_prompt(use_cache=False)
        assert "narrowest sufficient verification" in prompt
        assert "Do not create multiple temporary tests" in prompt
        assert "Stop when the explicit requirements are satisfied" in prompt
        assert "Do not re-run passing checks" in prompt
        assert "material risk" in prompt

    def test_task_prompt_groups_related_mechanical_edits(self):
        prompt = build_full_system_prompt(use_cache=False)
        assert "same well-understood mechanical change" in prompt
        assert "coherent grouped edit" in prompt
        assert "long sequence of tiny edits" in prompt
        assert "Keep unrelated or semantically distinct changes separate" in prompt

    def test_task_prompt_keeps_the_reference_file_creation_qualifiers(self):
        """The file-creation bullet must carry BOTH reference qualifiers.

        Reference (typescript/src/constants/prompts.ts, "Doing tasks"):
        "Do not create files unless they're absolutely necessary for
        achieving your goal. Generally prefer editing an existing file to
        creating a new one, as this prevents file bloat and builds on
        existing work more effectively."

        The port dropped "for achieving your goal" and the rationale,
        leaving what reads as a flat ban on new files. That cost a
        terminal-bench 2.1 task outright (torch-tensor-parallelism,
        2026-07-25): the instruction was "Create the file
        /app/parallel_linear.py", /app was empty, no interpreter existed to
        verify against, and the agent spent every step hunting for one
        instead of writing the deliverable it had been asked for.
        """
        prompt = build_full_system_prompt(use_cache=False)
        assert "absolutely necessary for achieving your goal" in prompt, (
            "dropping 'for achieving your goal' turns a bloat-avoidance rule "
            "into a prohibition on producing a requested file"
        )
        assert "prevents file bloat" in prompt

    def test_task_prompt_scopes_escalation_to_genuine_blockage(self):
        """Restored from the reference's "If an approach fails" bullet.

        The named tool is omitted on purpose: AskUserQuestion is
        unregistered on the headless surface, and a prompt that names an
        unavailable tool is its own failure mode (fix-git, 2026-07-25 —
        asked twice, then handed the task back to a user who did not exist).
        """
        prompt = build_full_system_prompt(use_cache=False)
        assert "genuinely stuck after investigation" in prompt
        assert "not as a first response to friction" in prompt

    def test_tools_prompt_pushes_parallel_tool_calls(self):
        """Bare permission to batch is not enough — the port dropped the push.

        Reference wording carries three parts: you may call multiple tools,
        MAXIMIZE parallel calls for efficiency, and do NOT parallelize
        dependent calls. clawcodex kept only the first, which reads as
        permission without direction — and the dependency carve-out is what
        makes acting on it safe.

        Measured cost: clawcodex emitted >1 tool call per assistant turn in
        5.7% of steps against the latest Claude Code's 18.1% on the same
        terminal-bench 2.1 tasks (2026-07-26). Each un-batched independent
        pair is an extra step, which is most of the two harnesses' step-count
        difference.
        """
        prompt = build_full_system_prompt(use_cache=False)
        assert "make all independent tool calls in parallel" in prompt
        assert "Maximize use of parallel tool calls" in prompt, (
            "without the imperative, batching stays theoretical"
        )
        assert "do NOT call these tools in parallel" in prompt, (
            "the dependency carve-out is what makes parallelizing safe to act on"
        )

    def test_throwaway_scripts_may_be_authored_inline(self):
        """A one-shot script is part of the command, not a deliverable.

        The Write preference must survive for deliverables — that is what
        keeps real edits reviewable as diffs — while a throwaway script the
        agent runs once may be authored inside the Bash call that runs it,
        saving a step.

        Stated as permission ("may"), not instruction, because the tradeoff
        is context-dependent: in acceptEdits mode Write is auto-accepted
        while any Bash redirect prompts (check_accept_edits_bash gates on
        _has_shell_redirection, so the target path is irrelevant), so the
        saved step costs an approval there.

        No location is specified — /tmp and in-roots paths prompt
        identically, so mandating one buys nothing.
        """
        prompt = build_full_system_prompt(use_cache=False)
        assert "To create files use Write instead of cat with heredoc" in prompt, (
            "the deliverable path must still prefer Write, for reviewable diffs"
        )
        assert "throwaway script you run once and discard" in prompt
        assert "may instead be authored inline" in prompt, (
            "permission, not instruction — the tradeoff depends on whether "
            "Bash is already unprompted in the caller's permission mode"
        )
        tools_section = prompt.split("# Using your tools")[1].split("\n- Break")[0]
        assert "/tmp" not in tools_section
        assert "exclusively" not in tools_section, (
            "an absolute 'Reserve Bash exclusively' contradicts the carve-out"
        )

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

    def test_no_plan_mode_section(self):
        # Plan mode is NOT a system-prompt section: the original injects
        # per-turn plan_mode attachments (system reminders) into the
        # conversation instead (src/context_system/plan_mode.py). The old
        # invented "# Plan Mode" section (and its plan_mode kwarg) is gone.
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
        assert "Non-Interactive" in prompt
        assert "Final note" in prompt


class TestTaskToolGating:
    """The task-tool bullet is per-surface (``_task_tool_bullet``).

    Headless keeps the damped TB2.1-tuned wording — measured over 14
    clawcodex and 21 Claude Code trials on seven shared terminal-bench 2.1
    tasks (2026-07-26): task-tool steps per trial were 0.21 vs 0.00, 27% of
    the remaining step gap spent on bookkeeping. Interactive restores the
    reference's imperative (prompts.ts getUsingYourToolsSection): the damped
    rationale — "a task list you are the only reader of" — is false there,
    because the checklist renders as the pinned HUD the user watches.
    """

    def setup_method(self):
        get_system_prompt_cache().invalidate_all()

    def teardown_method(self):
        from src.bootstrap.state import set_is_interactive

        set_is_interactive(False)
        get_system_prompt_cache().invalidate_all()

    def test_headless_keeps_the_damped_wording_and_its_own_tool_name(self):
        prompt = build_full_system_prompt(use_cache=False)
        assert "Break down and manage multi-step work" in prompt
        assert "Skip it when" in prompt, "the skip condition is the measured fix"
        assert "trivial enough" in prompt
        # Headless sessions expose TodoWrite, not TaskV2 — the bullet used to
        # name TaskCreate, a tool those sessions do not have.
        assert "TodoWrite tool" in prompt
        assert "TaskCreate" not in prompt

    def test_interactive_restores_the_reference_imperative(self):
        from src.bootstrap.state import set_is_interactive

        set_is_interactive(True)
        prompt = build_full_system_prompt(use_cache=False)
        assert "Break down and manage your work with the TaskCreate tool" in prompt
        assert "helping the user track your progress" in prompt
        assert "Mark each task as completed as soon as you are done" in prompt
        assert "Skip it when" not in prompt

    def test_env_opt_in_names_task_create_even_headless(self):
        import os
        from unittest import mock

        # CLAUDE_CODE_ENABLE_TASKS flips the exposed toolset to TaskV2
        # (task_flags.is_todo_v2_enabled) without making the session
        # interactive — wording stays damped, the name follows the tool.
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENABLE_TASKS": "1"}):
            prompt = build_full_system_prompt(use_cache=False)

        assert "Break down and manage multi-step work" in prompt
        assert "TaskCreate tool" in prompt
        assert "TodoWrite" not in prompt

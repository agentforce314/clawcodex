"""Phase-5 / WI-5.1 — forked skill execution tests.

Closes gap #8: skills with frontmatter ``context: 'fork'`` now run in a
separate context window via ``execute_forked_skill``. The previously-dead
``status == "forked"`` branch in ``_skill_map_result_to_api`` is now
reachable.

Production wiring of the forked-skill runner happens at bootstrap time
(it drives ``run_agent`` with the skill's parameters). For these tests
we inject a stub runner via ``ToolContext.forked_skill_runner`` so the
forked code path is exercised without requiring a real LLM provider.

Coverage:
  * ``context: 'fork'`` triggers the forked path; ``context: 'inline'``
    (default) does not.
  * The runner receives the rendered prompt + skill parameters
    (allowed_tools, model, effort).
  * The runner's return value lands in ``output["result"]`` and the
    output carries ``status="forked"``.
  * ``_skill_map_result_to_api`` correctly formats the forked result
    (chapter-12's "Skill X completed (forked execution).\\n\\nResult: ..."
    shape).
  * Missing runner → ``is_error=True`` ToolResult with a clear message.
  * Runner exceptions → ``is_error=True``, error preserved in output.
  * Hook registration goes through ``register_frontmatter_hooks(is_agent=True)``
    (the Stop→SubagentStop conversion path) — NOT through
    ``register_skill_hooks``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.hooks.config_manager import HookConfigManager, HookConfigSnapshot
from src.hooks.registry import AsyncHookRegistry
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks
from src.tool_system.tools.skill import SkillTool, _skill_map_result_to_api


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None
    forked_skill_runner: Any | None = None
    tool_use_id: str | None = None


def _empty_config_manager() -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks={}, timestamp=0.0, source_path=None)
    return m


def _write_skill(
    skills_dir: Path,
    *,
    name: str,
    fork: bool,
    body: str = "Forked skill body",
    extra_frontmatter: str = "",
) -> Path:
    """Write a SKILL.md file with optional ``context: 'fork'`` frontmatter."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    fm = [
        f"name: {name}",
        f"description: Phase-5 fork test skill",
    ]
    if fork:
        fm.append("context: fork")
    if extra_frontmatter:
        fm.append(extra_frontmatter)
    skill_md.write_text(
        "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n"
    )
    return skill_md


# ---------------------------------------------------------------------------
# Fork-vs-inline branching
# ---------------------------------------------------------------------------


class TestForkBranching:
    @pytest.mark.asyncio
    async def test_context_fork_triggers_forked_path(self, tmp_path):
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, name="forky", fork=True, body="Do the thing")

        captured: dict[str, Any] = {}

        async def stub_runner(*, prompt, allowed_tools, model, effort, parent_context):
            captured["prompt"] = prompt
            captured["allowed_tools"] = allowed_tools
            captured["model"] = model
            captured["effort"] = effort
            return "forked-result-text"

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "forky"}, ctx)

        # Runner was called.
        assert "prompt" in captured
        # Rendered skill body reached the runner.
        assert "Do the thing" in captured["prompt"]

        # Result shape matches the forked branch.
        assert result.is_error is False
        assert result.output["status"] == "forked"
        assert result.output["commandName"] == "forky"
        assert result.output["result"] == "forked-result-text"

    @pytest.mark.asyncio
    async def test_no_context_fork_runs_inline(self, tmp_path):
        # Default: inline execution. Runner must NOT be called.
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, name="inliny", fork=False, body="inline body")

        called = {"count": 0}

        async def stub_runner(**kwargs):
            called["count"] += 1
            return "should-not-be-called"

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "inliny"}, ctx)

        # Runner was NOT called — inline path returns rendered prompt.
        assert called["count"] == 0
        assert result.output.get("status") != "forked"
        assert "inline body" in result.output.get("prompt", "")


# ---------------------------------------------------------------------------
# Dead branch in _skill_map_result_to_api becomes reachable
# ---------------------------------------------------------------------------


class TestSkillMapResultToApiForkedBranch:
    """The pre-Phase-5 ``status == "forked"`` branch in
    ``_skill_map_result_to_api`` was dead code (no caller produced
    ``status="forked"``). Phase 5 makes the branch reachable.
    """

    def test_forked_branch_returns_completed_envelope(self):
        # Direct unit-test of the formatter — no SkillTool needed.
        output = {
            "status": "forked",
            "commandName": "my-skill",
            "result": "the sub-agent's final text",
        }
        api_result = _skill_map_result_to_api(output, tool_use_id="t1")
        assert api_result["type"] == "tool_result"
        assert api_result["tool_use_id"] == "t1"
        assert "Skill \"my-skill\" completed (forked execution)" in api_result["content"]
        assert "the sub-agent's final text" in api_result["content"]

    def test_inline_branch_returns_launching_envelope(self):
        # Counterpart: inline status renders the "Launching skill: X" form.
        output = {"commandName": "my-skill"}
        api_result = _skill_map_result_to_api(output, tool_use_id="t1")
        assert "Launching skill: my-skill" == api_result["content"]


# ---------------------------------------------------------------------------
# Runner contract
# ---------------------------------------------------------------------------


class TestRunnerContract:
    @pytest.mark.asyncio
    async def test_no_runner_returns_error_with_clear_message(self, tmp_path):
        # When forked_skill_runner is None, the fork branch returns an
        # is_error=True ToolResult with a message that says what's wrong.
        # Skill authors should not see a silent degradation-to-inline.
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, name="forky2", fork=True)

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            forked_skill_runner=None,  # explicit
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "forky2"}, ctx)

        assert result.is_error is True
        assert result.output["status"] == "forked"
        assert "forked_skill_runner" in result.output["error"]

    @pytest.mark.asyncio
    async def test_runner_exception_surfaced(self, tmp_path):
        skills_dir = tmp_path / "skills"
        _write_skill(skills_dir, name="boom", fork=True)

        async def failing_runner(**kwargs):
            raise RuntimeError("simulated forked-skill failure")

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            forked_skill_runner=failing_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "boom"}, ctx)

        assert result.is_error is True
        assert result.output["status"] == "forked"
        assert "simulated forked-skill failure" in result.output["error"]

    @pytest.mark.asyncio
    async def test_runner_receives_skill_parameters(self, tmp_path):
        # The runner is supposed to use the skill's allowed_tools / model /
        # effort to configure the sub-agent. Verify they're passed through.
        skills_dir = tmp_path / "skills"
        _write_skill(
            skills_dir, name="paramed", fork=True,
            extra_frontmatter="model: opus\nallowed-tools: [Bash, Read]\neffort: high",
        )

        captured = {}

        async def stub_runner(*, prompt, allowed_tools, model, effort, parent_context):
            captured["allowed_tools"] = allowed_tools
            captured["model"] = model
            captured["effort"] = effort
            return ""

        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            await SkillTool.call({"skill": "paramed"}, ctx)

        assert captured["model"] == "opus"
        assert captured["allowed_tools"] == ["Bash", "Read"]
        assert captured["effort"] == "high"


# ---------------------------------------------------------------------------
# Forked-skill hook registration goes through register_frontmatter_hooks
# (B1: Stop→SubagentStop conversion path)
# ---------------------------------------------------------------------------


class TestForkedSkillHookRegistration:
    @pytest.mark.asyncio
    async def test_forked_skill_with_stop_hook_converts_to_subagentstop(self, tmp_path):
        # Forked skill declaring a Stop hook → after registration, hook
        # lives under SubagentStop (NOT Stop). This is the B1-resolved
        # pathway: forked skills are sub-agents, so their Stop hooks
        # need to fire on SubagentStop.
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "fork-with-stop"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: fork-with-stop\n"
            "description: forked skill with Stop hook\n"
            "context: fork\n"
            "hooks:\n"
            "  Stop:\n"
            "    - matcher: \"\"\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: echo cleanup-on-stop\n"
            "---\n\nbody\n"
        )

        async def stub_runner(**kwargs):
            return "result"

        registry = SessionHookRegistry()
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s-fork",
            forked_skill_runner=stub_runner,
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = await SkillTool.call({"skill": "fork-with-stop"}, ctx)

        assert result.is_error is False

        # Hook landed under SubagentStop, NOT Stop. This is the
        # forked-skill / register_frontmatter_hooks(is_agent=True) path.
        sub_hooks = await get_session_hooks(
            registry=registry, session_id="s-fork", event="SubagentStop",
        )
        assert len(sub_hooks) == 1
        assert sub_hooks[0].config.command == "echo cleanup-on-stop"

        # And NOT under Stop.
        stop_hooks = await get_session_hooks(
            registry=registry, session_id="s-fork", event="Stop",
        )
        assert stop_hooks == []

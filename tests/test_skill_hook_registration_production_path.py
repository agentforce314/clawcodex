"""Phase-3 D1 cleanup — production-path test for skill hook scheduling.

Critic D1 flagged that ``test_skill_once_true_e2e`` calls
``register_skill_hooks`` directly via ``await``, bypassing the sync→async
bridge in ``_schedule_skill_hook_registration`` that the production
``_run_markdown_skill`` actually uses. This test drives the production path
end-to-end via the real ``SkillTool.call`` entry, proving:

  * The sync caller (``SkillTool.call``) returns immediately.
  * The fire-and-forget registration completes via ``loop.create_task``
    before the next ``await`` boundary.
  * The newly-registered hook is visible to ``_run_hooks_for_event`` on
    the very next executor invocation — i.e., "registration completes
    before next executor invocation" (the critical timing property D1
    asks us to pin).

This option-A test is preferred over a unit test of
``_schedule_skill_hook_registration`` itself because it covers the actual
production wiring (including the fire-and-forget timing assumption) rather
than the helper in isolation.
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
from src.hooks.hook_executor import _run_hooks_for_event
from src.hooks.registry import AsyncHookRegistry
from src.hooks.session_hooks import SessionHookRegistry, get_session_hooks
from src.tool_system.tools.skill import SkillTool


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


def _empty_config_manager() -> HookConfigManager:
    m = HookConfigManager(registry=AsyncHookRegistry(), settings_path="/dev/null")
    m._snapshot = HookConfigSnapshot(hooks={}, timestamp=0.0, source_path=None)
    return m


def _write_skill_with_hooks(skills_dir: Path, *, name: str, hook_command: str) -> Path:
    """Write a SKILL.md file with frontmatter ``hooks:`` declaring a single
    once=true PreToolUse Bash hook.

    Mirrors the chapter's worked example #2 layout. We hand-roll the
    frontmatter (rather than calling ``create_skill``) because
    ``create_skill`` doesn't accept a ``hooks:`` argument — that's chapter-
    12 territory not yet wired into the skill-creation helper.
    """
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: production-path test skill\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - matcher: Bash\n"
        "      hooks:\n"
        f"        - type: command\n"
        f"          command: {hook_command}\n"
        f"          once: true\n"
        "---\n\n"
        f"# {name}\n\nProduction-path test skill body.\n"
    )
    return skill_md


class TestSkillHookRegistrationProductionPath:
    """The bridge contract: sync ``SkillTool.call`` schedules an async
    registration that lands before the next executor invocation.
    """

    @pytest.mark.asyncio
    async def test_skilltool_call_registers_hooks_visible_to_next_executor_run(
        self, tmp_path,
    ):
        # 1. Build the skill on disk with frontmatter hooks.
        skills_dir = tmp_path / "skills"
        _write_skill_with_hooks(
            skills_dir, name="audit-skill", hook_command="echo audit",
        )

        # 2. Build the production-shaped context: a SessionHookRegistry,
        #    a session_id, an empty snapshot manager.
        registry = SessionHookRegistry()
        session_id = "session-prod-path"
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id=session_id,
        )

        # 3. Sanity: registry is empty before invocation.
        before = await get_session_hooks(
            registry=registry, session_id=session_id, event="PreToolUse",
        )
        assert before == []

        # 4. Drive the production entry point: SkillTool.call. Note this
        #    is a SYNC call returning a ToolResult immediately. The hook
        #    registration is fire-and-forget via loop.create_task.
        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = SkillTool.call({"skill": "audit-skill"}, ctx)

        # The sync return: ToolResult shape (skill rendered successfully).
        assert result.is_error is False
        assert result.output["commandName"] == "audit-skill"

        # 5. Yield once to let the loop.create_task'd registration land.
        #    This is the timing pin: ONE await boundary between SkillTool.call
        #    return and the registration being visible. If the bridge is
        #    broken (e.g., asyncio.run inside a running loop, or registration
        #    silently dropped), this test fails.
        await asyncio.sleep(0)

        # 6. Registration is now visible.
        after = await get_session_hooks(
            registry=registry, session_id=session_id, event="PreToolUse",
        )
        assert len(after) == 1, (
            f"Production-path bridge failed: registration didn't land "
            f"after one await boundary; registry={[e.config.command for e in after]!r}"
        )
        assert after[0].config.command == "echo audit"
        assert after[0].config.once is True

        # 7. Drive the executor — the hook must fire on a matching
        #    PreToolUse + Bash combination (proving the I2 collect step
        #    sees session-scoped hooks registered via the production path).
        fired_commands: list[str] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_use_id": "u1"},
            ctx,
        ):
            msg = item.get("message")
            if msg is not None:
                # Progress messages contain the command in the data dict.
                data = getattr(msg, "data", None) or {}
                if isinstance(data, dict) and data.get("command"):
                    fired_commands.append(str(data["command"]))

        assert any("echo audit" in c for c in fired_commands), (
            f"hook did not fire after production-path registration; "
            f"fired_commands={fired_commands!r}"
        )

        # 8. ``once: true`` removal landed via the executor's on_success
        #    callback → the second invocation finds the registry empty.
        for _ in range(3):
            await asyncio.sleep(0)
        final = await get_session_hooks(
            registry=registry, session_id=session_id, event="PreToolUse",
        )
        assert final == [], (
            f"once:true removal failed via production path; still: "
            f"{[e.config.command for e in final]!r}"
        )

    @pytest.mark.asyncio
    async def test_skilltool_call_with_no_hooks_does_not_schedule(
        self, tmp_path,
    ):
        # Sanity: a skill WITHOUT frontmatter ``hooks:`` does not schedule
        # any registration (no spurious async tasks, no registry entries).
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "plain-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: no hooks\n---\n\nplain body.\n"
        )

        registry = SessionHookRegistry()
        ctx = _MockContext(
            hook_config_manager=_empty_config_manager(),
            session_hook_registry=registry,
            session_id="s",
        )

        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            result = SkillTool.call({"skill": "plain-skill"}, ctx)
        assert result.is_error is False

        await asyncio.sleep(0)
        # Registry stays empty across all events.
        for ev in ("PreToolUse", "PostToolUse", "Stop"):
            entries = await get_session_hooks(
                registry=registry, session_id="s", event=ev,
            )
            assert entries == [], (
                f"plain skill spuriously registered hooks under {ev!r}: "
                f"{[e.config.command for e in entries]!r}"
            )

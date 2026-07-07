"""ch12 round-4 acceptance tests: (WI-1) skill invocation delivers the
rendered body to the model, (WI-2) hooks load from all settings scopes with
correct source tagging, (WI-3) SessionStart/SessionEnd/PreCompact fire sites.

Covers my-docs/port-improvement-round-4/ch12-extensibility-round4-plan.md.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSkillDeliversBody(unittest.TestCase):
    """WI-1 — the Skill tool result carries the rendered instructions as a
    meta user message (new_messages), not just 'Launching skill: X'."""

    def test_skill_result_has_new_messages(self):
        from src.tool_system.tools.skill import _run_markdown_skill
        from src.tool_system.context import ToolContext

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / ".clawcodex" / "skills" / "greet"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: greet\ndescription: greet the user\n---\n"
                "Say hello warmly and ask how you can help."
            )
            ctx = ToolContext(workspace_root=Path(tmp))
            result = _run_markdown_skill("greet", "", ctx)

        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.new_messages)
        self.assertEqual(len(result.new_messages), 1)
        msg = result.new_messages[0]
        self.assertTrue(getattr(msg, "isMeta", False))
        self.assertIn("hello", str(msg.content).lower())


class TestMultiScopeHookLoading(unittest.TestCase):
    """WI-2 — hooks load from user + project + local scopes, each tagged
    with its source (the trust gate keys on source.is_policy)."""

    def test_project_and_local_hooks_load_with_source(self):
        from src.hooks.config_manager import HookConfigManager
        from src.hooks.hook_types import HookSource
        from src.hooks.registry import AsyncHookRegistry

        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            claude = proj / ".clawcodex"
            claude.mkdir()
            (claude / "settings.json").write_text(json.dumps({
                "hooks": {"PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "echo project"}]}
                ]}
            }))
            (claude / "settings.local.json").write_text(json.dumps({
                "hooks": {"PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "echo local"}]}
                ]}
            }))
            # Point the USER scope at an empty temp file.
            user = proj / "user_settings.json"
            user.write_text("{}")

            reg = AsyncHookRegistry()
            mgr = HookConfigManager(reg, cwd=str(proj))
            with patch("src.permissions.settings_paths.user_settings_path",
                       return_value=str(user)):
                snap = asyncio.run(mgr.load())

        commands = {
            c.command: c.source
            for c in snap.hooks.get("PreToolUse", [])
        }
        self.assertIn("echo project", commands)
        self.assertIn("echo local", commands)
        self.assertEqual(commands["echo project"], HookSource.PROJECT_SETTINGS)
        self.assertEqual(commands["echo local"], HookSource.LOCAL_SETTINGS)

    def test_project_hook_is_policy_false_so_dropped_under_distrust(self):
        # SECURITY (critic b): a malicious repo's .clawcodex/settings.json
        # PreToolUse hook (source=PROJECT_SETTINGS) is NOT policy, so the
        # trust gate drops it in an UNtrusted workspace — it only fires once
        # the user trusts the workspace. Loading project hooks is therefore
        # safe (trust-then-run).
        from src.hooks.hook_types import HookSource

        self.assertFalse(HookSource.PROJECT_SETTINGS.is_policy)
        self.assertFalse(HookSource.LOCAL_SETTINGS.is_policy)
        self.assertFalse(HookSource.USER_SETTINGS.is_policy)
        self.assertTrue(HookSource.POLICY_SETTINGS.is_policy)

    def test_single_scope_mode_unchanged(self):
        # An explicit settings_path (tests/SDK) → single user scope.
        from src.hooks.config_manager import HookConfigManager
        from src.hooks.hook_types import HookSource
        from src.hooks.registry import AsyncHookRegistry

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "settings.json"
            f.write_text(json.dumps({
                "hooks": {"PreToolUse": [
                    {"type": "command", "command": "echo user"}]}
            }))
            reg = AsyncHookRegistry()
            mgr = HookConfigManager(reg, settings_path=str(f))
            snap = asyncio.run(mgr.load())
        cfgs = snap.hooks.get("PreToolUse", [])
        self.assertEqual(len(cfgs), 1)
        self.assertEqual(cfgs[0].source, HookSource.USER_SETTINGS)


class TestSessionHookTrustGate(unittest.TestCase):
    """critic B1 (BLOCKING security) — the session-lifecycle routers must
    NOT run a repo-injected (project) command hook in an UNtrusted
    workspace; a policy hook MUST still run."""

    def _ctx_with_snapshot(self, *, trusted, source):
        from src.hooks.config_manager import HookConfigSnapshot
        from src.hooks.hook_types import HookConfig
        from src.tool_system.context import ToolContext

        ctx = ToolContext(workspace_root=Path("/tmp"))
        ctx.workspace_trusted = trusted

        mgr = MagicMock()
        mgr.snapshot = HookConfigSnapshot(hooks={
            "SessionStart": [HookConfig(
                type="command", command="echo pwned", source=source,
            )]
        })
        ctx.hook_config_manager = mgr
        return ctx

    def _run(self, ctx):
        from src.hooks.session_hooks import run_session_start_hooks

        ran = []

        async def _fake_cmd(config, stdin):
            ran.append(config.command)
            return {"exit_code": 0}

        with patch("src.hooks.hook_executor._execute_command_hook", _fake_cmd):
            asyncio.run(run_session_start_hooks(tool_use_context=ctx))
        return ran

    def test_untrusted_project_hook_does_not_run(self):
        from src.hooks.hook_types import HookSource

        ctx = self._ctx_with_snapshot(
            trusted=False, source=HookSource.PROJECT_SETTINGS,
        )
        self.assertEqual(self._run(ctx), [])  # dropped by the trust gate

    def test_untrusted_policy_hook_runs(self):
        from src.hooks.hook_types import HookSource

        ctx = self._ctx_with_snapshot(
            trusted=False, source=HookSource.POLICY_SETTINGS,
        )
        self.assertEqual(self._run(ctx), ["echo pwned"])  # policy survives

    def test_trusted_project_hook_runs(self):
        from src.hooks.hook_types import HookSource

        ctx = self._ctx_with_snapshot(
            trusted=True, source=HookSource.PROJECT_SETTINGS,
        )
        self.assertEqual(self._run(ctx), ["echo pwned"])  # trusted → runs


class TestSessionLifecycleFireSites(unittest.TestCase):
    """WI-3 — SessionStart/SessionEnd/PreCompact fire at the right sites."""

    def _session(self):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        return _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )

    def test_session_start_fires_once(self):
        sess = self._session()
        calls = []

        async def _spy(**kw):
            calls.append(kw)
            return []

        with patch("src.hooks.session_hooks.run_session_start_hooks", _spy):
            sess._fire_session_start_once()
            sess._fire_session_start_once()  # second call is a no-op
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("session_id"), "s1")

    def test_session_end_fires_on_shutdown(self):
        sess = self._session()
        calls = []

        async def _spy(**kw):
            calls.append(kw)
            return []

        # shutdown touches other subsystems; patch them to no-ops.
        with patch("src.hooks.session_hooks.run_session_end_hooks", _spy), \
             patch("src.tasks.eviction.stop_eviction_sweeper"):
            sess._worker = None
            sess._current_abort = None
            asyncio.run(sess.shutdown())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

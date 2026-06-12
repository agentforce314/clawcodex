"""Phase-1 / WI-1.5 — env-var injection at hook fire time.

Three new env vars on top of inherited ``os.environ``:
  * ``CLAUDE_PROJECT_DIR`` — workspace root from active context.
  * ``CLAUDE_PLUGIN_ROOT`` — set from ``hook.skill_root`` (skill-declared
    hooks only).
  * ``CLAUDE_ENV_FILE`` — per-fire ephemeral path. Set ONLY for
    ``SessionStart``, ``Setup``, ``CwdChanged``. Per N4: this WI sets the
    path; sourcing-and-applying loop is a separate follow-up ticket.

These tests cover ``_build_hook_env`` directly (unit-level) plus a
subprocess round-trip that verifies the env var is actually visible to the
hook command.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from src.hooks.hook_executor import _build_hook_env, _execute_command_hook
from src.hooks.hook_types import HookConfig


@dataclass
class _MockCtx:
    workspace_root: str = "/some/workspace"


class TestBuildHookEnv:
    def test_claude_project_dir_set_from_workspace_root(self):
        ctx = _MockCtx(workspace_root="/work/dir")
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PROJECT_DIR"] == "/work/dir"

    def test_claude_project_dir_empty_when_no_context(self):
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, None)
        assert env["CLAUDE_PROJECT_DIR"] == ""

    def test_claude_plugin_root_from_skill_root(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x", skill_root="/skills/my-skill")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PLUGIN_ROOT"] == "/skills/my-skill"

    def test_claude_plugin_root_empty_for_non_skill_hooks(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")  # skill_root=None
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PLUGIN_ROOT"] == ""

    def test_claude_env_file_set_for_session_start(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "SessionStart"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""
        # Path is under ~/.clawcodex/hook-env/ — fail-loud if the layout
        # changes silently.
        assert "hook-env" in env["CLAUDE_ENV_FILE"]
        assert "SessionStart" in env["CLAUDE_ENV_FILE"]

    def test_claude_env_file_set_for_setup(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "Setup"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""

    def test_claude_env_file_set_for_cwd_changed(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "CwdChanged"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""

    def test_claude_env_file_empty_for_pre_tool_use(self):
        # PreToolUse is a tool-lifecycle event, not a lifecycle env-propagation
        # event.
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_ENV_FILE"] == ""

    def test_claude_hook_event_preserved(self):
        # Pre-existing var stays.
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PostToolUse"}, ctx)
        assert env["CLAUDE_HOOK_EVENT"] == "PostToolUse"

    def test_inherited_environment_preserved(self):
        # The new vars don't clobber inherited environment.
        os.environ["CLAW_TEST_PRESERVED"] = "yes"
        try:
            ctx = _MockCtx()
            hook = HookConfig(type="command", command="x")
            env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
            assert env.get("CLAW_TEST_PRESERVED") == "yes"
        finally:
            del os.environ["CLAW_TEST_PRESERVED"]


class TestEnvVisibleToSubprocess:
    @pytest.mark.asyncio
    async def test_command_sees_claude_project_dir(self):
        ctx = _MockCtx(workspace_root="/expected/path")
        hook = HookConfig(
            type="command",
            command='printf "DIR=%s" "$CLAUDE_PROJECT_DIR"',
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "DIR=/expected/path" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_command_sees_claude_plugin_root(self):
        ctx = _MockCtx()
        hook = HookConfig(
            type="command",
            command='printf "ROOT=%s" "$CLAUDE_PLUGIN_ROOT"',
            skill_root="/path/to/skill",
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "ROOT=/path/to/skill" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_command_sees_claude_env_file_for_session_start(self):
        ctx = _MockCtx()
        hook = HookConfig(
            type="command",
            command='printf "FILE=%s" "$CLAUDE_ENV_FILE"',
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "SessionStart"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "hook-env" in (result.stdout or "")
        assert "SessionStart" in (result.stdout or "")


# ---------------------------------------------------------------------------
# #281 — the source-and-apply cycle (shell-evaluated, Bash-tool-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_session_env(tmp_path, monkeypatch):
    """Per-test hygiene for the #281 plumbing: empty session buckets and a
    tmp HOME so the env-file mkdir never touches the real ~/.clawcodex."""
    from src.hooks.session_env import reset_session_hook_env_for_testing

    monkeypatch.setenv("HOME", str(tmp_path))
    reset_session_hook_env_for_testing()
    yield
    reset_session_hook_env_for_testing()


class TestShellEvalEnvExports:
    def _eval(self, tmp_path, content: str):
        from src.hooks.hook_executor import _shell_eval_env_exports

        f = tmp_path / "envfile"
        f.write_text(content)
        return _shell_eval_env_exports(str(f))

    def test_plain_and_export_assignments(self, tmp_path):
        out = self._eval(tmp_path, "FOO=bar\nexport BAZ=qux\n")
        assert out == {"FOO": "bar", "BAZ": "qux"}

    def test_dollar_expansion(self, tmp_path):
        # The canonical venv/conda idiom: $VAR references MUST expand —
        # a literal parse would corrupt PATH for every later spawn.
        out = self._eval(tmp_path, 'export E281_PATH="$HOME/bin:$PATH"\n')
        assert out["E281_PATH"] == f"{os.environ['HOME']}/bin:{os.environ['PATH']}"

    def test_quoting(self, tmp_path):
        out = self._eval(tmp_path, "A=\"hello world\"\nB='single quoted'\n")
        assert out == {"A": "hello world", "B": "single quoted"}

    def test_failed_source_returns_nothing(self, tmp_path):
        out = self._eval(tmp_path, "if [ ; then\n")  # syntax error
        assert out == {}

    def test_shell_noise_keys_excluded(self, tmp_path):
        out = self._eval(tmp_path, "X281=1\n")
        assert set(out) == {"X281"}  # no PWD/SHLVL/_/OLDPWD

    def test_evaluation_sees_prior_session_exports(self, tmp_path):
        # A Setup hook prepending to a PATH a SessionStart hook already
        # extended must see the extended value, not the raw os.environ one.
        from src.hooks.session_env import merge_into_bucket

        merge_into_bucket("SessionStart", {"E281_CHAIN": "first"})
        out = self._eval(tmp_path, 'E281_CHAIN="$E281_CHAIN,second"\n')
        assert out["E281_CHAIN"] == "first,second"


class TestApplyEnvFile:
    def test_applies_into_session_bucket_and_removes_file(self, tmp_path):
        from src.hooks.hook_executor import _apply_env_file
        from src.hooks.session_env import get_session_hook_env

        f = tmp_path / "envfile"
        f.write_text('export HOOK_VAR_281="from hook"\n')
        _apply_env_file(str(f), "SessionStart")
        assert get_session_hook_env().get("HOOK_VAR_281") == "from hook"
        assert "HOOK_VAR_281" not in os.environ  # host env untouched
        assert not f.exists()

    def test_missing_file_is_noop(self, tmp_path):
        from src.hooks.hook_executor import _apply_env_file

        _apply_env_file(str(tmp_path / "nope"), "SessionStart")  # no raise

    def test_oversized_file_is_ignored_but_removed(self, tmp_path):
        from src.hooks.hook_executor import (
            _MAX_ENV_FILE_BYTES,
            _apply_env_file,
        )
        from src.hooks.session_env import get_session_hook_env

        f = tmp_path / "big"
        f.write_text("BIG281=x\n" + "#" * (_MAX_ENV_FILE_BYTES + 1))
        _apply_env_file(str(f), "SessionStart")
        assert "BIG281" not in get_session_hook_env()
        assert not f.exists()


class TestSessionEnvBuckets:
    def test_cwd_changed_refire_replaces_previous_exports(self):
        from src.hooks.session_env import (
            clear_event_bucket,
            get_session_hook_env,
            merge_into_bucket,
        )

        merge_into_bucket("CwdChanged", {"PROJ_VAR": "project-a"})
        # Next cwd change: the executor clears the bucket before the
        # event's hooks (if any) run.
        clear_event_bucket("CwdChanged")
        merge_into_bucket("CwdChanged", {"OTHER": "project-b"})
        env = get_session_hook_env()
        assert "PROJ_VAR" not in env
        assert env["OTHER"] == "project-b"

    def test_later_lifecycle_events_win(self):
        from src.hooks.session_env import get_session_hook_env, merge_into_bucket

        merge_into_bucket("SessionStart", {"K": "start"})
        merge_into_bucket("CwdChanged", {"K": "cwd"})
        assert get_session_hook_env()["K"] == "cwd"


class TestPowershellParity:
    def test_powershell_hooks_get_no_env_file(self):
        hook = HookConfig(type="command", command="x", shell="powershell")
        env = _build_hook_env(hook, {"hook_event": "SessionStart"}, None)
        assert env["CLAUDE_ENV_FILE"] == ""


class TestEndToEndEnvPropagation:
    @pytest.mark.asyncio
    async def test_session_start_hook_export_reaches_next_bash_spawn(self):
        """The documented contract (#281): a hook writes ``export`` lines
        to "$CLAUDE_ENV_FILE" and subsequent Bash tool commands see the
        variable — via the session env merged at spawn, NOT the host env."""
        from src.hooks.session_env import get_session_hook_env

        hook = HookConfig(
            type="command",
            command='echo "export HOOK_E2E_281=propagated" > "$CLAUDE_ENV_FILE"',
        )
        result = await _execute_command_hook(hook, {"hook_event": "SessionStart"})
        assert result.exit_code == 0
        assert get_session_hook_env().get("HOOK_E2E_281") == "propagated"
        assert "HOOK_E2E_281" not in os.environ

        # Spawn the way the Bash tool does: session env merged over
        # os.environ.
        import subprocess

        probe = subprocess.run(
            ["/bin/sh", "-c", 'printf "%s" "$HOOK_E2E_281"'],
            capture_output=True,
            text=True,
            env={**os.environ, **get_session_hook_env()},
        )
        assert probe.stdout == "propagated"

    @pytest.mark.asyncio
    async def test_bash_tool_foreground_sees_hook_export(self, tmp_path):
        """Full integration: hook export → real Bash tool dispatch."""
        from src.hooks.session_env import merge_into_bucket
        from src.permissions.types import ToolPermissionContext
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry
        from src.tool_system.protocol import ToolCall

        merge_into_bucket("SessionStart", {"HOOK_BASH_281": "visible"})
        registry = build_default_registry(include_user_tools=False)
        ctx = ToolContext(
            workspace_root=tmp_path,
            permission_context=ToolPermissionContext(mode="bypassPermissions"),
        )
        result = registry.dispatch(
            ToolCall(
                name="Bash",
                input={"command": 'printf "%s" "$HOOK_BASH_281"'},
            ),
            ctx,
        )
        assert result.is_error is False
        assert "visible" in str(result.output)

    @pytest.mark.asyncio
    async def test_path_prepend_does_not_brick_bash_spawn(self, tmp_path):
        """The canonical venv idiom: export PATH="$HOME/bin:$PATH" must
        expand (a literal parse bricked every later spawn)."""
        from src.hooks.session_env import get_session_hook_env

        hook = HookConfig(
            type="command",
            command='echo \'export PATH="$HOME/bin:$PATH"\' > "$CLAUDE_ENV_FILE"',
        )
        result = await _execute_command_hook(hook, {"hook_event": "SessionStart"})
        assert result.exit_code == 0
        new_path = get_session_hook_env()["PATH"]
        assert "$" not in new_path  # fully expanded
        assert new_path.startswith(f"{os.environ['HOME']}/bin:")
        # bash must still be resolvable through the new PATH
        import subprocess

        probe = subprocess.run(
            ["bash", "-lc", "echo ok"],
            capture_output=True,
            text=True,
            env={**os.environ, **get_session_hook_env()},
        )
        assert probe.stdout.strip() == "ok"

    @pytest.mark.asyncio
    async def test_failed_hook_exports_are_discarded(self):
        from src.hooks.session_env import get_session_hook_env

        hook = HookConfig(
            type="command",
            command=(
                'echo "HOOK_FAIL_281=should-not-apply" > "$CLAUDE_ENV_FILE"; '
                "exit 1"
            ),
        )
        result = await _execute_command_hook(hook, {"hook_event": "SessionStart"})
        assert result.exit_code == 1
        assert "HOOK_FAIL_281" not in get_session_hook_env()

    @pytest.mark.asyncio
    async def test_non_lifecycle_event_does_not_propagate(self):
        from src.hooks.session_env import get_session_hook_env

        hook = HookConfig(
            type="command",
            command=(
                'if [ -n "$CLAUDE_ENV_FILE" ]; then '
                'echo "HOOK_PRE_281=nope" > "$CLAUDE_ENV_FILE"; fi'
            ),
        )
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 0
        assert "HOOK_PRE_281" not in get_session_hook_env()

    def test_session_start_overrides_setup(self):
        # TS HOOK_ENV_PRIORITY: setup < sessionstart < cwdchanged.
        from src.hooks.session_env import get_session_hook_env, merge_into_bucket

        merge_into_bucket("Setup", {"P": "setup"})
        merge_into_bucket("SessionStart", {"P": "session-start"})
        assert get_session_hook_env()["P"] == "session-start"

    @pytest.mark.asyncio
    async def test_session_start_refire_replaces_via_session_hooks_path(self):
        """The clear-on-fire invariant must hold on BOTH dispatch paths —
        run_session_start_hooks (the lifecycle router) included."""
        from src.hooks.registry import AsyncHookRegistry
        from src.hooks.session_env import get_session_hook_env, merge_into_bucket
        from src.hooks.session_hooks import run_session_start_hooks

        merge_into_bucket("SessionStart", {"STALE_281": "old"})
        await run_session_start_hooks(AsyncHookRegistry())  # no hooks defined
        assert "STALE_281" not in get_session_hook_env()

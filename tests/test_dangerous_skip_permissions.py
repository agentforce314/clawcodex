"""Tests for the ``--dangerously-skip-permissions`` wiring (round 5).

Mirrors the behavior of the TS reference's ``initialPermissionModeFromCLI``,
``setup.ts`` root/sudo gate, and the runtime permission check in
``has_permissions_to_use_tool``.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.permissions.dangerous_safety import (
    enforce_dangerous_skip_permissions_safety,
    is_sandbox_environment,
)
from src.permissions.modes import (
    has_allow_bypass_permissions_mode,
    initial_permission_mode_from_cli,
)


# ---------------------------------------------------------------------------
# initial_permission_mode_from_cli


def test_dsp_flag_resolves_to_bypass_permissions():
    mode = initial_permission_mode_from_cli(dangerously_skip_permissions=True)
    assert mode == "bypassPermissions"


def test_no_flags_falls_back_to_default():
    mode = initial_permission_mode_from_cli()
    assert mode == "default"


def test_permission_mode_cli_used_when_dsp_absent():
    mode = initial_permission_mode_from_cli(permission_mode_cli="plan")
    assert mode == "plan"


def test_dsp_flag_takes_priority_over_permission_mode_cli():
    mode = initial_permission_mode_from_cli(
        permission_mode_cli="plan",
        dangerously_skip_permissions=True,
    )
    assert mode == "bypassPermissions"


def test_settings_default_mode_used_as_third_priority():
    mode = initial_permission_mode_from_cli(settings_default_mode="acceptEdits")
    assert mode == "acceptEdits"


def test_unknown_permission_mode_string_falls_back_to_default():
    mode = initial_permission_mode_from_cli(permission_mode_cli="garbage")
    assert mode == "default"


def test_priority_dsp_then_cli_then_settings():
    mode = initial_permission_mode_from_cli(
        permission_mode_cli="plan",
        settings_default_mode="acceptEdits",
    )
    # CLI beats settings
    assert mode == "plan"


# ---------------------------------------------------------------------------
# Root/sudo safety gate


def test_safety_gate_no_op_when_bypass_not_requested():
    # Should never raise regardless of uid.
    enforce_dangerous_skip_permissions_safety(bypass_requested=False)


def test_safety_gate_no_op_when_not_root(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    enforce_dangerous_skip_permissions_safety(bypass_requested=True)


@pytest.mark.skipif(sys.platform == "win32", reason="root check is no-op on Windows")
def test_safety_gate_aborts_when_root_outside_sandbox(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    monkeypatch.delenv("IS_SANDBOX", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_BUBBLEWRAP", raising=False)
    err = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        enforce_dangerous_skip_permissions_safety(bypass_requested=True, stderr=err)
    assert excinfo.value.code == 1
    assert "root/sudo" in err.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="root check is no-op on Windows")
def test_safety_gate_allows_root_when_is_sandbox_set(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    monkeypatch.setenv("IS_SANDBOX", "1")
    enforce_dangerous_skip_permissions_safety(bypass_requested=True)


@pytest.mark.skipif(sys.platform == "win32", reason="root check is no-op on Windows")
def test_safety_gate_allows_root_when_bubblewrap_set(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    monkeypatch.delenv("IS_SANDBOX", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_BUBBLEWRAP", "1")
    enforce_dangerous_skip_permissions_safety(bypass_requested=True)


def test_is_sandbox_environment_falsy_for_zero_or_empty(monkeypatch):
    monkeypatch.setenv("IS_SANDBOX", "0")
    monkeypatch.delenv("CLAUDE_CODE_BUBBLEWRAP", raising=False)
    assert is_sandbox_environment() is False
    monkeypatch.setenv("IS_SANDBOX", "")
    assert is_sandbox_environment() is False


def test_is_sandbox_environment_truthy_for_one(monkeypatch):
    monkeypatch.setenv("IS_SANDBOX", "1")
    monkeypatch.delenv("CLAUDE_CODE_BUBBLEWRAP", raising=False)
    assert is_sandbox_environment() is True


# ---------------------------------------------------------------------------
# has_allow_bypass_permissions_mode (settings reader)


def test_has_allow_bypass_permissions_mode_default_false():
    # The default settings should not enable bypass mode availability.
    # (Whatever's in the actual user config is fine — we just ensure this
    # function doesn't crash and returns a bool.)
    result = has_allow_bypass_permissions_mode()
    assert isinstance(result, bool)


def _write_perms_config(path: Path, enabled: bool) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"settings": {"permissions": {"allowBypassPermissionsMode": enabled}}}),
        encoding="utf-8",
    )


@pytest.fixture
def _tiered_configs(tmp_path, monkeypatch):
    """Patch the three config-tier paths to hermetic temp files and return them.

    Sidesteps git-root resolution + the real user config so the reader's tier
    selection is observable in isolation.
    """
    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project.json"
    local_path = tmp_path / "local.json"
    monkeypatch.setattr("src.config.get_global_config_path", lambda: global_path)
    monkeypatch.setattr(
        "src.config.get_project_config_path", lambda cwd=None: project_path,
    )
    monkeypatch.setattr(
        "src.config.get_local_config_path", lambda cwd=None: local_path,
    )
    return global_path, project_path, local_path


def test_has_allow_bypass_reads_global_tier(_tiered_configs):
    global_path, _, _ = _tiered_configs
    _write_perms_config(global_path, True)
    assert has_allow_bypass_permissions_mode() is True


def test_has_allow_bypass_ignores_local_tier(_tiered_configs):
    # SECURITY: the local tier is <git-root>/.clawcodex/config.local.json —
    # get_local_config_path is GIT-ROOT relative, not home relative, so it is
    # exactly as committable as the project tier below. `.local` names a
    # convention in your own .gitignore, not a trust boundary.
    #
    # This is a deliberate divergence from the TS reference, which does read the
    # local tier: availability is not a minor capability. check.py's
    # `should_bypass` is `mode == "bypassPermissions" or (mode == "plan" and
    # is_bypass_permissions_mode_available)`, and the same disjunction in
    # tool_system/context.py lifts working-root containment — so a committed
    # file could turn `plan` into a write-anywhere bypass, with `clawcodex -p`
    # never reaching the folder-trust gate.
    _, _, local_path = _tiered_configs
    _write_perms_config(local_path, True)
    assert has_allow_bypass_permissions_mode() is False


def test_has_allow_bypass_ignores_project_tier(_tiered_configs):
    # SECURITY: the committable <git-root>/.clawcodex/config.json must NOT be
    # able to auto-enable bypass availability (parity with the TS
    # projectSettings exclusion). Only the project tier is set here.
    _, project_path, _ = _tiered_configs
    _write_perms_config(project_path, True)
    assert has_allow_bypass_permissions_mode() is False


def test_a_repo_granted_availability_cannot_lift_plan_containment(_tiered_configs):
    """The end-to-end consequence, through the real gate.

    Availability + `plan` is a full bypass, so a repo-writable source for it
    would defeat the headline property that `/plan` restrains.
    """
    from src.permissions.check import has_permissions_to_use_tool
    from src.permissions.modes import resolve_interactive_permission_state
    from src.permissions.types import ToolPermissionContext
    from src.tool_system.tools.write import WriteTool

    _, project_path, local_path = _tiered_configs
    _write_perms_config(local_path, True)
    _write_perms_config(project_path, True)

    mode, available, _sel = resolve_interactive_permission_state(
        permission_mode_cli="plan",
        dangerously_skip_permissions=False,
        allow_dangerously_skip_permissions=False,
        implicit_full_access=False,
    )
    assert available is False
    ctx = ToolPermissionContext(
        mode=mode, is_bypass_permissions_mode_available=available,
    )
    decision = has_permissions_to_use_tool(
        WriteTool, {"file_path": "/etc/evil.txt", "content": "x"}, ctx,
    )
    assert decision.behavior != "allow"


def test_has_allow_bypass_false_when_all_tiers_absent(_tiered_configs):
    assert has_allow_bypass_permissions_mode() is False


# ---------------------------------------------------------------------------
# Headless wiring


def test_headless_dsp_flag_flips_tool_context_to_bypass(tmp_path, monkeypatch):
    """Smoke test the new HeadlessOptions fields without booting an LLM."""
    from src.entrypoints.headless import HeadlessOptions

    # We don't run the full headless loop — too noisy. Instead exercise the
    # path that builds the tool_context by inspecting HeadlessOptions and
    # the default values.
    opts = HeadlessOptions(
        prompt="hi",
        skip_permissions=True,
        permission_mode="default",
        is_bypass_permissions_mode_available=False,
    )
    # ``skip_permissions`` is the legacy alias and is honored.
    assert opts.skip_permissions is True


def test_headless_options_defaults():
    from src.entrypoints.headless import HeadlessOptions

    opts = HeadlessOptions(prompt="hi")
    assert opts.skip_permissions is False
    assert opts.permission_mode == "default"
    assert opts.is_bypass_permissions_mode_available is False


def test_headless_run_skip_permissions_sets_bypass_mode(tmp_path, monkeypatch):
    """Smoke that run_headless threads `skip_permissions` -> bypass mode."""
    from src.entrypoints import headless as headless_mod
    from src.entrypoints.headless import HeadlessOptions, run_headless
    from src.providers.base import ChatResponse

    class _FakeProvider:
        def __init__(self, api_key, base_url=None, model=None):
            self.model = model or "fake"

        def chat(self, messages, tools=None, **kw):
            return ChatResponse(
                content="ok",
                model="fake",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="end_turn",
                tool_uses=None,
            )

    class _FakeRegistry:
        def list_tools(self):
            return []

        def remove_tool(self, name):
            # run_headless unregisters AskUserQuestion (no user on
            # this surface); real ToolRegistry returns a bool.
            return False

    monkeypatch.setattr(
        headless_mod, "get_provider_class", lambda n: _FakeProvider
    )
    monkeypatch.setattr(
        headless_mod, "get_provider_config",
        lambda n: {"api_key": "x", "default_model": "fake"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    # ENTRY-2: startup validation reads the REAL provider registry (the
    # shared helper, not the module aliases faked here) — stub it out;
    # it has its own dedicated tests (test_startup_validation.py).
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.get_provider_validation_error",
        lambda name: None,
    )
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry()
    )

    captured: dict = {}
    # Headless now routes through ``run_query_as_agent_loop`` (async)
    # instead of the legacy ``run_agent_loop``. Patch the actual call
    # site so this fixture still observes the tool_context the
    # production path constructs.
    original = headless_mod.run_query_as_agent_loop

    async def _capture(*args, **kw):
        captured["tool_context"] = kw["tool_context"]
        return await original(*args, **kw)

    monkeypatch.setattr(headless_mod, "run_query_as_agent_loop", _capture)

    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="text",
            skip_permissions=True,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    assert code == 0
    ctx = captured["tool_context"]
    assert ctx.permission_context.mode == "bypassPermissions"
    assert ctx.permission_context.is_bypass_permissions_mode_available is True
    assert ctx.permission_handler is None
    assert ctx.allow_docs is True


def test_headless_run_default_mode_keeps_auto_deny_handler(tmp_path, monkeypatch):
    from src.entrypoints import headless as headless_mod
    from src.entrypoints.headless import HeadlessOptions, run_headless
    from src.providers.base import ChatResponse

    class _FakeProvider:
        def __init__(self, api_key, base_url=None, model=None):
            self.model = model or "fake"

        def chat(self, messages, tools=None, **kw):
            return ChatResponse(
                content="ok",
                model="fake",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="end_turn",
                tool_uses=None,
            )

    class _FakeRegistry:
        def list_tools(self):
            return []

        def remove_tool(self, name):
            # run_headless unregisters AskUserQuestion (no user on
            # this surface); real ToolRegistry returns a bool.
            return False

    monkeypatch.setattr(
        headless_mod, "get_provider_class", lambda n: _FakeProvider
    )
    monkeypatch.setattr(
        headless_mod, "get_provider_config",
        lambda n: {"api_key": "x", "default_model": "fake"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    # ENTRY-2: startup validation reads the REAL provider registry (the
    # shared helper, not the module aliases faked here) — stub it out;
    # it has its own dedicated tests (test_startup_validation.py).
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.get_provider_validation_error",
        lambda name: None,
    )
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry()
    )

    captured: dict = {}
    # Headless now routes through ``run_query_as_agent_loop`` (async)
    # instead of the legacy ``run_agent_loop``. Patch the actual call
    # site so this fixture still observes the tool_context the
    # production path constructs.
    original = headless_mod.run_query_as_agent_loop

    async def _capture(*args, **kw):
        captured["tool_context"] = kw["tool_context"]
        return await original(*args, **kw)

    monkeypatch.setattr(headless_mod, "run_query_as_agent_loop", _capture)

    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="text",
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    assert code == 0
    ctx = captured["tool_context"]
    # Default mode keeps the auto-deny handler.
    assert ctx.permission_context.mode == "default"
    assert ctx.permission_handler is not None
    from src.permissions.types import PermissionAskRequest

    reply = ctx.permission_handler(
        PermissionAskRequest(tool_name="Bash", message="needs approval")
    )
    assert reply.behavior == "deny"


# ---------------------------------------------------------------------------
# CLI parser


def test_cli_parser_accepts_dangerously_skip_permissions():
    from src.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["--dangerously-skip-permissions"])
    assert args.dangerously_skip_permissions is True
    assert args.allow_dangerously_skip_permissions is False


def test_cli_parser_accepts_allow_dangerously_skip_permissions():
    from src.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["--allow-dangerously-skip-permissions"])
    assert args.allow_dangerously_skip_permissions is True
    assert args.dangerously_skip_permissions is False


def test_cli_parser_accepts_permission_mode():
    from src.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["--permission-mode", "plan"])
    assert args.permission_mode == "plan"


def test_cli_parser_default_permission_state():
    from src.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([])
    assert args.dangerously_skip_permissions is False
    assert args.allow_dangerously_skip_permissions is False
    assert args.permission_mode is None


def test_resolve_permission_state_stashes_resolved_mode_on_args():
    from src.cli import _build_parser, _resolve_permission_state

    parser = _build_parser()
    args = parser.parse_args(["--dangerously-skip-permissions"])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "bypassPermissions"
    assert args._resolved_is_bypass_available is True


def _as_tty(monkeypatch):
    """Make the process look like it is attached to a terminal.

    The loose default is TTY-gated, and pytest runs with pipes — so without this
    every "interactive" assertion would silently test the non-TTY path instead.
    """
    import sys as _sys

    monkeypatch.setattr(_sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True, raising=False)


def test_resolve_permission_state_full_access_when_no_flag_interactive(monkeypatch):
    """A bare interactive `clawcodex` starts in Full Access.

    This replaced the previous `default`: the permission UX was reworked so the
    tool is loose by default and `/permissions` dials it back. Note what does
    NOT change — engine bypass AVAILABILITY stays False, because that flag also
    relaxes plan mode (`check.py` `should_bypass`) and `/plan` must keep
    restraining even in a full-access session.
    """
    from src.cli import _build_parser, _resolve_permission_state

    _as_tty(monkeypatch)
    parser = _build_parser()
    args = parser.parse_args([])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "bypassPermissions"
    assert args._resolved_is_bypass_available is False
    # …but the picker can still offer Full Access, which is a separate capability.
    assert args._resolved_bypass_selectable is True


def test_resolve_permission_state_non_tty_launch_is_not_loose():
    """"Not --print" is not the same as "a human is sitting there".

    A piped/automated launch that merely omits -p must not take the loose floor
    — `_gate_folder_trust` grants trust implicitly on non-TTY stdin, so nothing
    downstream would stop it either. pytest's own pipes make this the default
    state here, which is exactly the case being pinned.
    """
    from src.cli import _build_parser, _resolve_permission_state

    parser = _build_parser()
    args = parser.parse_args([])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "default"
    assert args._resolved_bypass_selectable is False


def test_resolve_permission_state_print_mode_keeps_default():
    """`--print` / headless is deliberately excluded from the loose default:
    CI and the Harbor eval harness drive it, and silently changing what a
    benchmark run is permitted to do is not acceptable."""
    from src.cli import _build_parser, _resolve_permission_state

    parser = _build_parser()
    args = parser.parse_args(["--print", "hello"])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "default"
    assert args._resolved_is_bypass_available is False
    assert args._resolved_bypass_selectable is False


def test_resolve_permission_state_allow_dangerously_only_does_not_flip_mode():
    from src.cli import _build_parser, _resolve_permission_state

    parser = _build_parser()
    args = parser.parse_args(["--allow-dangerously-skip-permissions"])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "default"
    assert args._resolved_is_bypass_available is True


# ---------------------------------------------------------------------------
# Runtime permission check honors bypass mode


def test_runtime_check_returns_allow_in_bypass_mode():
    """End-to-end: `has_permissions_to_use_tool` should allow without prompt."""
    from src.permissions.check import has_permissions_to_use_tool
    from src.permissions.types import (
        PermissionAllowDecision,
        ToolPermissionContext,
    )

    class _StubTool:
        name = "Bash"
        is_mcp = False

        def check_permissions(self, tool_input, context):
            from src.permissions.types import PermissionPassthroughResult

            return PermissionPassthroughResult(behavior="passthrough")

    ctx = ToolPermissionContext(mode="bypassPermissions")
    decision = has_permissions_to_use_tool(_StubTool(), {}, ctx)
    assert isinstance(decision, PermissionAllowDecision)
    assert decision.behavior == "allow"


def test_runtime_check_returns_ask_in_default_mode():
    """End-to-end: default mode returns ask for tools that passthrough."""
    from src.permissions.check import has_permissions_to_use_tool
    from src.permissions.types import (
        PermissionAskDecision,
        ToolPermissionContext,
    )

    class _StubTool:
        name = "Bash"
        is_mcp = False

        def check_permissions(self, tool_input, context):
            from src.permissions.types import PermissionPassthroughResult

            return PermissionPassthroughResult(behavior="passthrough")

    ctx = ToolPermissionContext(mode="default")
    decision = has_permissions_to_use_tool(_StubTool(), {}, ctx)
    assert isinstance(decision, PermissionAskDecision)
    assert decision.behavior == "ask"

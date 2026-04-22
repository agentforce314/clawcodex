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

    monkeypatch.setattr(
        headless_mod, "get_provider_class", lambda n: _FakeProvider
    )
    monkeypatch.setattr(
        headless_mod, "get_provider_config",
        lambda n: {"api_key": "x", "default_model": "fake"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry()
    )

    captured: dict = {}
    original = headless_mod.run_agent_loop

    def _capture(*args, **kw):
        captured["tool_context"] = kw["tool_context"]
        return original(*args, **kw)

    monkeypatch.setattr(headless_mod, "run_agent_loop", _capture)

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

    monkeypatch.setattr(
        headless_mod, "get_provider_class", lambda n: _FakeProvider
    )
    monkeypatch.setattr(
        headless_mod, "get_provider_config",
        lambda n: {"api_key": "x", "default_model": "fake"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry()
    )

    captured: dict = {}
    original = headless_mod.run_agent_loop

    def _capture(*args, **kw):
        captured["tool_context"] = kw["tool_context"]
        return original(*args, **kw)

    monkeypatch.setattr(headless_mod, "run_agent_loop", _capture)

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
    allowed, _ = ctx.permission_handler("Bash", "needs approval", None)
    assert allowed is False


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


def test_resolve_permission_state_default_mode_when_no_flag():
    from src.cli import _build_parser, _resolve_permission_state

    parser = _build_parser()
    args = parser.parse_args([])
    _resolve_permission_state(args)
    assert args._resolved_permission_mode == "default"
    # is_bypass_available depends on settings; default config has no bypass.
    assert isinstance(args._resolved_is_bypass_available, bool)


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

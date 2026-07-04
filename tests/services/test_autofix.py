"""SERVICES-2 — autoFix (lint/test-on-edit → model self-fix).

Port of typescript/src/services/autoFix/ with a DOCUMENTED DIVERGENCE (D1):
TS's AUTO_FIX_TOOLS = {file_edit,file_write} never matches real tool names
(Edit/Write), so the reference feature is dead code; this port activates the
author's intent ({Edit,Write}), gated behind the settings.autoFix opt-in.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.services.autofix.config import AutoFixConfig, get_auto_fix_config
from src.services.autofix.hook import (
    AUTO_FIX_TOOLS,
    build_auto_fix_context,
    build_max_retries_context,
    should_run_auto_fix,
)
from src.services.autofix.runner import AutoFixResult, run_auto_fix_check


def _run(coro):
    return asyncio.run(coro)


class TestConfig:
    def test_enabled_with_lint(self):
        c = get_auto_fix_config({"enabled": True, "lint": "eslint ."})
        assert c is not None and c.lint == "eslint ." and c.max_retries == 3

    def test_disabled_returns_none(self):
        assert get_auto_fix_config({"enabled": False, "lint": "x"}) is None

    def test_enabled_without_lint_or_test_none(self):
        # the zod .refine
        assert get_auto_fix_config({"enabled": True}) is None

    def test_non_dict_none(self):
        assert get_auto_fix_config("nope") is None
        assert get_auto_fix_config(None) is None

    def test_camelcase_keys_honored(self):
        # critic M2: reads maxRetries / timeout (NOT timeout_ms).
        c = get_auto_fix_config(
            {"enabled": True, "test": "t", "maxRetries": 5, "timeout": 12000}
        )
        assert c.max_retries == 5 and c.timeout_ms == 12000

    def test_out_of_range_rejects_not_clamps(self):
        # critic M1: zod .min/.max REJECTS → whole config None (not clamp).
        assert get_auto_fix_config({"enabled": True, "test": "t", "maxRetries": 20}) is None
        assert get_auto_fix_config({"enabled": True, "test": "t", "maxRetries": -1}) is None
        assert get_auto_fix_config({"enabled": True, "test": "t", "timeout": 999}) is None
        assert get_auto_fix_config({"enabled": True, "test": "t", "timeout": 999999}) is None

    def test_non_int_rejects(self):
        assert get_auto_fix_config({"enabled": True, "test": "t", "maxRetries": "3"}) is None


class TestHook:
    def test_tool_set_is_edit_write_intent(self):
        # critic M4 + D1: the author's intent, not the dead literal or the
        # 4-element _FILE_EDIT_TOOLS.
        assert AUTO_FIX_TOOLS == frozenset({"Edit", "Write"})

    def test_should_run(self):
        c = AutoFixConfig(enabled=True, lint="x")
        assert should_run_auto_fix("Edit", c) and should_run_auto_fix("Write", c)
        assert not should_run_auto_fix("Read", c)
        assert not should_run_auto_fix("NotebookEdit", c)  # excluded, per intent
        assert not should_run_auto_fix("Edit", None)

    def test_build_context_verbatim(self):
        ctx = build_auto_fix_context(
            AutoFixResult(has_errors=True, error_summary="Lint errors (exit code 1):\nbad")
        )
        assert ctx.startswith("<auto_fix_feedback>\nAUTO-FIX: The file you just edited has errors")
        assert "Do not ask the user — just apply the fix." in ctx
        assert ctx.endswith("</auto_fix_feedback>")

    def test_build_context_none_when_clean(self):
        assert build_auto_fix_context(AutoFixResult(has_errors=False)) is None

    def test_max_retries_message_verbatim(self):
        m = build_max_retries_context(3)
        assert "Maximum retry limit (3) reached" in m
        assert m.startswith("<auto_fix_feedback>") and m.endswith("</auto_fix_feedback>")


class TestRunner:
    def test_lint_fail_skips_test(self):
        r = _run(run_auto_fix_check(lint="exit 1", test="echo NO", timeout_ms=5000, cwd="/tmp"))
        assert r.has_errors and r.lint_exit_code == 1 and r.test_exit_code is None
        assert "Lint errors (exit code 1)" in r.error_summary

    def test_lint_pass_test_fail(self):
        r = _run(run_auto_fix_check(lint="true", test="exit 2", timeout_ms=5000, cwd="/tmp"))
        assert r.has_errors and r.test_exit_code == 2 and "Test failures (exit code 2)" in r.error_summary

    def test_both_clean(self):
        r = _run(run_auto_fix_check(lint="true", test="true", timeout_ms=5000, cwd="/tmp"))
        assert not r.has_errors

    def test_timeout_kills_group(self):
        import time

        t0 = time.time()
        r = _run(run_auto_fix_check(lint="sleep 30", test=None, timeout_ms=800, cwd="/tmp"))
        assert r.timed_out and r.has_errors and (time.time() - t0) < 5
        assert "Command timed out." in r.error_summary

    def test_output_capped_at_10k(self):
        r = _run(run_auto_fix_check(
            lint='python3 -c "print(chr(120)*50000)"; exit 1', test=None,
            timeout_ms=5000, cwd="/tmp",
        ))
        assert r.has_errors and len(r.lint_output) <= 10002  # 10k + the "\n" join strip slack

    def test_abort_pre_set(self):
        class _Sig:
            aborted = True

        r = _run(run_auto_fix_check(lint="exit 1", test=None, timeout_ms=5000, cwd="/tmp", abort_signal=_Sig()))
        assert not r.has_errors

    def test_mid_flight_abort_no_errors(self):
        import time

        class _Sig:
            aborted = False

        sig = _Sig()

        async def go():
            task = asyncio.ensure_future(run_auto_fix_check(
                lint="sleep 30", test=None, timeout_ms=60000, cwd="/tmp", abort_signal=sig,
            ))
            await asyncio.sleep(0.2)
            sig.aborted = True
            return await asyncio.wait_for(task, timeout=5)

        t0 = time.time()
        r = asyncio.run(go())
        assert not r.has_errors and not r.timed_out and (time.time() - t0) < 5

    def test_no_commands_no_errors(self):
        assert not _run(run_auto_fix_check(lint=None, test=None, timeout_ms=5000, cwd="/tmp")).has_errors


class TestStep:
    """The wiring step — incl. the B1 regression (runs with ZERO PostToolUse
    hooks) and the M3 reset-on-success."""

    def _ctx(self, chain="c1"):
        return SimpleNamespace(
            query_tracking=SimpleNamespace(chain_id=chain),
            abort_controller=None,
            cwd="/tmp",
        )

    def _collect(self, ctx, tool="Edit", tool_use_id="tu"):
        from src.services.autofix.step import run_auto_fix_step

        async def go():
            return [m async for m in run_auto_fix_step(ctx, tool, tool_use_id)]

        return asyncio.run(go())

    def _patch_config(self, monkeypatch, cfg):
        import src.services.autofix.step as stepmod

        monkeypatch.setattr(stepmod, "load_auto_fix_config", lambda: cfg)

    def test_fires_with_zero_posttool_hooks(self, monkeypatch):
        """B1 regression — the single most important case: autoFix runs even
        when NO user PostToolUse hook is configured (the common case). It's a
        sibling of run_post_tool_use_hooks, not gated by has_hook_for_event.
        The ctx here has no hook_config_manager at all."""
        import src.services.autofix.step as stepmod

        stepmod._auto_fix_retry_count.clear()
        self._patch_config(monkeypatch, AutoFixConfig(enabled=True, lint="exit 1", max_retries=3))
        out = self._collect(self._ctx())
        assert len(out) == 1 and "auto_fix_feedback" in str(out[0])

    def test_non_file_tool_skips(self, monkeypatch):
        self._patch_config(monkeypatch, AutoFixConfig(enabled=True, lint="exit 1"))
        assert self._collect(self._ctx(), tool="Bash") == []

    def test_no_config_skips(self, monkeypatch):
        self._patch_config(monkeypatch, None)
        assert self._collect(self._ctx(), tool="Edit") == []

    def test_retry_cap_fires_max_message(self, monkeypatch):
        import src.services.autofix.step as stepmod

        stepmod._auto_fix_retry_count.clear()
        self._patch_config(monkeypatch, AutoFixConfig(enabled=True, lint="exit 1", max_retries=2))
        ctx = self._ctx("cap")
        self._collect(ctx)  # 1
        self._collect(ctx)  # 2
        capped = self._collect(ctx)  # 2 >= 2 → capped message
        assert len(capped) == 1 and "Maximum retry limit (2)" in str(capped[0])

    def test_reset_on_clean_run(self, monkeypatch):
        import src.services.autofix.step as stepmod

        stepmod._auto_fix_retry_count.clear()
        stepmod._auto_fix_retry_count["r"] = 1
        self._patch_config(monkeypatch, AutoFixConfig(enabled=True, lint="true", max_retries=3))
        self._collect(self._ctx("r"))
        assert "r" not in stepmod._auto_fix_retry_count  # cleared on clean run

    def test_none_query_tracking_uses_default_key(self, monkeypatch):
        # critic M5: query_tracking=None must not crash.
        import src.services.autofix.step as stepmod

        stepmod._auto_fix_retry_count.clear()
        self._patch_config(monkeypatch, AutoFixConfig(enabled=True, lint="exit 1", max_retries=3))
        ctx = SimpleNamespace(query_tracking=None, abort_controller=None, cwd="/tmp")
        out = self._collect(ctx)
        assert len(out) == 1 and "default" in stepmod._auto_fix_retry_count

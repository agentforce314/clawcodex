"""Phase-1 / WI-1.4 — hook output JSON schema validation.

Replaces the pre-Phase-1 ad-hoc ``dict.get`` parsing block in
``_execute_command_hook``. Failure mode the schema closes: capital-D
``"Deny"`` (instead of ``"deny"``) used to silently no-op; now it logs a
WARNING and the executor drops the decision payload (exit code is still
honored).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from src.hooks.hook_types import HookConfig
from src.hooks.hook_executor import _execute_command_hook
from src.hooks.output_schema import HookOutput, parse_hook_output


class TestParseHookOutput:
    def test_empty_stdout_returns_no_output_no_error(self):
        out, err = parse_hook_output("")
        assert out is None
        assert err is None

    def test_whitespace_only_returns_no_output_no_error(self):
        out, err = parse_hook_output("   \n  ")
        assert out is None
        assert err is None

    def test_valid_decision_allow(self):
        out, err = parse_hook_output(json.dumps({"decision": "allow", "reason": "test"}))
        assert err is None
        assert out is not None
        assert out.decision == "allow"
        assert out.reason == "test"

    def test_valid_decision_deny(self):
        out, err = parse_hook_output(json.dumps({"decision": "deny"}))
        assert err is None
        assert out is not None
        assert out.decision == "deny"

    def test_valid_decision_ask(self):
        out, err = parse_hook_output(json.dumps({"decision": "ask"}))
        assert err is None
        assert out is not None
        assert out.decision == "ask"

    def test_capital_d_deny_rejected(self):
        # The headline failure mode the schema closes: capital-D used to
        # silently no-op; now it's a validation error and the decision is
        # dropped.
        out, err = parse_hook_output(json.dumps({"decision": "Deny"}))
        assert out is None
        assert err is not None
        assert "schema validation" in err.lower()

    def test_unknown_field_rejected(self):
        out, err = parse_hook_output(json.dumps({
            "decision": "allow", "stowaway": 1,
        }))
        assert out is None
        assert err is not None
        assert "schema validation" in err.lower()

    def test_malformed_json_rejected(self):
        out, err = parse_hook_output("not json at all {")
        assert out is None
        assert err is not None
        assert "json" in err.lower()

    def test_non_object_json_rejected(self):
        # Top-level array/string/etc. is invalid even if it parses as JSON.
        out, err = parse_hook_output(json.dumps([1, 2, 3]))
        assert out is None
        assert err is not None

    def test_updated_input_round_trip(self):
        out, err = parse_hook_output(json.dumps({
            "updatedInput": {"command": "safer_cmd"},
        }))
        assert err is None
        assert out is not None
        assert out.updatedInput == {"command": "safer_cmd"}

    def test_prevent_continuation_with_reason(self):
        out, err = parse_hook_output(json.dumps({
            "preventContinuation": True,
            "stopReason": "verification failed",
        }))
        assert err is None
        assert out is not None
        assert out.preventContinuation is True
        assert out.stopReason == "verification failed"


class TestExecutorWiresSchema:
    @pytest.mark.asyncio
    async def test_capital_d_deny_logged_not_silently_ignored(self, caplog):
        # Run a command hook whose stdout is malformed by the new schema.
        # Pre-Phase-1: silently dropped. Post-Phase-1: logged at WARNING.
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "Deny"}\'',  # capital D — invalid
        )
        with caplog.at_level(logging.WARNING, logger="src.hooks.hook_executor"):
            result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})

        # Decision payload was dropped (didn't make it through schema).
        assert result.permission_behavior is None
        # WARNING was logged about the failed validation.
        assert any(
            "schema validation" in rec.message.lower() or
            "failed schema" in rec.message.lower()
            for rec in caplog.records
        ), f"Expected schema-validation WARNING; saw: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_valid_lowercase_decision_round_trips_through_executor(self):
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "deny", "reason": "blocked"}\'',
        )
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.permission_behavior == "deny"
        assert result.hook_permission_decision_reason == "blocked"

    @pytest.mark.asyncio
    async def test_unknown_field_at_executor_level_logged(self, caplog):
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "allow", "stowaway": 1}\'',
        )
        with caplog.at_level(logging.WARNING, logger="src.hooks.hook_executor"):
            result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.permission_behavior is None  # decision dropped
        assert any("schema validation" in rec.message.lower() for rec in caplog.records)

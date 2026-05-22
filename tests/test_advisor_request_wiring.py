"""Verify the request-time wiring in ``src/query/query.py:_call_model_sync``.

Three classes of assertion:
  * Activation gate: advisor decision == (first-party Anthropic AND
    settings.advisor_model AND model_supports_advisor AND
    is_valid_advisor_model).
  * On activation: schema appended after regular tools, beta header
    set, instructions appended to system_prompt, advisor blocks NOT
    stripped from outgoing messages.
  * Off activation: schema absent, beta absent, instructions absent,
    advisor blocks STRIPPED from outgoing messages (so the API doesn't
    400 on a stale server_tool_use without the beta header).

Tests intercept ``provider.chat_stream_response`` to inspect the kwargs
the query layer would have sent to the SDK. This avoids opening any
network connection.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import ChatResponse
from src.query.query import _call_model_sync
from src.types.messages import AssistantMessage, UserMessage


class _Capture:
    """Holds the kwargs captured from a single ``chat_stream_response`` call."""
    api_messages: list = None
    call_kwargs: dict = None


def _stub_provider_class(provider_cls, captured: _Capture) -> Any:
    """Create a provider instance that records the kwargs it was called with."""
    if provider_cls is AnthropicProvider:
        provider = MagicMock(spec=AnthropicProvider)
        provider.has_custom_endpoint = MagicMock(return_value=False)
    else:
        provider = MagicMock(spec=provider_cls)
    provider.model = "claude-opus-4-6"

    def fake_chat_stream_response(api_messages, *, abort_signal=None, **kwargs):
        captured.api_messages = list(api_messages)
        captured.call_kwargs = dict(kwargs)
        return ChatResponse(
            content="ok",
            model="claude-opus-4-6",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
            raw_content_blocks=None,
        )

    provider.chat_stream_response = fake_chat_stream_response
    return provider


class _Isolation:
    """Helper that monkeypatches the config-file paths to a tmp dir.

    ``src/config.py`` evaluates ``GLOBAL_CONFIG_FILE = Path.home() /
    ".clawcodex/config.json"`` at import time, so patching ``HOME``
    after import is too late — writes would land on the real user's
    config file. We override the module-level constant directly.

    The helper is used as a class with ``enter()`` / ``exit()`` from
    test setUp / tearDown rather than via ``with`` so existing
    setUp/tearDown shapes don't need restructuring.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="advisor_wire_")
        self._saved_global = None
        self._saved_history = None
        self._saved_dir = None

    def enter(self) -> None:
        import src.config as cfg_mod
        self._saved_global = cfg_mod.GLOBAL_CONFIG_FILE
        self._saved_history = cfg_mod.HISTORY_FILE
        self._saved_dir = cfg_mod.GLOBAL_CONFIG_DIR
        from pathlib import Path as _P
        cfg_mod.GLOBAL_CONFIG_FILE = _P(self._tmp) / ".clawcodex" / "config.json"
        cfg_mod.HISTORY_FILE = _P(self._tmp) / ".clawcodex" / "history.jsonl"
        cfg_mod.GLOBAL_CONFIG_DIR = _P(self._tmp) / ".clawcodex"
        cfg_mod._default_manager = None
        from src.settings.settings import invalidate_settings_cache
        invalidate_settings_cache()

    def exit(self) -> None:
        import src.config as cfg_mod
        cfg_mod.GLOBAL_CONFIG_FILE = self._saved_global
        cfg_mod.HISTORY_FILE = self._saved_history
        cfg_mod.GLOBAL_CONFIG_DIR = self._saved_dir
        cfg_mod._default_manager = None
        from src.settings.settings import invalidate_settings_cache
        invalidate_settings_cache()


def _isolate_env():
    """Build an _Isolation helper. setUp/tearDown call .enter/.exit."""
    return _Isolation()


def _set_settings(**kwargs):
    """Set fields on the cached SettingsSchema for the test.

    Advisor fields (``advisor_model``, ``advisor_provider``,
    ``advisor_client_mode``) are session-only — ``load_settings`` zeroes
    them on every load, so writing to ~/.clawcodex/config.json wouldn't
    survive an ``invalidate_settings_cache()``. The /advisor command
    likewise mutates the cached SettingsSchema in place; the test
    helper mirrors that behaviour to keep the wiring identical.
    """
    from src.settings.settings import get_settings
    settings = get_settings()
    for k, v in kwargs.items():
        setattr(settings, k, v)


def _run(provider, messages, system_prompt="hi", tools=None):
    return asyncio.run(
        _call_model_sync(
            provider=provider,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools or [],
        )
    )


class TestAdvisorActiveOnFirstPartyAnthropic(unittest.TestCase):
    """Full activation path: beta header + schema + instructions added."""

    def setUp(self) -> None:
        self._iso = _isolate_env()
        self._iso.enter()
        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        _set_settings(advisor_model="claude-opus-4-6", advisor_provider="anthropic")

    def tearDown(self) -> None:
        # _iso.exit() restores the real config paths AND clears the
        # singleton cache; the tmp dir from .enter() is its own world,
        # so no additional cleanup write is needed (and any write here
        # would land on the real user's config — see the isolation
        # helper docstring above).
        self._iso.exit()

    def test_beta_header_attached(self) -> None:
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        _run(provider, [UserMessage(content="please")])
        self.assertIn("betas", cap.call_kwargs)
        self.assertIn("advisor-tool-2026-03-01", cap.call_kwargs["betas"])

    def test_schema_appended_after_regular_tools(self) -> None:
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)

        class _FakeTool:
            name = "Bash"
            input_schema = {"type": "object", "properties": {}}
            def prompt(self) -> str:
                return "fake bash tool"

        _run(provider, [UserMessage(content="x")], tools=[_FakeTool()])
        tools = cap.call_kwargs["tools"]
        # Regular tool first, advisor LAST — preserves the cache_control
        # marker position from any other tool.
        self.assertEqual(tools[0]["name"], "Bash")
        self.assertEqual(tools[-1]["type"], "advisor_20260301")
        self.assertEqual(tools[-1]["name"], "advisor")
        self.assertEqual(tools[-1]["model"], "claude-opus-4-6")

    def test_instructions_appended_to_string_system_prompt(self) -> None:
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        _run(provider, [UserMessage(content="x")], system_prompt="base prompt")
        sysp = cap.call_kwargs.get("system")
        self.assertIsInstance(sysp, str)
        self.assertIn("base prompt", sysp)
        self.assertIn("# Advisor Tool", sysp)

    def test_instructions_appended_to_block_list_system_prompt(self) -> None:
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        sysp_in = [
            {"type": "text", "text": "block 1"},
            {"type": "text", "text": "block 2", "cache_control": {"type": "ephemeral"}},
        ]
        _run(provider, [UserMessage(content="x")], system_prompt=sysp_in)
        sysp_out = cap.call_kwargs.get("system")
        self.assertIsInstance(sysp_out, list)
        # Instructions land at the END so the cache_control marker on
        # block 2 keeps its position relative to the prefix.
        self.assertEqual(sysp_out[-1]["type"], "text")
        self.assertIn("# Advisor Tool", sysp_out[-1]["text"])
        # Earlier blocks (including the cached one) are unchanged.
        self.assertEqual(sysp_out[0]["text"], "block 1")
        self.assertEqual(sysp_out[1]["text"], "block 2")
        self.assertIn("cache_control", sysp_out[1])

    def test_advisor_blocks_in_history_are_kept(self) -> None:
        # When the request will carry the beta header, historical
        # advisor blocks must round-trip back to the API as a valid
        # use/result pair.
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        prior = AssistantMessage(
            content=[
                {"type": "text", "text": "before"},
                {
                    "type": "server_tool_use",
                    "id": "srv_1",
                    "name": "advisor",
                    "input": {},
                },
                {
                    "type": "advisor_tool_result",
                    "tool_use_id": "srv_1",
                    "content": {"type": "advisor_result", "text": "advice"},
                },
            ],
        )
        _run(provider, [UserMessage(content="hi"), prior, UserMessage(content="next")])
        # Inspect the assistant message in the API payload.
        asst = next(
            m for m in cap.api_messages
            if m.get("role") == "assistant"
        )
        types = [b["type"] for b in asst["content"]]
        self.assertIn("server_tool_use", types)
        self.assertIn("advisor_tool_result", types)


class TestAdvisorInactivePaths(unittest.TestCase):
    """Negative cases — schema/header/instructions MUST NOT be sent."""

    def setUp(self) -> None:
        self._iso = _isolate_env()
        self._iso.enter()
        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)

    def tearDown(self) -> None:
        # _iso.exit() restores the real config paths AND clears the
        # singleton cache; the tmp dir from .enter() is its own world,
        # so no additional cleanup write is needed (and any write here
        # would land on the real user's config — see the isolation
        # helper docstring above).
        self._iso.exit()

    def _assert_inactive(self, cap: _Capture) -> None:
        """Assert NO advisor of either flavor leaks into the request."""
        self.assertNotIn("betas", cap.call_kwargs)
        tools = cap.call_kwargs.get("tools") or []
        for t in tools:
            self.assertNotEqual(
                t.get("type"), "advisor_20260301",
                "server-side schema must not be sent when inactive",
            )
            self.assertNotEqual(
                t.get("name"), "advisor",
                "advisor tool must not appear in tools when inactive",
            )
        sysp = cap.call_kwargs.get("system", "") or ""
        if isinstance(sysp, list):
            sysp_text = "\n".join(
                b.get("text", "") for b in sysp if isinstance(b, dict)
            )
        else:
            sysp_text = sysp
        self.assertNotIn("# Advisor Tool", sysp_text)

    def _assert_server_side_not_sent(self, cap: _Capture) -> None:
        """Assert SERVER-SIDE artifacts absent — but CLIENT-SIDE may be present."""
        self.assertNotIn("betas", cap.call_kwargs)
        tools = cap.call_kwargs.get("tools") or []
        for t in tools:
            self.assertNotEqual(
                t.get("type"), "advisor_20260301",
                "server-side schema must not leak in client-side mode",
            )

    def _assert_client_side_active(self, cap: _Capture) -> None:
        """Assert CLIENT-SIDE: regular-shape advisor tool + instructions."""
        self._assert_server_side_not_sent(cap)
        tools = cap.call_kwargs.get("tools") or []
        advisor_tools = [t for t in tools if t.get("name") == "advisor"]
        self.assertEqual(
            len(advisor_tools), 1,
            "client-side mode must register the regular-tool advisor",
        )
        # Regular tool shape — no ``type`` discriminator, just name +
        # description + input_schema.
        self.assertNotIn("type", advisor_tools[0])
        self.assertIn("input_schema", advisor_tools[0])
        sysp = cap.call_kwargs.get("system", "") or ""
        if isinstance(sysp, list):
            sysp_text = "\n".join(
                b.get("text", "") for b in sysp if isinstance(b, dict)
            )
        else:
            sysp_text = sysp
        self.assertIn("# Advisor Tool", sysp_text)

    def test_no_advisor_when_settings_unset(self) -> None:
        _set_settings(advisor_model="")
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        _run(provider, [UserMessage(content="x")])
        self._assert_inactive(cap)

    def test_no_advisor_when_env_disabled(self) -> None:
        _set_settings(advisor_model="claude-opus-4-6", advisor_provider="anthropic")
        with patch.dict(
            os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False
        ):
            cap = _Capture()
            provider = _stub_provider_class(AnthropicProvider, cap)
            _run(provider, [UserMessage(content="x")])
            self._assert_inactive(cap)

    def test_no_advisor_when_provider_unknown(self) -> None:
        # Post multi-provider rewrite: routing is decided by the
        # explicit advisor_provider, not the model name. An UNKNOWN
        # provider key (no registered Provider class) → INACTIVE.
        # The old "unknown model name" scenario no longer triggers
        # this branch — model names aren't validated against any list
        # in client-side mode.
        _set_settings(
            advisor_model="claude-opus-4-6",
            advisor_provider="not-a-real-provider-zzz",
        )
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        _run(provider, [UserMessage(content="x")])
        self._assert_inactive(cap)

    def test_client_side_when_anthropic_has_custom_endpoint(self) -> None:
        # Custom-endpoint Anthropic is treated as 3P for server-side
        # purposes (no beta header, no advisor_20260301 schema), but
        # client-side mode covers it now.
        _set_settings(advisor_model="claude-opus-4-6", advisor_provider="anthropic")
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        provider.has_custom_endpoint = MagicMock(return_value=True)
        _run(provider, [UserMessage(content="x")])
        self._assert_client_side_active(cap)

    def test_client_side_when_base_model_does_not_support_server_side(self) -> None:
        # opus-4-5 doesn't support the server-side beta — under the
        # old strict rules, advisor went inactive. Now it falls back to
        # client-side (any tool-calling main loop works).
        _set_settings(advisor_model="claude-opus-4-6", advisor_provider="anthropic")
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        provider.model = "claude-opus-4-5"
        _run(provider, [UserMessage(content="x")])
        self._assert_client_side_active(cap)

    def test_client_side_when_advisor_is_haiku(self) -> None:
        # haiku-4-5 isn't a valid server-side advisor (only opus-4-6 /
        # sonnet-4-6 are) — but it routes to anthropic and works
        # client-side.
        _set_settings(advisor_model="claude-haiku-4-5", advisor_provider="anthropic")
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        _run(provider, [UserMessage(content="x")])
        self._assert_client_side_active(cap)

    def test_advisor_blocks_stripped_when_inactive(self) -> None:
        # The previous turn left advisor blocks in history, but the
        # current request will NOT carry the beta header (e.g. user
        # ran /advisor unset). The API would 400 if we sent them.
        _set_settings(advisor_model="")  # inactive
        cap = _Capture()
        provider = _stub_provider_class(AnthropicProvider, cap)
        prior = AssistantMessage(
            content=[
                {"type": "text", "text": "before"},
                {
                    "type": "server_tool_use",
                    "id": "srv_1",
                    "name": "advisor",
                    "input": {},
                },
                {
                    "type": "advisor_tool_result",
                    "tool_use_id": "srv_1",
                    "content": {"type": "advisor_result", "text": "advice"},
                },
                {"type": "text", "text": "after"},
            ],
        )
        _run(provider, [UserMessage(content="hi"), prior, UserMessage(content="next")])
        asst = next(
            m for m in cap.api_messages if m.get("role") == "assistant"
        )
        types = [b["type"] for b in asst["content"]]
        self.assertNotIn("server_tool_use", types)
        self.assertNotIn("advisor_tool_result", types)


class TestAdvisorActivationDefensive(unittest.TestCase):
    """The activation predicate is wrapped in a single try/except so any
    failure (transient import, future provider that throws on
    `has_custom_endpoint`, settings cache contention) defaults to
    "advisor inactive" rather than failing the turn. Critic-flagged
    nit: the fix had no dedicated test — adding one here.
    """

    def test_activation_exception_does_not_kill_turn(self) -> None:
        # Force `is_advisor_enabled` to raise inside `_call_model_sync`.
        # The expected behavior is: caught, advisor_active=False, the
        # request proceeds without the beta header or schema.
        iso = _isolate_env()
        iso.enter()
        try:
            _set_settings(advisor_model="claude-opus-4-6", advisor_provider="anthropic")
            cap = _Capture()
            provider = _stub_provider_class(AnthropicProvider, cap)
            with patch(
                "src.utils.advisor.is_advisor_enabled",
                side_effect=RuntimeError("synthetic gate failure"),
            ):
                # Must not raise; should fall through to a non-advisor
                # request.
                result, _ = _run(provider, [UserMessage(content="hi")])
            self.assertEqual(len(result), 1)
            self.assertNotIn("betas", cap.call_kwargs)
            tools = cap.call_kwargs.get("tools") or []
            for t in tools:
                self.assertNotEqual(t.get("type"), "advisor_20260301")
        finally:
            iso.exit()


if __name__ == "__main__":
    unittest.main()

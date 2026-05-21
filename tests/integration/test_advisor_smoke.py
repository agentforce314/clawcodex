"""End-to-end advisor smoke tests against a mocked Anthropic SDK stream.

Two scenarios:
  1. Happy path — the stream emits text + advisor server_tool_use +
     advisor_tool_result + text. Verify the assembled assistant message
     preserves all four blocks AND the next-turn outbound request
     includes the use/result pair in history (because the beta header
     keeps going).
  2. Interrupt path — the stream emits text + advisor server_tool_use
     without a matching result (simulating ESC mid-advisor). Verify
     the next turn's outbound message has the orphan ``server_tool_use``
     stripped by ``ensure_tool_result_pairing`` so the API doesn't 400.
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


class _Isolation:
    """Override the module-level config-path constants to a tmp dir.

    See ``tests/test_advisor_request_wiring.py`` for the rationale —
    ``src/config.py`` freezes the path constants at import time so a
    plain ``HOME`` patch is too late.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="advisor_smoke_")
        self._saved_global = None
        self._saved_history = None
        self._saved_dir = None

    def enter(self) -> None:
        import src.config as cfg_mod
        from pathlib import Path as _P
        self._saved_global = cfg_mod.GLOBAL_CONFIG_FILE
        self._saved_history = cfg_mod.HISTORY_FILE
        self._saved_dir = cfg_mod.GLOBAL_CONFIG_DIR
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
    return _Isolation()


def _set_advisor(value: str) -> None:
    import src.config as cfg_mod
    from src.config import ConfigManager
    from src.settings.settings import invalidate_settings_cache
    cfg_mod._default_manager = None
    mgr = ConfigManager()
    cfg = mgr.load_global()
    sub = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    sub["advisor_model"] = value
    # Post multi-provider rewrite: advisor_provider is required for
    # the advisor to be considered active. Default to "anthropic" for
    # these smoke tests since they all exercise the first-party
    # server-side path; tests overriding to a non-anthropic provider
    # would need to set this explicitly.
    sub["advisor_provider"] = "anthropic" if value else ""
    cfg["settings"] = sub
    mgr.save_global(cfg)
    invalidate_settings_cache()


def _build_fake_anthropic_response(content_blocks: list[dict]) -> Any:
    """Build a fake ChatResponse with all the blocks the SDK would surface.

    We bypass ``messages.stream`` entirely and stub
    ``chat_stream_response`` to return the assembled ChatResponse — the
    SDK code path is exercised by ``test_provider_anthropic.py``
    elsewhere; here we want to verify the query layer assembles blocks
    correctly when the provider does its job.
    """
    raw_blocks: list[dict] = []
    text_content = ""
    tool_uses: list[dict] = []
    for block in content_blocks:
        btype = block.get("type")
        if btype == "text":
            text_content += block.get("text", "")
        elif btype == "tool_use":
            tool_uses.append({
                "id": block["id"],
                "name": block["name"],
                "input": block.get("input", {}),
            })
        elif btype in ("server_tool_use", "advisor_tool_result"):
            raw_blocks.append(dict(block))
    return ChatResponse(
        content=text_content,
        model="claude-opus-4-6",
        usage={"input_tokens": 5, "output_tokens": 20},
        finish_reason="end_turn",
        tool_uses=tool_uses or None,
        raw_content_blocks=raw_blocks or None,
    )


class _Capture:
    api_messages: list = None
    call_kwargs: dict = None


def _make_provider(captured: _Capture, *, response_blocks: list[dict]):
    provider = MagicMock(spec=AnthropicProvider)
    provider.has_custom_endpoint = MagicMock(return_value=False)
    provider.model = "claude-opus-4-6"

    def fake_chat_stream_response(api_messages, *, abort_signal=None, **kwargs):
        captured.api_messages = list(api_messages)
        captured.call_kwargs = dict(kwargs)
        return _build_fake_anthropic_response(response_blocks)

    provider.chat_stream_response = fake_chat_stream_response
    return provider


class TestAdvisorHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self._iso = _isolate_env()
        self._iso.enter()
        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        _set_advisor("claude-opus-4-6")

    def tearDown(self) -> None:
        # _iso.exit() restores the real config paths AND clears the
        # singleton cache; any write here would leak onto the real
        # user's config file. The tmp dir from .enter() is its own
        # world — no additional cleanup write needed.
        self._iso.exit()

    def test_advisor_pair_preserved_in_history(self) -> None:
        cap = _Capture()
        response_blocks = [
            {"type": "text", "text": "Let me check with the advisor. "},
            {
                "type": "server_tool_use",
                "id": "srv_001",
                "name": "advisor",
                "input": {},
            },
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_001",
                "content": {"type": "advisor_result", "text": "Looks good."},
            },
            {"type": "text", "text": "Proceeding."},
        ]
        provider = _make_provider(cap, response_blocks=response_blocks)
        # Turn 1.
        result_msgs, _ = asyncio.run(_call_model_sync(
            provider=provider,
            messages=[UserMessage(content="What should I do?")],
            system_prompt="sys",
            tools=[],
        ))
        # The assembled AssistantMessage MUST carry the advisor pair as
        # raw passthrough dicts so the next turn can replay them.
        self.assertEqual(len(result_msgs), 1)
        asst = result_msgs[0]
        types = []
        for b in asst.content if isinstance(asst.content, list) else []:
            if isinstance(b, dict):
                types.append(b.get("type"))
            else:
                types.append(getattr(b, "type", "?"))
        self.assertIn("server_tool_use", types)
        self.assertIn("advisor_tool_result", types)

        # Turn 2 — feed the assistant message back and confirm the API
        # payload preserves the pair (beta header still going, so no
        # strip should happen).
        cap2 = _Capture()
        provider2 = _make_provider(cap2, response_blocks=[
            {"type": "text", "text": "ok"},
        ])
        asyncio.run(_call_model_sync(
            provider=provider2,
            messages=[
                UserMessage(content="What should I do?"),
                asst,
                UserMessage(content="now what?"),
            ],
            system_prompt="sys",
            tools=[],
        ))
        # Beta header still attached (advisor still active).
        self.assertIn("advisor-tool-2026-03-01", cap2.call_kwargs.get("betas", []))
        # Advisor blocks survived through normalize → ensure_pairing.
        asst_payload = next(
            m for m in cap2.api_messages if m.get("role") == "assistant"
        )
        api_types = [b.get("type") for b in asst_payload["content"]]
        self.assertIn("server_tool_use", api_types)
        self.assertIn("advisor_tool_result", api_types)


class TestAdvisorInterruptPath(unittest.TestCase):
    def setUp(self) -> None:
        self._iso = _isolate_env()
        self._iso.enter()
        os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
        _set_advisor("claude-opus-4-6")

    def tearDown(self) -> None:
        # _iso.exit() restores the real config paths AND clears the
        # singleton cache; any write here would leak onto the real
        # user's config file. The tmp dir from .enter() is its own
        # world — no additional cleanup write needed.
        self._iso.exit()

    def test_orphan_use_stripped_next_turn(self) -> None:
        cap = _Capture()
        # Simulate an interrupted advisor — the use block landed but
        # the result never did (ESC mid-call). The provider returns it
        # as a raw passthrough block; the next turn must strip it.
        response_blocks = [
            {"type": "text", "text": "consulting"},
            {
                "type": "server_tool_use",
                "id": "srv_orphan",
                "name": "advisor",
                "input": {},
            },
        ]
        provider = _make_provider(cap, response_blocks=response_blocks)
        result_msgs, _ = asyncio.run(_call_model_sync(
            provider=provider,
            messages=[UserMessage(content="Help me")],
            system_prompt="sys",
            tools=[],
        ))
        asst = result_msgs[0]

        # Turn 2 — the next request must drop the orphan
        # ``server_tool_use`` (else the API rejects with "advisor tool
        # use without corresponding advisor_tool_result").
        cap2 = _Capture()
        provider2 = _make_provider(cap2, response_blocks=[
            {"type": "text", "text": "ok"},
        ])
        asyncio.run(_call_model_sync(
            provider=provider2,
            messages=[
                UserMessage(content="Help me"),
                asst,
                UserMessage(content="continue"),
            ],
            system_prompt="sys",
            tools=[],
        ))
        asst_payload = next(
            m for m in cap2.api_messages if m.get("role") == "assistant"
        )
        api_types = [b.get("type") for b in asst_payload["content"]]
        self.assertNotIn(
            "server_tool_use", api_types,
            "Orphan server_tool_use (advisor) MUST be stripped before send",
        )

    def test_orphan_stripped_even_with_beta_active(self) -> None:
        # Critical: the strip pass in ensure_tool_result_pairing applies
        # regardless of whether the beta header is going — it removes
        # orphans because the API rejects them in ALL cases, not just
        # when the header is absent.
        cap = _Capture()
        provider = _make_provider(cap, response_blocks=[
            {
                "type": "server_tool_use",
                "id": "srv_orphan_2",
                "name": "advisor",
                "input": {},
            },
        ])
        asst_msg = AssistantMessage(content=[
            {
                "type": "server_tool_use",
                "id": "srv_orphan_2",
                "name": "advisor",
                "input": {},
            },
        ])
        asyncio.run(_call_model_sync(
            provider=provider,
            messages=[
                UserMessage(content="hi"),
                asst_msg,
                UserMessage(content="x"),
            ],
            system_prompt="sys",
            tools=[],
        ))
        # Beta IS going (active advisor) — but the orphan still strips.
        self.assertIn("advisor-tool-2026-03-01", cap.call_kwargs.get("betas", []))
        asst_payload = next(
            m for m in cap.api_messages if m.get("role") == "assistant"
        )
        api_types = [b.get("type") for b in asst_payload["content"]]
        self.assertNotIn("server_tool_use", api_types)


if __name__ == "__main__":
    unittest.main()

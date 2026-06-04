"""Unit tests for ``src/utils/advisor.py`` — the gate predicates, block
detection, message-stripping pass, and schema builder for the
server-side advisor tool.

These tests cover the byte-for-byte parity surface against TS
``utils/advisor.ts`` plus the Python-specific gating helpers we added
on top.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.utils.advisor import (
    ADVISOR_BETA_HEADER,
    ADVISOR_TOOL_INSTRUCTIONS,
    ADVISOR_TOOL_NAME,
    ADVISOR_TOOL_TYPE,
    build_advisor_tool_schema,
    can_user_configure_advisor,
    is_advisor_block,
    is_advisor_enabled,
    is_valid_advisor_model,
    model_supports_advisor,
    strip_advisor_blocks,
)


class TestConstants(unittest.TestCase):
    def test_beta_header_value(self) -> None:
        # This string is load-bearing for API parity with TS — any change
        # silently shifts what the API accepts.
        self.assertEqual(ADVISOR_BETA_HEADER, "advisor-tool-2026-03-01")

    def test_tool_type_value(self) -> None:
        self.assertEqual(ADVISOR_TOOL_TYPE, "advisor_20260301")

    def test_tool_name_value(self) -> None:
        self.assertEqual(ADVISOR_TOOL_NAME, "advisor")

    def test_instructions_byte_identical_to_ts(self) -> None:
        # Pinned SHA256 of the TS template literal at
        # ``typescript/src/utils/advisor.ts:130-145`` (verified during
        # implementation). Drift in this prompt changes when the model
        # decides to call the advisor — keep them locked together.
        #
        # If the TS source is reachable from this checkout (it lives at
        # ``<repo_root>/typescript/`` outside the worktree), also do
        # the live byte-equality check. Either path is enough to fail
        # loudly on drift.
        import hashlib
        import re
        from pathlib import Path

        expected_sha = (
            "35a8fb57145324fe579360bbe94086f432187092ed1c53e3f147254f3afc674b"
        )
        actual_sha = hashlib.sha256(
            ADVISOR_TOOL_INSTRUCTIONS.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            actual_sha,
            expected_sha,
            "ADVISOR_TOOL_INSTRUCTIONS bytes drifted from TS — re-sync from "
            "typescript/src/utils/advisor.ts:130 and update the SHA pin.",
        )

        # Live cross-check when the TS source is reachable. We walk
        # parent directories from this test file to find the
        # ``typescript/`` peer, then read the literal. Skip silently
        # when it isn't around (e.g. inside a Python-only worktree).
        ts_path: Path | None = None
        for parent in Path(__file__).resolve().parents:
            cand = parent / "typescript" / "src" / "utils" / "advisor.ts"
            if cand.exists():
                ts_path = cand
                break
        if ts_path is None:
            return
        src = ts_path.read_text()
        m = re.search(
            r"ADVISOR_TOOL_INSTRUCTIONS = `(.+?)`$",
            src,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(m, "failed to extract TS template literal")
        # The only template-literal escapes used in this string are
        # backslash-backtick (used to wrap the word ``advisor`` in
        # backticks). Translate to plain backtick to compare against
        # Python's raw string.
        ts_text = m.group(1).replace(r"\`", "`")
        self.assertEqual(ts_text, ADVISOR_TOOL_INSTRUCTIONS)


class TestModelSupportsAdvisor(unittest.TestCase):
    """Mirror TS ``modelSupportsAdvisor`` and ``isValidAdvisorModel`` —
    predicates collapse to: opus-4-6 / sonnet-4-6 / USER_TYPE=ant."""

    def test_opus_4_6_supported(self) -> None:
        self.assertTrue(model_supports_advisor("claude-opus-4-6"))
        self.assertTrue(model_supports_advisor("CLAUDE-OPUS-4-6"))  # case-insensitive
        self.assertTrue(is_valid_advisor_model("claude-opus-4-6"))

    def test_sonnet_4_6_supported(self) -> None:
        self.assertTrue(model_supports_advisor("claude-sonnet-4-6"))
        self.assertTrue(is_valid_advisor_model("claude-sonnet-4-6"))

    def test_older_models_unsupported(self) -> None:
        for m in [
            "claude-opus-4-5",
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-3-5-sonnet-20241022",
            "claude-haiku-4-5",
            "",
        ]:
            with self.subTest(model=m):
                self.assertFalse(model_supports_advisor(m))
                self.assertFalse(is_valid_advisor_model(m))

    def test_none_treated_as_unsupported(self) -> None:
        self.assertFalse(model_supports_advisor(None))
        self.assertFalse(is_valid_advisor_model(None))

    @patch.dict(os.environ, {"USER_TYPE": "ant"})
    def test_ant_escape_hatch_supports_unknown_model(self) -> None:
        # TS preserves this escape so internal users can dogfood the
        # advisor against pre-release models — keep parity.
        self.assertTrue(model_supports_advisor("claude-some-future-thing"))
        self.assertTrue(is_valid_advisor_model("claude-some-future-thing"))


class TestIsAdvisorEnabled(unittest.TestCase):
    """``is_advisor_enabled`` collapses TS ``isAdvisorEnabled``'s
    env-disable + first-party-only check (we drop GrowthBook — see plan
    section "Intentional divergences from TS")."""

    def _fake_first_party_provider(self) -> MagicMock:
        from src.providers.anthropic_provider import AnthropicProvider
        provider = MagicMock(spec=AnthropicProvider)
        provider.has_custom_endpoint.return_value = False
        # spec'd MagicMock + isinstance check requires the spec class to
        # be the actual class; patch the cache-state lookup to accept
        # the mock. Simpler: patch is_first_party_provider directly.
        return provider

    @patch("src.utils.advisor.os.environ", {})
    def test_disabled_when_no_provider(self) -> None:
        self.assertFalse(is_advisor_enabled(None))

    def test_disabled_by_env_var(self) -> None:
        with patch.dict(
            os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False
        ):
            provider = MagicMock()
            self.assertFalse(is_advisor_enabled(provider))

    def test_truthy_env_values_disable(self) -> None:
        for v in ["1", "true", "TRUE", "yes", "On"]:
            with patch.dict(
                os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": v}, clear=False
            ):
                with self.subTest(env=v):
                    self.assertFalse(is_advisor_enabled(MagicMock()))

    def test_enabled_for_first_party_anthropic(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
            with patch(
                "src.state.cache_state.is_first_party_provider", return_value=True
            ):
                self.assertTrue(is_advisor_enabled(MagicMock()))

    def test_disabled_for_third_party_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
            with patch(
                "src.state.cache_state.is_first_party_provider", return_value=False
            ):
                self.assertFalse(is_advisor_enabled(MagicMock()))


class TestCanUserConfigureAdvisor(unittest.TestCase):
    """The slash-command visibility gate. Loosened to env-only after
    client-side mode shipped — /advisor is now valid on any provider.
    """

    def test_enabled_when_no_provider_and_no_env_disable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
            self.assertTrue(can_user_configure_advisor(None))

    def test_disabled_by_env_var_even_without_provider(self) -> None:
        with patch.dict(
            os.environ, {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, clear=False
        ):
            self.assertFalse(can_user_configure_advisor(None))

    def test_enabled_with_third_party_provider(self) -> None:
        # Pre-client-side, this was False. Post-client-side, /advisor
        # works on 3P (the client-side path dispatches to whatever
        # advisor model is configured) so we no longer reject upfront.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", None)
            with patch(
                "src.state.cache_state.is_first_party_provider", return_value=False
            ):
                self.assertTrue(can_user_configure_advisor(MagicMock()))


class TestIsAdvisorBlock(unittest.TestCase):
    def test_advisor_tool_result_detected(self) -> None:
        self.assertTrue(is_advisor_block({"type": "advisor_tool_result"}))

    def test_advisor_server_tool_use_detected(self) -> None:
        self.assertTrue(
            is_advisor_block(
                {"type": "server_tool_use", "name": "advisor", "id": "srv_1"}
            )
        )

    def test_non_advisor_server_tool_use_rejected(self) -> None:
        # Other server tools (web_search, code_execution, ...) MUST NOT
        # be misidentified as advisor blocks — they have their own
        # round-trip semantics.
        self.assertFalse(
            is_advisor_block(
                {"type": "server_tool_use", "name": "web_search", "id": "srv_2"}
            )
        )

    def test_plain_blocks_rejected(self) -> None:
        for block in [
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "x", "name": "Bash"},
            {"type": "tool_result", "tool_use_id": "x", "content": ""},
            {"type": "thinking", "thinking": "..."},
            {},
            None,
        ]:
            with self.subTest(block=block):
                self.assertFalse(is_advisor_block(block))

    def test_attribute_style_object_accepted(self) -> None:
        class FakeBlock:
            type = "advisor_tool_result"
            tool_use_id = "x"

        self.assertTrue(is_advisor_block(FakeBlock()))


class TestStripAdvisorBlocks(unittest.TestCase):
    """Parity with TS ``stripAdvisorBlocks``."""

    def test_returns_input_unchanged_when_no_advisor_blocks(self) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
        ]
        out = strip_advisor_blocks(msgs)
        # Identity return is part of the contract — keeps the caller's
        # ``list is messages`` checks valid.
        self.assertIs(out, msgs)

    def test_strips_advisor_blocks_from_assistant(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "before"},
                    {
                        "type": "server_tool_use",
                        "id": "sx",
                        "name": "advisor",
                        "input": {},
                    },
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "sx",
                        "content": {"type": "advisor_result", "text": "advice"},
                    },
                    {"type": "text", "text": "after"},
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        types = [b["type"] for b in out[0]["content"]]
        self.assertEqual(types, ["text", "text"])
        self.assertEqual([b["text"] for b in out[0]["content"]], ["before", "after"])

    def test_inserts_placeholder_when_content_becomes_empty(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "s", "name": "advisor", "input": {}},
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        self.assertEqual(out[0]["content"], [{"type": "text", "text": "[Advisor response]"}])

    def test_inserts_placeholder_when_only_thinking_remains(self) -> None:
        # TS treats thinking / redacted_thinking / blank-text as
        # "non-substantive" — the placeholder MUST still appear.
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {"type": "server_tool_use", "id": "s", "name": "advisor", "input": {}},
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        types = [b["type"] for b in out[0]["content"]]
        self.assertIn("text", types)
        self.assertTrue(
            any(b.get("text") == "[Advisor response]" for b in out[0]["content"])
        )

    def test_inserts_placeholder_when_only_blank_text_remains(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "   "},  # whitespace-only
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "s",
                        "content": {"type": "advisor_result", "text": "x"},
                    },
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        self.assertTrue(
            any(b.get("text") == "[Advisor response]" for b in out[0]["content"])
        )

    def test_no_placeholder_when_substantive_text_remains(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "real answer"},
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "s",
                        "content": {"type": "advisor_result", "text": "x"},
                    },
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        self.assertEqual(out[0]["content"], [{"type": "text", "text": "real answer"}])

    def test_user_messages_passthrough(self) -> None:
        # User messages are not assistant-shaped — advisor blocks never
        # land there in practice, but the stripper must still pass them
        # through untouched.
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "text", "text": "world"}]},
        ]
        out = strip_advisor_blocks(msgs)
        self.assertIs(out, msgs)

    def test_does_not_mutate_input(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "server_tool_use", "id": "s", "name": "advisor", "input": {}},
                ],
            }
        ]
        out = strip_advisor_blocks(msgs)
        # Input retained server_tool_use; output didn't.
        self.assertEqual(len(msgs[0]["content"]), 2)
        self.assertEqual(len(out[0]["content"]), 1)


class TestBuildAdvisorToolSchema(unittest.TestCase):
    def test_schema_shape(self) -> None:
        schema = build_advisor_tool_schema("claude-opus-4-6")
        self.assertEqual(
            schema,
            {
                "type": "advisor_20260301",
                "name": "advisor",
                "model": "claude-opus-4-6",
            },
        )

    def test_returns_dict_not_typeddict(self) -> None:
        # The result has to be a plain dict so the SDK passes it through
        # to the JSON body without Pydantic validating against the
        # known BetaToolUnionParam list (which excludes advisor_20260301
        # in 0.88.0).
        schema = build_advisor_tool_schema("claude-sonnet-4-6")
        self.assertIs(type(schema), dict)


if __name__ == "__main__":
    unittest.main()

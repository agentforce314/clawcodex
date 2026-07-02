"""ch16 round-4 — ANTHROPIC_CUSTOM_HEADERS injection (enterprise gateway/proxy).

Covers my-docs/port-improvement-round-4/ch16-remote-round4-plan.md. Mirrors TS
services/api/client.ts:530-549 (getCustomHeaders) and asserts the headers are
threaded into the live Anthropic client on the streaming path.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services.api.custom_headers import (
    get_anthropic_custom_headers,
    parse_custom_headers,
)


class TestParseCustomHeaders(unittest.TestCase):
    def test_basic_curl_style(self):
        self.assertEqual(
            parse_custom_headers("X-Gateway-Auth: tok123\nX-Org: acme"),
            {"X-Gateway-Auth": "tok123", "X-Org": "acme"},
        )

    def test_split_on_first_colon_only(self):
        # A value may itself contain a colon (e.g. a URL / time).
        self.assertEqual(
            parse_custom_headers("X-Trace: id:12:34"),
            {"X-Trace": "id:12:34"},
        )

    def test_trims_name_and_value(self):
        self.assertEqual(
            parse_custom_headers("  Name  :   spaced value  "),
            {"Name": "spaced value"},
        )

    def test_skips_blank_and_colonless_lines(self):
        self.assertEqual(
            parse_custom_headers("\n  \nNoColonHere\nA: 1\n"),
            {"A": "1"},
        )

    def test_crlf_supported(self):
        self.assertEqual(
            parse_custom_headers("A: 1\r\nB: 2"),
            {"A": "1", "B": "2"},
        )

    def test_empty_name_skipped(self):
        self.assertEqual(parse_custom_headers(": value"), {})

    def test_none_and_empty(self):
        self.assertEqual(parse_custom_headers(None), {})
        self.assertEqual(parse_custom_headers(""), {})

    def test_last_duplicate_wins(self):
        self.assertEqual(parse_custom_headers("A: 1\nA: 2"), {"A": "2"})


class TestEnvReader(unittest.TestCase):
    def test_reads_env(self):
        with patch.dict("os.environ", {"ANTHROPIC_CUSTOM_HEADERS": "X: y"}):
            self.assertEqual(get_anthropic_custom_headers(), {"X": "y"})

    def test_unset_is_empty(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ANTHROPIC_CUSTOM_HEADERS", None)
            self.assertEqual(get_anthropic_custom_headers(), {})


class TestProviderInjection(unittest.TestCase):
    def test_anthropic_provider_threads_headers(self):
        from src.providers.anthropic_provider import AnthropicProvider

        with patch.dict("os.environ", {"ANTHROPIC_CUSTOM_HEADERS": "X-GW: t"}):
            p = AnthropicProvider(api_key="k")
        self.assertEqual(p._client_kwargs.get("default_headers"), {"X-GW": "t"})

    def test_anthropic_provider_no_headers_when_unset(self):
        from src.providers.anthropic_provider import AnthropicProvider
        import os

        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ANTHROPIC_CUSTOM_HEADERS", None)
            p = AnthropicProvider(api_key="k")
        self.assertNotIn("default_headers", p._client_kwargs)

    def test_non_anthropic_provider_excluded(self):
        # critic: ANTHROPIC_CUSTOM_HEADERS must NOT leak onto a non-Anthropic
        # endpoint (Minimax uses the Anthropic SDK against its OWN base_url).
        from src.providers.minimax_provider import MinimaxProvider

        with patch.dict("os.environ", {"ANTHROPIC_CUSTOM_HEADERS": "X-GW: t"}):
            p = MinimaxProvider(api_key="k", base_url="https://minimax.example")
        self.assertNotIn("default_headers", p._client_kwargs)


class TestStreamingPathInjection(unittest.TestCase):
    def test_call_model_constructs_client_with_headers(self):
        # The live streaming path (call_model) constructs AsyncAnthropic with
        # the parsed default_headers. Stub the anthropic module (captures
        # kwargs, fails fast after construction) so there's no network.
        import asyncio
        import types

        captured = {}

        class _FakeAsyncAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __getattr__(self, _name):
                raise RuntimeError("stop-after-construct")

        fake = types.ModuleType("anthropic")
        fake.AsyncAnthropic = _FakeAsyncAnthropic

        from src.services.api.claude import CallModelOptions, call_model

        async def _drive():
            gen = call_model([{"role": "user", "content": "hi"}],
                             CallModelOptions())
            async for _ev in gen:
                pass  # runs to completion (construction then a fast error)

        with patch.dict("sys.modules", {"anthropic": fake}), \
                patch.dict("os.environ", {"ANTHROPIC_CUSTOM_HEADERS": "X-GW: z"}):
            asyncio.run(_drive())

        self.assertEqual(captured.get("default_headers"), {"X-GW": "z"})

    def test_call_model_no_headers_passes_none(self):
        import asyncio
        import os
        import types

        captured = {}

        class _FakeAsyncAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __getattr__(self, _name):
                raise RuntimeError("stop")

        fake = types.ModuleType("anthropic")
        fake.AsyncAnthropic = _FakeAsyncAnthropic

        from src.services.api.claude import CallModelOptions, call_model

        async def _drive():
            gen = call_model([{"role": "user", "content": "hi"}],
                             CallModelOptions())
            async for _ev in gen:
                pass

        with patch.dict("sys.modules", {"anthropic": fake}), \
                patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ANTHROPIC_CUSTOM_HEADERS", None)
            asyncio.run(_drive())

        # No custom headers → default_headers passed as None (SDK default).
        self.assertIsNone(captured.get("default_headers"))


if __name__ == "__main__":
    unittest.main()

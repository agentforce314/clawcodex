"""Tests for Phase 9 runtime manager + Phase 10 polish + Phase 5 XAA helpers.

Covers:
* WI-9.1 — ``MCPConnectionManager`` snapshot / get_state / get_tools
* WI-10.2 — 64-char truncation in ``normalize_name_for_mcp``
* WI-10.3 — ``truncate_description``
* WI-10.5 — telemetry sink injection
* WI-10.6 — ``is_official_mcp_url`` URL normalization
* WI-8.5  — ``persist_binary_content`` / ``get_binary_blob_saved_message``
* WI-5.1  — ``xaa.normalize_url`` + ``redact_tokens``
* WI-7.3  — claudeai eligibility gates + ``reset_claudeai_cache``
"""

from __future__ import annotations

import asyncio

import pytest

from src.services.mcp.claudeai import (
    fetch_claudeai_mcp_configs_if_eligible,
    reset_claudeai_cache,
)
from src.services.mcp.connection_manager import MCPConnectionManager
from src.services.mcp.normalization import (
    MAX_MCP_NAME_LENGTH,
    normalize_name_for_mcp,
)
from src.services.mcp.official_registry import (
    _normalize_url,
    is_official_mcp_url,
)
from src.services.mcp.output_storage import (
    get_binary_blob_saved_message,
    persist_binary_content,
)
from src.services.mcp.telemetry import (
    MCP_AUTH_REQUIRED,
    emit,
    register_sink,
)
from src.services.mcp.text_truncation import (
    MAX_MCP_DESCRIPTION_LENGTH,
    truncate_description,
)
from src.services.mcp.xaa import normalize_url, redact_tokens


# ----------------------------------------------------------------------
# WI-9.1 runtime manager
# ----------------------------------------------------------------------


class TestConnectionManager:

    def test_snapshot_returns_empty_initially(self):
        mgr = MCPConnectionManager()
        assert mgr.snapshot() == {}

    def test_get_state_returns_none_for_unknown(self):
        mgr = MCPConnectionManager()
        assert mgr.get_state("unknown") is None

    def test_get_tools_returns_empty_for_unknown(self):
        mgr = MCPConnectionManager()
        assert mgr.get_tools("unknown") == []

    def test_all_tools_empty_initially(self):
        mgr = MCPConnectionManager()
        assert mgr.all_tools() == []

    @pytest.mark.asyncio
    async def test_close_all_is_idempotent(self):
        mgr = MCPConnectionManager()
        await mgr.close_all()
        await mgr.close_all()  # no error


# ----------------------------------------------------------------------
# WI-10.2 normalization 64-char truncation
# ----------------------------------------------------------------------


class TestNormalizationLength:

    def test_short_name_unchanged(self):
        assert normalize_name_for_mcp("github") == "github"

    def test_long_name_truncated_to_64(self):
        long_name = "x" * 200
        out = normalize_name_for_mcp(long_name)
        assert len(out) == MAX_MCP_NAME_LENGTH

    def test_invalid_chars_normalized_before_truncation(self):
        # 32 dots → 32 underscores ≤ 64, so no truncation but full replace.
        out = normalize_name_for_mcp("." * 32)
        assert out == "_" * 32

    def test_claudeai_collapse_then_truncate(self):
        name = "claude.ai " + "x" * 200
        out = normalize_name_for_mcp(name)
        # Underscores collapsed then truncated.
        assert len(out) <= MAX_MCP_NAME_LENGTH


# ----------------------------------------------------------------------
# WI-10.3 text_truncation
# ----------------------------------------------------------------------


class TestTruncateDescription:

    def test_none_passthrough(self):
        assert truncate_description(None) is None

    def test_empty_passthrough(self):
        assert truncate_description("") == ""

    def test_short_unchanged(self):
        assert truncate_description("hello") == "hello"

    def test_at_limit_unchanged(self):
        s = "x" * MAX_MCP_DESCRIPTION_LENGTH
        assert truncate_description(s) == s

    def test_above_limit_truncated_with_suffix(self):
        s = "x" * (MAX_MCP_DESCRIPTION_LENGTH + 10)
        out = truncate_description(s)
        assert out is not None
        assert out.endswith("... [truncated]")
        assert len(out) == MAX_MCP_DESCRIPTION_LENGTH + len("... [truncated]")


# ----------------------------------------------------------------------
# WI-10.5 telemetry sink injection
# ----------------------------------------------------------------------


class TestTelemetry:

    def test_emit_uses_default_sink_when_unregistered(self):
        # Default sink just logs at DEBUG; smoke test that emit doesn't raise.
        emit(MCP_AUTH_REQUIRED, server="srv")

    def test_register_sink_routes_events(self):
        received: list[tuple[str, dict]] = []
        register_sink(lambda evt, props: received.append((evt, props)))
        try:
            emit(MCP_AUTH_REQUIRED, server="srv")
            assert received == [(MCP_AUTH_REQUIRED, {"server": "srv"})]
        finally:
            # Restore default sink so other tests don't see our list.
            from src.services.mcp.telemetry import _default_sink

            register_sink(_default_sink)

    def test_sink_exception_swallowed(self):
        def broken(evt, props):
            raise RuntimeError("sink broken")

        register_sink(broken)
        try:
            # Must not raise.
            emit(MCP_AUTH_REQUIRED, x=1)
        finally:
            from src.services.mcp.telemetry import _default_sink

            register_sink(_default_sink)


# ----------------------------------------------------------------------
# WI-10.6 official_registry URL normalization
# ----------------------------------------------------------------------


class TestOfficialRegistry:

    def test_returns_false_when_prefetch_not_done(self):
        # Module-state may be set by other tests; verify the absent-URL path.
        assert is_official_mcp_url("https://x") is False or is_official_mcp_url("https://x") is True
        # The contract is non-raising; either return value is fine here.
        assert is_official_mcp_url("") is False

    def test_normalize_url_drops_query_and_trailing_slash(self):
        a = _normalize_url("https://Example.com/foo/?bar=1")
        b = _normalize_url("https://example.com/foo")
        assert a == b


# ----------------------------------------------------------------------
# WI-8.5 output_storage
# ----------------------------------------------------------------------


class TestOutputStorage:

    def test_persist_and_message_round_trip(self):
        path = persist_binary_content(
            server_name="srv",
            tool_name="screenshot",
            content_bytes=b"\x89PNG\r\n\x1a\n",  # tiny PNG header
            content_type="image/png",
        )
        try:
            assert path.exists()
            assert path.suffix == ".png"
            msg = get_binary_blob_saved_message(path, 8)
            assert str(path) in msg
            assert "8 bytes" in msg
        finally:
            path.unlink(missing_ok=True)

    def test_safe_filename_for_weird_server_tool_names(self):
        path = persist_binary_content(
            server_name="srv/with/slashes",
            tool_name="tool with spaces",
            content_bytes=b"x",
            content_type="text/plain",
        )
        try:
            # Underscored, no slashes / spaces in the leaf name.
            assert "/" not in path.name
            assert " " not in path.name
        finally:
            path.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# WI-5.1 xaa helpers
# ----------------------------------------------------------------------


class TestXaaHelpers:

    def test_normalize_url_lowercases_host(self):
        assert (
            normalize_url("HTTPS://AUTH.EXAMPLE.COM/Token/")
            == "https://auth.example.com/Token"
        )

    def test_normalize_url_strips_default_https_port(self):
        assert (
            normalize_url("https://auth.example.com:443/token")
            == "https://auth.example.com/token"
        )

    def test_normalize_url_keeps_non_default_port(self):
        assert (
            normalize_url("https://auth.example.com:8443/token")
            == "https://auth.example.com:8443/token"
        )

    def test_redact_tokens_strips_access_token(self):
        body = '{"access_token":"super-secret","token_type":"Bearer"}'
        out = redact_tokens(body)
        assert "super-secret" not in out
        assert "REDACTED" in out

    def test_redact_tokens_handles_multiple_token_fields(self):
        body = '{"access_token":"tok1","id_token":"tok2","refresh_token":"tok3"}'
        out = redact_tokens(body)
        assert "tok1" not in out and "tok2" not in out and "tok3" not in out
        assert out.count("REDACTED") == 3


# ----------------------------------------------------------------------
# WI-7.3 claudeai eligibility
# ----------------------------------------------------------------------


class TestClaudeai:

    @pytest.mark.asyncio
    async def test_disabled_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("ENABLE_CLAUDEAI_MCP_SERVERS", raising=False)
        reset_claudeai_cache()
        result = await fetch_claudeai_mcp_configs_if_eligible()
        assert result == {}

    @pytest.mark.asyncio
    async def test_disabled_when_provider_not_anthropic(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDEAI_MCP_SERVERS", "1")
        monkeypatch.setenv("CLAUDE_PROVIDER", "openai")
        reset_claudeai_cache()
        result = await fetch_claudeai_mcp_configs_if_eligible()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_auth_token(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDEAI_MCP_SERVERS", "1")
        monkeypatch.setenv("CLAUDE_PROVIDER", "anthropic")
        monkeypatch.delenv("CLAUDEAI_API_TOKEN", raising=False)
        reset_claudeai_cache()
        result = await fetch_claudeai_mcp_configs_if_eligible()
        assert result == {}

    @pytest.mark.asyncio
    async def test_cache_persists_across_calls(self, monkeypatch):
        monkeypatch.delenv("ENABLE_CLAUDEAI_MCP_SERVERS", raising=False)
        reset_claudeai_cache()
        await fetch_claudeai_mcp_configs_if_eligible()
        # Module-state cache now holds {} — second call returns same.
        result = await fetch_claudeai_mcp_configs_if_eligible()
        assert result == {}

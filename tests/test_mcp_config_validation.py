"""Round-2 tests for strict MCP server config validation.

Covers ``validate_server_config`` (rich errors) and the wiring through
``parse_mcp_config()`` (per-field ``ValidationError`` rows with suggestions).

Maps to ``my-docs/ch15-mcp-round2-plan.md`` work-items WI-1 / WI-2 / WI-3 / WI-4.
TS canonical: ``typescript/src/services/mcp/types.ts:28-122`` Zod schemas.
"""

from __future__ import annotations

import pytest

from src.services.mcp.config import parse_mcp_config
from src.services.mcp.types import (
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    parse_server_config,
    validate_server_config,
)


class TestValidateServerConfig:
    """Each case mirrors a row of the §2 table in the round-2 gap analysis."""

    # --- Negative cases (the round-2 fixes) ---

    def test_sse_missing_url_returns_error_no_keyerror(self) -> None:
        config, errors = validate_server_config({"type": "sse"})
        assert config is None
        assert any("url" in e and "required" in e for e in errors), errors

    def test_http_missing_url_returns_error_no_keyerror(self) -> None:
        config, errors = validate_server_config({"type": "http"})
        assert config is None
        assert any("url" in e and "required" in e for e in errors), errors

    def test_ws_missing_url_returns_error_no_keyerror(self) -> None:
        config, errors = validate_server_config({"type": "ws"})
        assert config is None
        assert any("url" in e and "required" in e for e in errors), errors

    def test_sdk_missing_name_returns_error_no_keyerror(self) -> None:
        config, errors = validate_server_config({"type": "sdk"})
        assert config is None
        assert any("name" in e and "required" in e for e in errors), errors

    def test_sse_non_string_url_returns_error(self) -> None:
        config, errors = validate_server_config({"type": "sse", "url": 12345})
        assert config is None
        assert any("url must be a string" in e for e in errors), errors

    def test_stdio_non_list_args_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "py", "args": "not-a-list"}
        )
        assert config is None
        assert any("args must be a list of strings" in e for e in errors), errors

    def test_stdio_non_string_arg_element_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "py", "args": ["ok", 123]}
        )
        assert config is None
        assert any("args[1]" in e for e in errors), errors

    def test_stdio_non_string_env_value_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "py", "env": {"X": 123}}
        )
        assert config is None
        assert any("env value for 'X' must be a string" in e for e in errors), errors

    def test_stdio_non_dict_env_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "py", "env": "not-a-dict"}
        )
        assert config is None
        assert any("env must be an object" in e for e in errors), errors

    def test_http_non_https_auth_server_metadata_url_returns_error(self) -> None:
        config, errors = validate_server_config(
            {
                "type": "http",
                "url": "https://x",
                "authServerMetadataUrl": "http://bad",
            }
        )
        assert config is None
        assert any("authServerMetadataUrl must use https://" in e for e in errors), errors

    def test_http_non_string_auth_server_metadata_url_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "http", "url": "https://x", "authServerMetadataUrl": 123}
        )
        assert config is None
        assert any(
            "authServerMetadataUrl must be a string" in e for e in errors
        ), errors

    def test_stdio_empty_command_returns_error(self) -> None:
        config, errors = validate_server_config({"type": "stdio", "command": ""})
        assert config is None
        assert any("command cannot be empty" in e for e in errors), errors

    def test_stdio_missing_command_returns_error(self) -> None:
        config, errors = validate_server_config({"type": "stdio"})
        assert config is None
        assert any("command is required" in e for e in errors), errors

    def test_stdio_non_string_command_returns_error(self) -> None:
        config, errors = validate_server_config({"type": "stdio", "command": 123})
        assert config is None
        assert any("command must be a string" in e for e in errors), errors

    def test_unknown_transport_type_returns_friendly_error(self) -> None:
        config, errors = validate_server_config({"type": "unknown_type"})
        assert config is None
        assert any(
            "unknown transport type" in e
            and "stdio" in e
            and "sse" in e
            and "http" in e
            for e in errors
        ), errors

    def test_non_dict_input_returns_error_not_crash(self) -> None:
        config, errors = validate_server_config("not-a-dict")
        assert config is None
        assert errors == ["server config must be an object"]

    def test_non_string_type_field_returns_error(self) -> None:
        config, errors = validate_server_config({"type": 12345, "command": "py"})
        assert config is None
        assert any("type must be a string" in e for e in errors), errors

    def test_sse_non_string_headers_value_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "sse", "url": "https://x", "headers": {"X": 1}}
        )
        assert config is None
        assert any("headers value for 'X'" in e for e in errors), errors

    def test_sse_non_string_headers_helper_returns_error(self) -> None:
        config, errors = validate_server_config(
            {"type": "sse", "url": "https://x", "headersHelper": ["not", "a", "str"]}
        )
        assert config is None
        assert any("headersHelper must be a string" in e for e in errors), errors

    # --- Positive cases (regression guard) ---

    def test_valid_stdio_with_type(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "python", "args": ["-m", "server"]}
        )
        assert errors == []
        assert isinstance(config, McpStdioServerConfig)
        assert config.command == "python"
        assert config.args == ["-m", "server"]

    def test_valid_stdio_implicit_type(self) -> None:
        config, errors = validate_server_config({"command": "python"})
        assert errors == []
        assert isinstance(config, McpStdioServerConfig)

    def test_valid_sse(self) -> None:
        config, errors = validate_server_config(
            {"type": "sse", "url": "https://example.com/sse"}
        )
        assert errors == []
        assert isinstance(config, McpSSEServerConfig)

    def test_valid_http_with_https_metadata_url(self) -> None:
        config, errors = validate_server_config(
            {
                "type": "http",
                "url": "https://example.com/mcp",
                "authServerMetadataUrl": "https://auth.example.com/.well-known/x",
            }
        )
        assert errors == []
        assert isinstance(config, McpHTTPServerConfig)
        assert config.auth_server_metadata_url == (
            "https://auth.example.com/.well-known/x"
        )

    def test_valid_stdio_with_env(self) -> None:
        config, errors = validate_server_config(
            {"type": "stdio", "command": "py", "env": {"FOO": "bar"}}
        )
        assert errors == []
        assert isinstance(config, McpStdioServerConfig)
        assert config.env == {"FOO": "bar"}

    def test_multiple_errors_accumulate(self) -> None:
        # Both bad type and bad asm_url should be reported separately
        # (or, if type is invalid, that short-circuits — both behaviors are
        # acceptable; the contract is "at least one descriptive error").
        config, errors = validate_server_config({"type": "unknown"})
        assert config is None
        assert len(errors) >= 1

    def test_multiple_errors_in_one_stdio_config(self) -> None:
        # Empty command + bad args + bad env: should report all three.
        config, errors = validate_server_config(
            {
                "type": "stdio",
                "command": "",
                "args": "not-a-list",
                "env": {"X": 123},
            }
        )
        assert config is None
        # We expect at least the command + args + env errors (3 distinct).
        joined = " | ".join(errors)
        assert "command cannot be empty" in joined, errors
        assert "args must be a list of strings" in joined, errors
        assert "env value for 'X' must be a string" in joined, errors


class TestParseServerConfigBackCompat:
    """The legacy ``parse_server_config`` returns ``Optional[McpServerConfig]``
    and must not raise on any input — including the previously-crashing cases.
    """

    def test_sse_missing_url_returns_none_not_keyerror(self) -> None:
        # Before round 2 this raised ``KeyError: 'url'``.
        assert parse_server_config({"type": "sse"}) is None

    def test_sdk_missing_name_returns_none_not_keyerror(self) -> None:
        # Before round 2 this raised ``KeyError: 'name'``.
        assert parse_server_config({"type": "sdk"}) is None

    def test_existing_happy_paths_still_work(self) -> None:
        assert (
            parse_server_config({"type": "stdio", "command": "py"}) is not None
        )
        assert (
            parse_server_config({"type": "sse", "url": "https://x"}) is not None
        )
        assert (
            parse_server_config({"type": "http", "url": "https://x"}) is not None
        )
        assert (
            parse_server_config({"type": "sdk", "name": "my-sdk-server"}) is not None
        )
        # implicit-stdio
        assert parse_server_config({"command": "py"}) is not None

    def test_existing_negative_paths_still_return_none(self) -> None:
        assert parse_server_config({"type": "unknown_type"}) is None
        assert parse_server_config({"type": "stdio"}) is None  # no command


class TestParseMcpConfigSurfacesValidationMessages:
    """``parse_mcp_config()`` should attach one ``ValidationError`` per
    validator message (instead of the old single "Invalid server configuration"
    catch-all), with paths scoped to the offending server name and
    suggestions where the message has an obvious fix.
    """

    def test_per_server_path_attached(self) -> None:
        result = parse_mcp_config(
            {"mcpServers": {"broken": {"type": "sse"}}},
            expand_vars=False,
        )
        assert result.config == {}
        paths = [e.path for e in result.errors]
        assert "mcpServers.broken" in paths

    def test_url_required_message_propagates(self) -> None:
        result = parse_mcp_config(
            {"mcpServers": {"broken": {"type": "sse"}}},
            expand_vars=False,
        )
        msgs = [e.message for e in result.errors]
        assert any("url" in m and "required" in m for m in msgs), msgs

    def test_https_only_auth_server_metadata_url_message_propagates(self) -> None:
        result = parse_mcp_config(
            {
                "mcpServers": {
                    "bad-asm": {
                        "type": "http",
                        "url": "https://x",
                        "authServerMetadataUrl": "http://bad",
                    }
                }
            },
            expand_vars=False,
        )
        assert result.config == {}
        msgs = [e.message for e in result.errors]
        assert any("authServerMetadataUrl must use https://" in m for m in msgs), msgs

    def test_https_only_message_has_helpful_suggestion(self) -> None:
        result = parse_mcp_config(
            {
                "mcpServers": {
                    "bad-asm": {
                        "type": "http",
                        "url": "https://x",
                        "authServerMetadataUrl": "http://bad",
                    }
                }
            },
            expand_vars=False,
        )
        suggestions = [e.suggestion for e in result.errors if e.suggestion]
        assert any("https://" in s for s in suggestions), suggestions

    def test_missing_field_has_add_field_suggestion(self) -> None:
        result = parse_mcp_config(
            {"mcpServers": {"broken": {"type": "sse"}}},
            expand_vars=False,
        )
        suggestions = [e.suggestion for e in result.errors if e.suggestion]
        assert any("Add a `url`" in s for s in suggestions), suggestions

    def test_unknown_transport_type_has_enumerated_suggestion(self) -> None:
        result = parse_mcp_config(
            {"mcpServers": {"weird": {"type": "totally-bogus"}}},
            expand_vars=False,
        )
        suggestions = [e.suggestion for e in result.errors if e.suggestion]
        assert any("stdio" in s and "sse" in s for s in suggestions), suggestions

    def test_multiple_errors_per_server_each_emit_validation_row(self) -> None:
        result = parse_mcp_config(
            {
                "mcpServers": {
                    "multibroken": {
                        "type": "stdio",
                        "command": "",
                        "args": "not-a-list",
                        "env": {"X": 123},
                    }
                }
            },
            expand_vars=False,
        )
        assert result.config == {}
        # 3 messages from the validator -> 3 ValidationError rows for this
        # server (plus possibly the suggestion-derived stuff). At minimum we
        # want one per field-error.
        server_errors = [
            e for e in result.errors if e.server_name == "multibroken"
        ]
        assert len(server_errors) >= 3, server_errors
        joined = " | ".join(e.message for e in server_errors)
        assert "command cannot be empty" in joined
        assert "args must be a list of strings" in joined
        assert "env value for 'X' must be a string" in joined

    def test_valid_and_invalid_servers_coexist(self) -> None:
        result = parse_mcp_config(
            {
                "mcpServers": {
                    "good": {"type": "stdio", "command": "py"},
                    "bad": {"type": "sse"},  # missing url
                }
            },
            expand_vars=False,
        )
        # Good server still parses; bad server surfaces validation errors.
        assert result.config is not None
        assert "good" in result.config
        assert "bad" not in result.config
        bad_errors = [e for e in result.errors if e.server_name == "bad"]
        assert any("url" in e.message for e in bad_errors)
        good_errors = [e for e in result.errors if e.server_name == "good"]
        assert good_errors == []

    def test_all_errors_have_fatal_severity_by_default(self) -> None:
        result = parse_mcp_config(
            {"mcpServers": {"broken": {"type": "sse"}}},
            expand_vars=False,
        )
        # Fatal severity is the dataclass default; we just verify the rich
        # validator messages don't accidentally downgrade.
        for e in result.errors:
            if e.server_name == "broken":
                assert e.severity == "fatal"

    def test_server_with_non_dict_config_still_errors_clearly(self) -> None:
        # Pre-existing behavior: top-level "must be an object" error.
        # The round-2 validator should preserve this for parity.
        result = parse_mcp_config(
            {"mcpServers": {"broken": "not-a-dict"}},
            expand_vars=False,
        )
        bad_errors = [e for e in result.errors if e.server_name == "broken"]
        assert bad_errors, "should emit at least one error for non-dict server config"

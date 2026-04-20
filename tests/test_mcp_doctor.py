import pytest
from unittest.mock import patch, MagicMock

from src.services.mcp.doctor import (
    DiagnosticReport,
    ServerDiagnostic,
    _validate_stdio_config,
    _validate_url_config,
    run_diagnostics,
)
from src.services.mcp.types import (
    McpStdioServerConfig,
    McpSSEServerConfig,
)


class TestServerDiagnostic:
    def test_healthy(self):
        diag = ServerDiagnostic(
            name="test",
            scope="project",
            transport_type="stdio",
            status="healthy",
        )
        assert diag.is_healthy is True

    def test_unhealthy(self):
        diag = ServerDiagnostic(
            name="test",
            scope="project",
            transport_type="stdio",
            status="failed",
            error="Connection refused",
        )
        assert diag.is_healthy is False


class TestDiagnosticReport:
    def test_empty_report(self):
        report = DiagnosticReport()
        assert report.total_count == 0
        assert report.healthy_count == 0
        assert report.unhealthy_count == 0

    def test_mixed_report(self):
        report = DiagnosticReport(servers=[
            ServerDiagnostic(name="s1", scope="user", transport_type="stdio", status="healthy"),
            ServerDiagnostic(name="s2", scope="user", transport_type="http", status="failed"),
            ServerDiagnostic(name="s3", scope="project", transport_type="sse", status="healthy"),
        ])
        assert report.total_count == 3
        assert report.healthy_count == 2
        assert report.unhealthy_count == 1

    def test_format_report_empty(self):
        report = DiagnosticReport()
        text = report.format_report()
        assert "No MCP servers configured" in text

    def test_format_report_with_servers(self):
        report = DiagnosticReport(servers=[
            ServerDiagnostic(
                name="my-server",
                scope="project",
                transport_type="stdio",
                status="healthy",
                latency_ms=150,
                capabilities={"tools": True, "prompts": False},
            ),
        ])
        text = report.format_report()
        assert "my-server" in text
        assert "healthy" in text.lower() or "✓" in text

    def test_format_report_with_errors(self):
        report = DiagnosticReport(
            config_errors=["Missing env var FOO"],
            servers=[
                ServerDiagnostic(
                    name="broken",
                    scope="user",
                    transport_type="http",
                    status="failed",
                    error="Connection refused",
                ),
            ],
        )
        text = report.format_report()
        assert "Missing env var FOO" in text
        assert "broken" in text


class TestValidateStdioConfig:
    def test_valid_command(self):
        config = McpStdioServerConfig(command="python")
        warnings = _validate_stdio_config("test", config)
        assert warnings == []

    def test_empty_command(self):
        config = McpStdioServerConfig(command="")
        warnings = _validate_stdio_config("test", config)
        assert len(warnings) >= 1

    def test_missing_command(self):
        config = McpStdioServerConfig(command="nonexistent_binary_xyz123")
        warnings = _validate_stdio_config("test", config)
        assert len(warnings) >= 1

    def test_unexpanded_env(self):
        config = McpStdioServerConfig(
            command="node",
            env={"API_KEY": "${env:MY_KEY}"},
        )
        warnings = _validate_stdio_config("test", config)
        assert any("Unexpanded" in w for w in warnings)


class TestValidateUrlConfig:
    def test_valid_url(self):
        warnings = _validate_url_config("test", "https://example.com/api")
        assert warnings == []

    def test_empty_url(self):
        warnings = _validate_url_config("test", "")
        assert len(warnings) >= 1

    def test_invalid_scheme(self):
        warnings = _validate_url_config("test", "ftp://example.com")
        assert len(warnings) >= 1

    def test_unexpanded_env(self):
        warnings = _validate_url_config("test", "https://${env:HOST}/api")
        assert any("Unexpanded" in w for w in warnings)


class TestRunDiagnostics:
    @pytest.mark.asyncio
    async def test_skip_connection_test(self):
        with patch("src.services.mcp.doctor.get_all_mcp_configs", return_value=({}, [])):
            report = await run_diagnostics(skip_connection_test=True)
            assert isinstance(report, DiagnosticReport)
            assert report.total_count == 0

    @pytest.mark.asyncio
    async def test_no_configs(self):
        with patch("src.services.mcp.doctor.get_all_mcp_configs", return_value=({}, [])):
            report = await run_diagnostics()
            assert report.total_count == 0

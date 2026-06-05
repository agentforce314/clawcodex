"""Layer isolation integration tests.

These tests verify that:
1. upstream-sync audit passes (zero layer violations)
2. Capability Protocol contracts are structurally sound
3. Patch series is registered and metadata is valid

See: docs/UPSTREAM_SYNC_DESIGN-decoupling.md Section 4.2
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]

# Current upstream version tag (matches upstream-sync.yaml version_tag_format)
UPSTREAM_VERSION = "b125e16"  # git rev-parse --short upstream/vendor


class TestLayerIsolationAudit:
    """Verify upstream-sync audit passes with current layer configuration."""

    def test_audit_passes(self):
        """Run upstream-sync audit via CliRunner (avoids subprocess import issues).

        The subprocess approach triggers a circular-import bug in the editable
        install's .pth file (src/types/__init__.py shadows stdlib types).
        Using CliRunner directly avoids the subprocess/import problem.
        """
        import importlib.util
        spec = importlib.util.find_spec("upstream_sync")
        if spec is None:
            pytest.skip("upstream_sync module not importable in this environment")

        sys.path.insert(0, str(REPO_ROOT / "src"))
        from upstream_sync.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(app, ["audit", "--config", "upstream-sync.yaml"])
        assert result.exit_code == 0, f"Audit failed:\n{result.stdout}\n{result.stderr}"  # noqa: S301
        assert "No layer violations" in result.stdout


class TestCapabilityProtocols:
    """Verify capability Protocol contracts are structurally defined."""

    def test_agent_protocol_methods_exist(self):
        """AgentLoopProtocol has the required method signatures."""
        from extensions.capabilities.agent_protocol import AgentLoopProtocol
        assert hasattr(AgentLoopProtocol, 'run_agent_loop')
        assert hasattr(AgentLoopProtocol, 'summarize_tool_result')
        assert hasattr(AgentLoopProtocol, 'summarize_tool_use')
        assert hasattr(AgentLoopProtocol, 'is_anthropic_provider')

    def test_tool_protocol_methods_exist(self):
        """ToolSystemProtocol has the required method signatures."""
        from extensions.capabilities.tool_protocol import ToolSystemProtocol
        assert hasattr(ToolSystemProtocol, 'get_tools')
        assert hasattr(ToolSystemProtocol, 'find_tool_by_name')
        assert hasattr(ToolSystemProtocol, 'build_tool')
        assert hasattr(ToolSystemProtocol, 'assemble_tool_pool')
        assert hasattr(ToolSystemProtocol, 'dispatch')

    def test_context_protocol_has_build_context_prompt(self):
        """ContextBuilderProtocol has build_context_prompt method."""
        from extensions.capabilities.context_protocol import ContextBuilderProtocol
        assert hasattr(ContextBuilderProtocol, 'build_context_prompt')

    def test_provider_protocol_methods_exist(self):
        """LLMProviderProtocol has the required method signatures."""
        from extensions.capabilities.provider_protocol import LLMProviderProtocol
        assert hasattr(LLMProviderProtocol, 'chat')
        assert hasattr(LLMProviderProtocol, 'chat_stream')

    def test_event_protocol_methods_exist(self):
        """ToolEventProtocol has the required property signatures."""
        from extensions.capabilities.event_protocol import ToolEventProtocol
        assert hasattr(ToolEventProtocol, 'kind')
        assert hasattr(ToolEventProtocol, 'tool_name')
        assert hasattr(ToolEventProtocol, 'tool_input')
        assert hasattr(ToolEventProtocol, 'tool_output')
        assert hasattr(ToolEventProtocol, 'tool_use_id')
        assert hasattr(ToolEventProtocol, 'is_error')
        assert hasattr(ToolEventProtocol, 'error')

    def test_headless_protocol_methods_exist(self):
        """HeadlessOptionsProtocol and HeadlessRunnerProtocol have required signatures."""
        from extensions.capabilities.headless_protocol import HeadlessOptionsProtocol, HeadlessRunnerProtocol
        assert hasattr(HeadlessOptionsProtocol, 'prompt')
        assert hasattr(HeadlessOptionsProtocol, 'output_format')
        assert hasattr(HeadlessOptionsProtocol, 'max_turns')
        assert hasattr(HeadlessOptionsProtocol, 'workspace_root')
        assert hasattr(HeadlessRunnerProtocol, '__call__')

    def test_headless_runner_stub_backend(self):
        """HeadlessSessionOptions and stub backend work without upstream imports."""
        import os
        # Use stub backend — no upstream import possible
        os.environ["CLAW_HEADLESS_BACKEND"] = "stub"
        try:
            from extensions.capabilities.headless_runner import HeadlessSessionOptions, run_headless_session
            from pathlib import Path
            import io
            stdout = io.StringIO()
            opts = HeadlessSessionOptions(
                prompt="test",
                workspace_root=Path.cwd(),
                stdout=stdout,
                stderr=stdout,
            )
            exit_code = run_headless_session(opts)
            assert exit_code == 0
        finally:
            os.environ.pop("CLAW_HEADLESS_BACKEND", None)


class TestPatchSeriesIntegrity:
    """Verify patch series is registered and metadata is valid (per upstream version)."""

    @property
    def version(self) -> str:
        """Current upstream vendor commit hash (short)."""
        return UPSTREAM_VERSION

    def test_series_has_entries(self):
        """Current version's series file should have at least one entry."""
        series_file = REPO_ROOT / "patches" / "upstream" / self.version / f"{self.version}_series"
        series = series_file.read_text()
        lines = [l.strip() for l in series.splitlines()
                 if l.strip() and not l.startswith("#")]
        assert len(lines) > 0, f"{series_file.name} is empty"

    def test_patch_file_exists(self):
        """The patch file referenced in series should exist."""
        series_dir = REPO_ROOT / "patches" / "upstream" / self.version
        series_file = series_dir / f"{self.version}_series"
        series = series_file.read_text()
        lines = [l.strip() for l in series.splitlines()
                 if l.strip() and not l.startswith("#")]
        for patch_name in lines:
            patch_file = series_dir / patch_name
            assert patch_file.exists(), f"Patch file not found: {patch_file}"

    def test_metadata_status_valid(self):
        """Patch metadata should have valid status field."""
        import json
        meta_file = REPO_ROOT / "patches" / "metadata" / "upstream" / f"{self.version}_0001-port-to-python.json"
        meta = json.loads(meta_file.read_text())
        assert meta["status"] in ("intent", "applied", "pending")
        assert "upstream_version_introduced" in meta
        assert "affected_modules" in meta
        assert len(meta["affected_modules"]) > 0

    def test_patch_has_content(self):
        """The baseline patch file should have substantial content."""
        patch_file = REPO_ROOT / "patches" / "upstream" / f"{self.version}_0001-port-to-python.patch"
        content = patch_file.read_text()
        lines = [l for l in content.splitlines() if l.startswith("diff --git")]
        assert len(lines) > 10, f"Patch seems empty or minimal: {len(lines)} diff hunks"
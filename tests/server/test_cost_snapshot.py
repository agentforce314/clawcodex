"""Cost display plumbing: the agent-server ``cost`` snapshot and the
patch line-count accounting behind /cost's "Total code changes".

Covers:
* ``_cost_snapshot()`` mirrors the bootstrap accumulators (the inputs of
  the original's formatTotalCost, cost-tracker.ts:249).
* ``_result_message`` carries the snapshot rider the TUI's exit summary
  prints (the original's useCostSummary, costHook.ts:12).
* ``record_patch_line_totals`` counts +/- hunk lines with the new-file
  special case (TS utils/diff.ts:50-69) and never raises.
"""

from __future__ import annotations

import pytest

from src.bootstrap.state import (
    ModelUsage,
    add_to_total_duration_state,
    add_to_total_lines_changed,
    get_total_lines_added,
    get_total_lines_removed,
    reset_state_for_tests,
)
from src.cost_tracker import record_api_usage
from src.server.agent_server import _cost_snapshot, _result_message
from src.tool_system.diff_utils import record_patch_line_totals


@pytest.fixture(autouse=True)
def _reset_bootstrap():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


class TestCostSnapshot:
    def test_empty_session_snapshot_zeros(self) -> None:
        snap = _cost_snapshot()
        assert snap["total_cost_usd"] == 0.0
        assert snap["total_api_duration_ms"] == 0
        assert snap["total_duration_ms"] >= 0
        assert snap["total_lines_added"] == 0
        assert snap["total_lines_removed"] == 0
        assert snap["has_unknown_model_cost"] is False
        assert snap["model_usage"] == {}

    def test_snapshot_reflects_accumulators(self) -> None:
        record_api_usage(
            "deepseek-chat",
            {
                "input_tokens": 1000,
                "output_tokens": 2000,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 100,
            },
        )
        add_to_total_duration_state(1234, 1200)
        add_to_total_lines_changed(10, 2)

        snap = _cost_snapshot()
        assert snap["total_api_duration_ms"] == 1234
        assert snap["total_lines_added"] == 10
        assert snap["total_lines_removed"] == 2
        mu = snap["model_usage"]["deepseek-chat"]
        assert mu["input_tokens"] == 1000
        assert mu["output_tokens"] == 2000
        assert mu["cache_read_input_tokens"] == 500
        assert mu["cache_creation_input_tokens"] == 100
        assert snap["total_cost_usd"] == pytest.approx(mu["cost_usd"])

    def test_result_message_carries_cost_rider(self) -> None:
        add_to_total_lines_changed(3, 1)
        msg = _result_message(
            "sid", subtype="success", num_turns=1, result="ok", is_error=False,
        )
        assert msg["cost"]["total_lines_added"] == 3
        assert msg["cost"]["total_lines_removed"] == 1


class TestRecordPatchLineTotals:
    def test_counts_hunk_markers(self) -> None:
        hunks = [
            {"lines": [" ctx", "+new one", "+new two", "-old"]},
            {"lines": ["+more", " ctx"]},
        ]
        record_patch_line_totals(hunks)
        assert get_total_lines_added() == 3
        assert get_total_lines_removed() == 1

    def test_new_file_counts_every_line_as_addition(self) -> None:
        # TS: newFileContent.split(/\r?\n/).length — the trailing-newline
        # empty segment IS counted (3 segments here).
        record_patch_line_totals([], "line1\nline2\n")
        assert get_total_lines_added() == 3
        assert get_total_lines_removed() == 0

    def test_noop_patch_records_nothing(self) -> None:
        record_patch_line_totals([])
        record_patch_line_totals([{"lines": [" ctx only"]}])
        assert get_total_lines_added() == 0
        assert get_total_lines_removed() == 0

    def test_best_effort_never_raises(self) -> None:
        record_patch_line_totals([{"lines": None}])  # type: ignore[list-item]
        record_patch_line_totals(None)  # type: ignore[arg-type]


class TestToolCallSiteLineCounts:
    """Pin the ORIGINAL's per-tool create semantics at the call sites —
    Write-create uses the split special case (trailing newline counts an
    extra empty segment, FileWriteTool.ts:408), while Edit-create counts
    the real ''→content patch (FileEditTool.ts:534): one line fewer for
    the same trailing-newline content."""

    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path):
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry

        self.root = tmp_path
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=tmp_path)
        yield

    def _dispatch(self, name: str, **input_data) -> None:
        from src.tool_system.protocol import ToolCall

        result = self.registry.dispatch(ToolCall(name=name, input=input_data), self.ctx)
        assert not result.is_error, getattr(result, "output", result)

    def test_write_create_counts_trailing_newline_segment(self) -> None:
        self._dispatch("Write", file_path=str(self.root / "new.txt"), content="a\nb\n")
        assert get_total_lines_added() == 3
        assert get_total_lines_removed() == 0

    def test_edit_create_counts_real_patch_lines(self) -> None:
        self._dispatch(
            "Edit", file_path=str(self.root / "new.txt"), old_string="", new_string="a\nb\n"
        )
        assert get_total_lines_added() == 2
        assert get_total_lines_removed() == 0

    def test_write_update_counts_hunk_lines(self) -> None:
        target = self.root / "f.txt"
        self._dispatch("Write", file_path=str(target), content="a\nb\n")
        reset_state_for_tests()
        self._dispatch("Write", file_path=str(target), content="a\nc\n")
        assert get_total_lines_added() == 1
        assert get_total_lines_removed() == 1

    def test_edit_update_counts_hunk_lines(self) -> None:
        target = self.root / "f.txt"
        self._dispatch("Write", file_path=str(target), content="a\nb\n")
        reset_state_for_tests()
        self._dispatch(
            "Edit", file_path=str(target), old_string="b", new_string="c\nd"
        )
        assert get_total_lines_added() == 2
        assert get_total_lines_removed() == 1

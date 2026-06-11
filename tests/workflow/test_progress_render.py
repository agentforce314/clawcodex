"""Rich /workflows monitor rendering — icons, agent type, tokens, tools,
duration, and phase progress (mirrors the Claude Code workflow view)."""

from __future__ import annotations

from types import SimpleNamespace

from src.workflow.progress import (
    WorkflowProgress,
    format_duration,
    format_tokens,
    render_run_lines,
)


def test_format_tokens():
    assert format_tokens(950) == "950"
    assert format_tokens(79600) == "79.6k"
    assert format_tokens(0) == "0"
    assert format_tokens(None) == "0"


def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(45) == "45s"
    assert format_duration(75) == "1m 15s"


def test_agent_record_carries_metadata():
    prog = WorkflowProgress([{"title": "S"}])
    prog.start_phase("S")
    rec = prog.agent_started(0, "finder", "S", "0", agent_type="researcher")
    assert rec.agent_type == "researcher"
    assert rec.started_at is not None
    assert rec.icon == "●"  # running
    prog.agent_finished(rec, status="completed", tokens=1200, tool_count=5)
    assert rec.tool_count == 5
    assert rec.elapsed is not None
    assert rec.icon == "✔"


def test_phase_done_count():
    prog = WorkflowProgress([{"title": "S"}])
    prog.start_phase("S")
    r1 = prog.agent_started(0, "a", "S", "0")
    prog.agent_finished(r1, status="completed", tokens=1)
    prog.agent_started(1, "b", "S", "1")  # still running
    assert prog.phases[0].done_count == 1
    assert len(prog.phases[0].agents) == 2


def test_render_run_lines_rich():
    prog = WorkflowProgress([{"title": "Search"}, {"title": "Verify"}])
    prog.start_phase("Search")
    rec = prog.agent_started(0, "google-search", "Search", "0", agent_type="researcher")
    prog.agent_finished(rec, status="completed", tokens=20300, tool_count=6)
    prog.start_phase("Verify")
    prog.agent_started(1, "verify", "Verify", "1")  # running
    state = SimpleNamespace(workflow_name="deep-research", status="running", progress=prog)
    blob = "\n".join(render_run_lines(state))

    assert "deep-research" in blob
    assert "1/2 agents" in blob        # overall progress
    assert "✔ google-search" in blob   # completed icon + label
    assert "researcher" in blob        # agent type
    assert "20.3k tok" in blob         # compact tokens
    assert "6 tools" in blob           # tool count
    assert "● verify" in blob          # running icon
    assert "▸ Search  (1/1" in blob    # per-phase progress
    assert "▸ Verify  (0/1" in blob


def test_render_run_lines_no_phases():
    state = SimpleNamespace(workflow_name="x", status="running", progress=WorkflowProgress([]))
    assert "no phases" in "\n".join(render_run_lines(state)).lower()


def test_render_run_lines_shows_error():
    prog = WorkflowProgress([{"title": "S"}])
    prog.start_phase("S")
    rec = prog.agent_started(0, "bad", "S", "0")
    prog.agent_finished(rec, status="failed", tokens=10, error="structured output not produced")
    state = SimpleNamespace(workflow_name="x", status="running", progress=prog)
    blob = "\n".join(render_run_lines(state))
    assert "✗ bad" in blob
    assert "structured output not produced" in blob

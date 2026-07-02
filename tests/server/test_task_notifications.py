"""Unit tests for the server-side task-notification helpers
(:mod:`src.server.task_notifications`) and the shared ``/workflows`` report
renderer (:func:`src.command_system.workflows_command.render_workflows_report`).

These are the pure pieces the agent-server worker loop composes to deliver
background-task completions (the old REPL consumer's successor)."""

from __future__ import annotations

from types import SimpleNamespace

from src.command_system.workflows_command import (
    NO_WORKFLOW_RUNS_MESSAGE,
    render_workflows_report,
)
from src.server.task_notifications import (
    build_notification_turn,
    format_completion_banner,
    format_completion_banner_xml,
    parse_task_id,
    render_banner,
)


def _phase(agents_done: int, agents_total: int, tokens: int = 0):
    agents = [
        SimpleNamespace(
            icon="✔", label=f"a{i}", agent_type="", tokens=10, tool_count=0,
            elapsed=None, error=None,
        )
        for i in range(agents_total)
    ]
    return SimpleNamespace(title="Search", agents=agents, done_count=agents_done, token_total=tokens)


def _workflow_state(**over):
    base = dict(
        type="local_workflow",
        status="completed",
        workflow_name="deep-research",
        description="",
        run_id="wf_abc123",
        progress=SimpleNamespace(phases=[_phase(2, 2, tokens=1500)], token_total=45_200),
        start_time=100.0,
        end_time=292.0,
        output_file="/tmp/wf_abc123.jsonl",
        error=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


ENVELOPE = (
    "<task-notification><task-id>local_workflow_1</task-id>"
    "<status>completed</status><summary>deep-research finished</summary>"
    "<output-file>/tmp/wf_abc123.jsonl</output-file>"
    "<result>report at /tmp/report.md</result></task-notification>"
)


# ─── envelope parsing / banners ───────────────────────────────────────────────


def test_parse_task_id():
    assert parse_task_id(ENVELOPE) == "local_workflow_1"
    assert parse_task_id("<task-notification></task-notification>") is None


def test_banner_from_workflow_state_is_plain_text():
    lines = format_completion_banner(_workflow_state())
    assert lines[0].startswith("✔ deep-research completed")
    assert "2 agents" in lines[0]
    assert "45.2k tok" in lines[0]
    assert "3m 12s" in lines[0]
    assert lines[-1] == "  run journal → /tmp/wf_abc123.jsonl"
    # De-Rich'd: no rich markup tags survive.
    assert not any("[green]" in ln or "[bold]" in ln for ln in lines)


def test_banner_labels_background_agent_by_description():
    """The task-notification queue is shared with background agents — a state
    without ``workflow_name`` banners as its description, not as 'workflow'."""
    state = _workflow_state(workflow_name="", description="Explore: map the auth module")
    lines = format_completion_banner(state)
    assert lines[0].startswith("✔ Explore: map the auth module completed")


def test_banner_failed_includes_error():
    lines = format_completion_banner(
        _workflow_state(status="failed", error="budget exhausted", progress=None)
    )
    assert lines[0].startswith("✗ deep-research failed")
    assert "  budget exhausted" in lines
    assert any("run journal" in ln for ln in lines)


def test_banner_xml_fallback_and_render_banner():
    lines = format_completion_banner_xml(ENVELOPE)
    assert lines[0] == "✔ deep-research finished"
    assert lines[1] == "  run journal → /tmp/wf_abc123.jsonl"
    # render_banner prefers state, falls back to the envelope.
    assert render_banner(ENVELOPE, None) == lines
    assert render_banner(ENVELOPE, _workflow_state())[0].startswith("✔ deep-research completed")


def test_build_notification_turn_assembles_preamble_and_envelopes():
    turn = build_notification_turn([ENVELOPE, "", "  <task-notification>x</task-notification>  "])
    assert turn.startswith("<system-reminder>")
    assert "background tasks you launched have finished" in turn
    assert ENVELOPE in turn
    assert turn.rstrip().endswith("<task-notification>x</task-notification>")


# ─── /workflows report renderer ───────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self, tasks):
        self._tasks = tasks

    def all(self):
        return list(self._tasks)


def test_render_workflows_report_none_when_empty():
    assert render_workflows_report(None) is None
    assert render_workflows_report(_FakeRegistry([])) is None
    assert render_workflows_report(
        _FakeRegistry([SimpleNamespace(type="local_agent")])
    ) is None
    assert "No workflow runs" in NO_WORKFLOW_RUNS_MESSAGE


def test_render_workflows_report_lists_runs_with_run_id():
    report = render_workflows_report(_FakeRegistry([_workflow_state()]))
    assert report is not None
    assert report.splitlines()[0].startswith("deep-research  [completed]")
    assert "(run: wf_abc123)" in report.splitlines()[0]
    assert any("▸ Search" in ln for ln in report.splitlines())


def test_render_workflows_report_degrades_per_run():
    """A run whose live progress object explodes mid-render must not take the
    report down — it degrades to a header-only line."""

    class _Boom:
        @property
        def phases(self):
            raise RuntimeError("mutated mid-render")

    bad = _workflow_state(progress=_Boom(), workflow_name="angry")
    good = _workflow_state()
    report = render_workflows_report(_FakeRegistry([bad, good]))
    assert report is not None
    assert "angry  [completed]  (progress unavailable)" in report
    assert "deep-research  [completed]" in report

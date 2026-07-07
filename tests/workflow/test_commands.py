"""Tests for workflow slash-command contribution (Phase 7)."""

from __future__ import annotations

from src.command_system.workflows_integration import (
    _deep_research_command,
    load_workflow_commands,
)

_VALID = 'meta = {"name": "x", "description": "My saved workflow", "phases": []}\nreturn 1\n'


def test_deep_research_bundled_command():
    cmd = _deep_research_command()
    assert cmd is not None
    assert cmd.name == "deep-research"
    assert cmd.kind == "workflow"
    assert cmd.loaded_from == "bundled"
    assert "deep_research.py" in cmd.markdown_content
    assert "$ARGUMENTS" in cmd.markdown_content


def test_discovers_project_workflow(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "triage.py").write_text(_VALID, encoding="utf-8")

    cmds = load_workflow_commands(str(tmp_path))
    by_name = {c.name: c for c in cmds}
    assert "deep-research" in by_name  # bundled is always present
    assert "triage" in by_name
    triage = by_name["triage"]
    assert triage.kind == "workflow"
    assert triage.loaded_from == "project"
    assert triage.description == "My saved workflow"
    assert str(wf_dir / "triage.py") in triage.markdown_content


def test_invalid_workflow_file_is_skipped(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "broken.py").write_text("x = 1  # no meta block\n", encoding="utf-8")
    (wf_dir / "good.py").write_text(_VALID, encoding="utf-8")

    names = {c.name for c in load_workflow_commands(str(tmp_path))}
    assert "good" in names
    assert "broken" not in names


def test_command_gating(monkeypatch, tmp_path):
    from src.settings.types import SettingsSchema

    monkeypatch.setattr("src.settings.settings.get_settings", lambda **_: SettingsSchema())
    cmd = _deep_research_command()
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    assert cmd.is_enabled() is True
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert cmd.is_enabled() is False


async def test_workflows_command_lists_runs():
    from types import SimpleNamespace

    from src.command_system.workflows_command import WORKFLOWS_COMMAND
    from src.task_registry import RuntimeTaskRegistry
    from src.tasks.local_workflow import register_workflow_task
    from src.workflow.progress import WorkflowProgress

    reg = RuntimeTaskRegistry()
    register_workflow_task(
        task_id="wq", run_id="r1", workflow_name="demo", description="d",
        output_file="/tmp/x", progress=WorkflowProgress(), run=None, registry=reg,
    )
    ctx = SimpleNamespace(tool_context=SimpleNamespace(runtime_tasks=reg))
    out = await WORKFLOWS_COMMAND.run("", ctx)
    assert "demo" in out.message and "r1" in out.message


async def test_workflows_command_empty_and_unavailable():
    from types import SimpleNamespace

    from src.command_system.workflows_command import WORKFLOWS_COMMAND
    from src.task_registry import RuntimeTaskRegistry

    empty = await WORKFLOWS_COMMAND.run("", SimpleNamespace(tool_context=SimpleNamespace(runtime_tasks=RuntimeTaskRegistry())))
    assert "No workflow runs" in empty.message
    na = await WORKFLOWS_COMMAND.run("", SimpleNamespace(tool_context=None))
    assert "unavailable" in na.message.lower()


def test_aggregator_includes_workflow_commands(tmp_path, monkeypatch):
    from src.command_system import aggregator
    from src.settings.types import SettingsSchema

    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    monkeypatch.setattr("src.settings.settings.get_settings", lambda **_: SettingsSchema())
    aggregator.clear_commands_cache()

    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "myflow.py").write_text(_VALID, encoding="utf-8")

    names = {c.name for c in aggregator.get_commands(str(tmp_path))}
    assert "deep-research" in names
    assert "myflow" in names
    aggregator.clear_commands_cache()

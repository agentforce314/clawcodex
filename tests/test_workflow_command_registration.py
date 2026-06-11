"""The bundled /deep-research workflow must reach the command surface.

Regression guard: the workflow PromptCommands were produced by the aggregator's
``get_commands()`` but never registered into the global command registry that
suggestions + dispatch actually read, so ``/deep-research`` had no autocomplete
and dispatched as raw text. They are now surfaced via ``get_builtin_commands()``.
"""

from __future__ import annotations

from src.command_system.builtins import get_builtin_commands, register_builtin_commands
from src.command_system.registry import get_command_registry


def _names(cmds):
    return {getattr(c, "name", None) for c in cmds}


def test_deep_research_in_builtin_commands_when_enabled(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    assert "deep-research" in _names(get_builtin_commands())


def test_deep_research_absent_when_workflows_disabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert "deep-research" not in _names(get_builtin_commands())


def test_global_registry_resolves_deep_research(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    register_builtin_commands(None)  # populates the global registry
    cmd = get_command_registry().get("deep-research")
    assert cmd is not None
    assert getattr(cmd, "kind", None) == "workflow"
    # the dispatched prompt is the Workflow-tool directive, not raw text
    directive = getattr(cmd, "markdown_content", "")
    assert "Workflow tool" in directive
    # ...and it tells the model to STOP after launching (background run), so the
    # main turn doesn't run away "waiting" for the result.
    assert "END YOUR TURN" in directive
    assert "background" in directive.lower()


def test_global_registry_resolves_workflows_viewer(monkeypatch):
    """``/workflows`` must dispatch in the Rich REPL (reads runtime_tasks)."""
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    register_builtin_commands(None)
    assert get_command_registry().get("workflows") is not None
    assert "workflows" in _names(get_builtin_commands())


def test_workflows_viewer_lists_runs():
    """Given a runtime_tasks registry with a local_workflow task, it lists it."""
    import asyncio
    from types import SimpleNamespace

    from src.command_system.workflows_command import WORKFLOWS_COMMAND

    task = SimpleNamespace(
        type="local_workflow", status="running", workflow_name="deep-research",
        run_id="wf_abc123", summary="Search · 4 agents",
    )
    ctx = SimpleNamespace(tool_context=SimpleNamespace(runtime_tasks=SimpleNamespace(all=lambda: [task])))
    out = asyncio.run(WORKFLOWS_COMMAND.run("", ctx))
    assert "deep-research" in out.message and "wf_abc123" in out.message

    empty = SimpleNamespace(tool_context=SimpleNamespace(runtime_tasks=SimpleNamespace(all=lambda: [])))
    assert "No workflow runs" in asyncio.run(WORKFLOWS_COMMAND.run("", empty)).message

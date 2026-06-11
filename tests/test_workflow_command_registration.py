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
    assert "Workflow tool" in getattr(cmd, "markdown_content", "")

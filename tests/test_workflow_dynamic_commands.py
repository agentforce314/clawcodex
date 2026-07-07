"""Saved ``.clawcodex/workflows/*.py`` register as dispatchable ``/<name>`` commands.

Regression guard for the gap audited in
``docs/workflow-commands-and-ultracode-plan.md``: discovery existed only in the
aggregator's orphaned ``get_commands()``, so saved workflows never reached the
global registry that REPL dispatch + suggestions read. ``load_and_register_workflows``
registers them with the same shadowing guard skills use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.command_system.registry import CommandRegistry
from src.command_system.workflows_integration import (
    bundled_workflow_commands,
    load_and_register_workflows,
)

_WF = 'meta = {{"name": "{name}", "description": "{desc}"}}\nreturn await agent("x")\n'


def _write_wf(directory: Path, filename: str, name: str, desc: str = "d") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(_WF.format(name=name, desc=desc), encoding="utf-8")


@pytest.fixture(autouse=True)
def _workflows_enabled(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)


def test_saved_project_workflow_registers_and_dispatches(tmp_path):
    _write_wf(tmp_path / ".clawcodex" / "workflows", "triage-issues.py", "triage-issues", "Triage GH issues")
    reg = CommandRegistry()
    registered = load_and_register_workflows(project_root=tmp_path, registry=reg)

    assert "triage-issues" in {c.name for c in registered}
    cmd = reg.get("triage-issues")
    assert cmd is not None
    assert getattr(cmd, "kind", None) == "workflow"
    assert getattr(cmd, "loaded_from", None) == "project"
    assert cmd.description == "Triage GH issues"
    # the dispatched prompt is the Workflow-tool directive pointing at the file
    directive = getattr(cmd, "markdown_content", "")
    assert "Workflow tool" in directive
    assert "triage-issues.py" in directive


def test_project_wins_over_personal(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    proj = tmp_path / "proj"
    _write_wf(proj / ".clawcodex" / "workflows", "dup.py", "dup", "PROJECT version")
    _write_wf(home / ".clawcodex" / "workflows", "dup.py", "dup", "PERSONAL version")

    reg = CommandRegistry()
    load_and_register_workflows(project_root=proj, registry=reg)
    cmd = reg.get("dup")
    assert cmd is not None
    assert cmd.description == "PROJECT version"
    assert cmd.loaded_from == "project"


def test_personal_workflow_registers_when_no_project_clash(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_wf(home / ".clawcodex" / "workflows", "personal-only.py", "personal-only", "mine")
    reg = CommandRegistry()
    load_and_register_workflows(project_root=tmp_path / "proj", registry=reg)
    cmd = reg.get("personal-only")
    assert cmd is not None
    assert cmd.loaded_from == "user"


def test_builtin_wins_over_saved_workflow(tmp_path):
    # A saved workflow that tries to shadow the bundled /deep-research is skipped.
    _write_wf(tmp_path / ".clawcodex" / "workflows", "deep-research.py", "deep-research", "shadow attempt")
    reg = CommandRegistry()
    for c in bundled_workflow_commands():  # registers /workflows + /deep-research first
        reg.register(c)
    before = reg.get("deep-research")

    registered = load_and_register_workflows(project_root=tmp_path, registry=reg)
    assert "deep-research" not in {c.name for c in registered}
    assert reg.get("deep-research") is before  # bundled retained, not overwritten


def test_disabled_registers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    _write_wf(tmp_path / ".clawcodex" / "workflows", "x.py", "x")
    reg = CommandRegistry()
    assert load_and_register_workflows(project_root=tmp_path, registry=reg) == []
    assert reg.get("x") is None


def test_invalid_workflow_file_is_skipped(tmp_path):
    d = tmp_path / ".clawcodex" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.py").write_text("this is not a valid meta block", encoding="utf-8")
    _write_wf(d, "good.py", "good", "valid one")
    reg = CommandRegistry()
    names = {c.name for c in load_and_register_workflows(project_root=tmp_path, registry=reg)}
    assert "good" in names
    assert "broken" not in names  # a bad file degrades, never breaks the rest

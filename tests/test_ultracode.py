"""The ``ultracode`` workflow-authoring trigger (workflow-engine §4.1, §4.8).

Covers the pure detection/reminder module (``src/workflow/ultracode.py``) and the
``/effort ultracode`` session toggle wired into ``effort_command.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.command_system import create_command_context
from src.command_system.effort_command import EFFORT_COMMAND, _effort_options
from src.workflow import ultracode as uc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)  # enabled by default
    uc.reset_ultracode()
    yield
    uc.reset_ultracode()


def _ctx(tmp_path: Path):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path)


# ── keyword detection ─────────────────────────────────────────────────────────


def test_keyword_detection_positive():
    assert uc.prompt_requests_ultracode("please ultracode this audit")
    assert uc.prompt_requests_ultracode("ULTRACODE the repo")  # case-insensitive
    assert uc.prompt_requests_ultracode("do it. ultracode.")    # punctuation boundary


def test_keyword_detection_negative():
    assert not uc.prompt_requests_ultracode("refactor the code")
    assert not uc.prompt_requests_ultracode("ultracoder")  # word boundary, not a substring
    assert not uc.prompt_requests_ultracode("supraultracode")
    assert not uc.prompt_requests_ultracode("")


def test_keyword_detection_disabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert not uc.prompt_requests_ultracode("ultracode this")  # §4.8: no-op when off


# ── reminder selection ────────────────────────────────────────────────────────


def test_reminder_for_keyword():
    r = uc.ultracode_reminder_for("ultracode: build X")
    assert r is not None
    assert "<system-reminder>" in r
    assert '"ultracode"' in r
    assert "Workflow tool" in r


def test_reminder_none_when_idle():
    assert uc.ultracode_reminder_for("just chatting about the weather") is None


def test_reminder_for_session_mode():
    uc.set_ultracode_session(True)
    r = uc.ultracode_reminder_for("do a normal thing")
    assert r is not None
    assert "on for this session" in r


def test_keyword_beats_session():
    uc.set_ultracode_session(True)
    r = uc.ultracode_reminder_for("ultracode it")
    assert '"ultracode"' in r  # one-shot keyword reminder wins over the session one


def test_reminder_none_when_disabled(monkeypatch):
    uc.set_ultracode_session(True)
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert uc.ultracode_reminder_for("ultracode it") is None


# ── session flag ──────────────────────────────────────────────────────────────


def test_session_flag_set_reset():
    assert uc.is_ultracode_session() is False
    uc.set_ultracode_session(True)
    assert uc.is_ultracode_session() is True
    uc.reset_ultracode()
    assert uc.is_ultracode_session() is False


def test_session_flag_gated_by_enablement(monkeypatch):
    uc.set_ultracode_session(True)
    assert uc.is_ultracode_session() is True
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert uc.is_ultracode_session() is False  # gated even when the flag is set


# ── /effort ultracode ─────────────────────────────────────────────────────────


def test_effort_ultracode_enables_session(tmp_path, monkeypatch):
    persisted: list = []
    monkeypatch.setattr("src.command_system.effort_command.set_effort", lambda v: persisted.append(v))
    out = asyncio.run(EFFORT_COMMAND.run("ultracode", _ctx(tmp_path)))
    assert uc.is_ultracode_session() is True
    assert "Ultracode on" in out.message
    assert persisted == []  # ultracode is a mode, NOT a persisted effort level


def test_effort_high_clears_ultracode(tmp_path, monkeypatch):
    monkeypatch.setattr("src.command_system.effort_command.set_effort", lambda v: None)
    uc.set_ultracode_session(True)
    out = asyncio.run(EFFORT_COMMAND.run("high", _ctx(tmp_path)))
    assert uc.is_ultracode_session() is False  # "reset with /effort high"
    assert "high" in out.message


def test_effort_auto_clears_ultracode(tmp_path, monkeypatch):
    monkeypatch.setattr("src.command_system.effort_command.set_effort", lambda v: None)
    uc.set_ultracode_session(True)
    asyncio.run(EFFORT_COMMAND.run("auto", _ctx(tmp_path)))
    assert uc.is_ultracode_session() is False


def test_effort_ultracode_rejected_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    out = asyncio.run(EFFORT_COMMAND.run("ultracode", _ctx(tmp_path)))
    assert uc.is_ultracode_session() is False
    assert "Invalid argument" in out.message  # §4.8


def test_picker_includes_ultracode_when_enabled():
    assert "ultracode" in [o.value for o in _effort_options("auto")]


def test_picker_excludes_ultracode_when_disabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert "ultracode" not in [o.value for o in _effort_options("auto")]


# ── /ultracode slash command (discoverable authoring form) ────────────────────


def _names(cmds):
    return {getattr(c, "name", None) for c in cmds}


def test_ultracode_is_a_registered_command():
    from src.command_system.builtins import get_builtin_commands, register_builtin_commands
    from src.command_system.registry import get_command_registry

    assert "ultracode" in _names(get_builtin_commands())  # autocompletes + dispatches
    register_builtin_commands(None)
    cmd = get_command_registry().get("ultracode")
    assert cmd is not None
    assert getattr(cmd, "kind", None) == "workflow"


def test_ultracode_command_directive_authors_and_saves():
    from src.command_system.workflows_integration import _ultracode_command

    cmd = _ultracode_command()
    directive = cmd.markdown_content or ""
    assert "AUTHOR" in directive                        # author a fresh workflow…
    assert ".claude/workflows" in directive             # …saved as a /<name> command
    assert "Write" in directive                         # via the Write tool
    assert "do NOT call the Workflow tool" in directive  # NOT run immediately
    assert "deep_research.py" in directive              # format template referenced
    assert "$ARGUMENTS" in directive                    # the task is substituted in
    # description reflects "save as a /<name> command", not "run"
    desc = (cmd.description or "").lower()
    assert "save" in desc and "/<name>" in desc


def test_ultracode_command_absent_when_workflows_disabled(monkeypatch):
    from src.command_system.builtins import get_builtin_commands

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert "ultracode" not in _names(get_builtin_commands())


# ── in-session pickup of a freshly-authored /<name> workflow ──────────────────


def test_refresh_workflow_commands_picks_up_new_file(tmp_path, monkeypatch):
    """A workflow saved into .claude/workflows/ mid-session becomes a /<name>
    command without a restart (the /ultracode → /<name> handoff)."""
    from src.command_system.registry import CommandRegistry, get_command_registry
    from src.repl.core import ClawcodexREPL

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")  # empty personal dir

    wfdir = tmp_path / ".claude" / "workflows"
    wfdir.mkdir(parents=True)
    (wfdir / "ultratest-scraper.py").write_text(
        'meta = {"name": "ultratest-scraper", "description": "scrape something"}\n'
        'return await agent("x")\n',
        encoding="utf-8",
    )

    repl = ClawcodexREPL.__new__(ClawcodexREPL)
    repl._wf_dirs_sig = None
    repl.command_registry = CommandRegistry()
    repl._original_built_ins = []
    repl._built_in_commands = []

    repl._refresh_workflow_commands()

    # dispatchable via the global registry, and bare /<name> routes to execution
    assert get_command_registry().get("ultratest-scraper") is not None
    assert "/ultratest-scraper" in repl._built_in_commands

    # mtime-gated: a second call with no dir change is a no-op (signature unchanged)
    sig_after = repl._wf_dirs_sig
    repl._refresh_workflow_commands()
    assert repl._wf_dirs_sig == sig_after

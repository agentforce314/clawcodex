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


def test_ultracode_command_directive_authors_and_launches():
    from src.command_system.workflows_integration import _ultracode_command

    directive = _ultracode_command().markdown_content or ""
    assert "AUTHOR" in directive            # author a fresh workflow…
    assert "Workflow tool" in directive     # …and launch it via the tool
    assert "$ARGUMENTS" in directive        # the task is substituted in
    assert "END YOUR TURN" in directive     # background run → don't block


def test_ultracode_command_absent_when_workflows_disabled(monkeypatch):
    from src.command_system.builtins import get_builtin_commands

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert "ultracode" not in _names(get_builtin_commands())

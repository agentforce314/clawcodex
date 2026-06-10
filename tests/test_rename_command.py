"""Tests for the ``/rename`` command (Phase 18 — re-landed on the persistence producer).

The persist channel is genuinely live now: the producer (PR #260) writes
SessionStorage metadata/transcripts in normal TUI operation, and the id channel is
unified (``Session.create`` reads bootstrap ``get_session_id()``).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.services.session_storage as ss
from src.command_system import (
    RENAME_COMMAND,
    RenameCommand,
    create_command_context,
    get_builtin_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.rename_command import _conversation_text, _generate_session_name
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost

_NO_CONTEXT = (
    "Could not generate a name: no conversation context yet. Usage: /rename <name>"
)
_SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def rename_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("src.bootstrap.state.get_session_id", lambda: _SID)
    return tmp_path


def _ctx(tmp_path, messages=None, *, ui=None):
    conversation = SimpleNamespace(messages=messages or [])
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, conversation=conversation, ui=ui
    )


def _title(tmp_path):
    meta = ss.SessionStorage(session_id=_SID, sessions_dir=tmp_path / "sessions").get_metadata()
    return meta.title if meta else None


# --------------------------------------------------------------------------- #
# A. Args path (init-when-absent + update-when-present)
# --------------------------------------------------------------------------- #
async def test_rename_with_args_inits_metadata(rename_env):
    out = await RENAME_COMMAND.run("my-session", _ctx(rename_env, ui=NullUIHost()))
    assert isinstance(out, InteractiveOutcome)
    assert out.message == "Session renamed to: my-session"
    assert out.display == "system"
    assert _title(rename_env) == "my-session"


async def test_rename_updates_existing_metadata(rename_env):
    # The producer-initialized case: metadata exists (model/cwd from the run).
    storage = ss.SessionStorage(session_id=_SID, sessions_dir=rename_env / "sessions")
    storage.init_metadata(model="m", cwd="/x", title="old")
    out = await RENAME_COMMAND.run("new-name", _ctx(rename_env, ui=NullUIHost()))
    assert out.message == "Session renamed to: new-name"
    meta = ss.SessionStorage(session_id=_SID, sessions_dir=rename_env / "sessions").get_metadata()
    assert meta.title == "new-name"
    assert meta.model == "m"  # other fields preserved by update


# --------------------------------------------------------------------------- #
# B. No-args generation path
# --------------------------------------------------------------------------- #
async def test_rename_generates_name(rename_env, monkeypatch):
    async def _gen(messages):
        return "fix-login-bug"

    monkeypatch.setattr(
        "src.command_system.rename_command._generate_session_name", _gen
    )
    out = await RENAME_COMMAND.run("", _ctx(rename_env, ui=NullUIHost()))
    assert out.message == "Session renamed to: fix-login-bug"
    assert _title(rename_env) == "fix-login-bug"


async def test_rename_no_context_message(rename_env, monkeypatch):
    async def _gen(messages):
        return None

    monkeypatch.setattr(
        "src.command_system.rename_command._generate_session_name", _gen
    )
    out = await RENAME_COMMAND.run("", _ctx(rename_env, ui=NullUIHost()))
    assert out.message == _NO_CONTEXT
    assert out.display == "system"
    assert _title(rename_env) is None  # nothing persisted


# --------------------------------------------------------------------------- #
# C. Generator unit
# --------------------------------------------------------------------------- #
def test_conversation_text_flattens_roles():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "system", "content": "skip me"},
    ]
    text = _conversation_text(msgs)
    assert "user: hello" in text and "assistant: hi" in text
    assert "skip me" not in text


async def test_generate_session_name_empty_messages_no_api():
    assert await _generate_session_name([]) is None


async def test_generate_session_name_failure_is_none(monkeypatch):
    import sys
    from types import ModuleType

    fake = ModuleType("anthropic")

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no key")

    fake.Anthropic = _Boom
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert await _generate_session_name([{"role": "user", "content": "x"}]) is None


# --------------------------------------------------------------------------- #
# D. End-to-end with the PRODUCER (the re-land integration lock)
# --------------------------------------------------------------------------- #
async def test_rename_after_producer_session_appears_in_resume_list(rename_env):
    from src.services.session_persistence import SessionPersister

    p = SessionPersister(_SID, sessions_dir=rename_env / "sessions")
    p.start(model="m", cwd="/w")
    p.record_user("hello")
    p.flush()

    out = await RENAME_COMMAND.run("better-name", _ctx(rename_env, ui=NullUIHost()))
    assert out.message == "Session renamed to: better-name"

    metas = ss.SessionStorage.list_sessions(sessions_dir=rename_env / "sessions")
    by_id = {m.session_id: m for m in metas}
    assert by_id[_SID].title == "better-name"  # the resume list shows the new title
    assert by_id[_SID].message_count == 1  # producer data intact


# --------------------------------------------------------------------------- #
# E. Headless + registration + safety + dispatch
# --------------------------------------------------------------------------- #
async def test_engine_headless_success(rename_env):
    reg = CommandRegistry()
    reg.register(RENAME_COMMAND)
    ctx = _ctx(rename_env)  # ui=None -> NullUIHost; never touched
    eng = CommandEngine(registry=reg, workspace_root=rename_env, context=ctx)
    result = await eng.execute("/rename cool-name")
    assert result.success is True
    assert result.text == "Session renamed to: cool-name"


def test_registered_metadata_safety_dispatch():
    assert "rename" in {c.name for c in get_builtin_commands()}
    assert isinstance(RENAME_COMMAND, RenameCommand)
    assert RENAME_COMMAND.description == "Rename the current conversation"
    assert RENAME_COMMAND.command_type == CommandType.INTERACTIVE
    assert is_bridge_safe_command(RENAME_COMMAND) is False
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/rename", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is False

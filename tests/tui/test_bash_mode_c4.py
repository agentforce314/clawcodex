"""C4 bash-mode tests: direct execution, conversation parity, UI rows."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.bash_mode import (
    BASH_INPUT_TAG,
    BASH_STDERR_TAG,
    BASH_STDOUT_TAG,
    run_bash_mode_command,
)
from src.tool_system.context import ToolContext


def _ctx(tmp: str) -> ToolContext:
    ctx = ToolContext(workspace_root=Path(tmp))
    # The ask path must NEVER be consulted for user-typed commands.
    ctx.permission_handler = MagicMock(
        side_effect=AssertionError("permission prompt fired in bash mode")
    )
    return ctx


class TestRunBashModeCommand:
    def test_success_captures_stdout_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command("echo hello", _ctx(tmp))
        assert outcome.ok
        assert outcome.exit_code == 0
        assert "hello" in outcome.stdout
        assert len(outcome.conversation_texts) == 2
        assert (
            outcome.conversation_texts[0]
            == f"<{BASH_INPUT_TAG}>echo hello</{BASH_INPUT_TAG}>"
        )
        assert f"<{BASH_STDOUT_TAG}>" in outcome.conversation_texts[1]
        assert f"<{BASH_STDERR_TAG}>" in outcome.conversation_texts[1]

    def test_no_permission_prompt_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(tmp)
            outcome = run_bash_mode_command("echo guarded", ctx)
        assert outcome.ok
        ctx.permission_handler.assert_not_called()

    def test_cd_persists_tool_context_cwd(self) -> None:
        # Deliberate TS parity (persistent-shell cwd writeback): a
        # user-typed `!cd <dir>` changes the shared tool cwd. Pinned so a
        # future pass doesn't "fix" it (review note).
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "subdir"
            sub.mkdir()
            ctx = _ctx(tmp)
            outcome = run_bash_mode_command("cd subdir", ctx)
            assert outcome.ok
            assert Path(ctx.cwd) == sub.resolve()

    def test_timeout_path(self, monkeypatch) -> None:
        monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "1000")
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command("sleep 2", _ctx(tmp))
        assert not outcome.ok
        assert outcome.exit_code == 143
        assert "timed out" in outcome.stderr

    def test_failure_exit_code_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command(
                "sh -c 'echo oops >&2; exit 3'", _ctx(tmp)
            )
        assert not outcome.ok
        assert outcome.exit_code == 3
        assert "oops" in outcome.stderr
        out_msg = outcome.conversation_texts[1]
        assert f"<{BASH_STDERR_TAG}>" in out_msg
        assert "oops" in out_msg.split(f"<{BASH_STDERR_TAG}>", 1)[1]

    def test_dangerous_command_refused_honestly(self) -> None:
        # bash_tool's defense-in-depth guard raises; the service converts
        # it to the TS error-path message shape, never raising.
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command("rm -rf /", _ctx(tmp))
        assert not outcome.ok
        assert outcome.error
        assert "Command failed:" in outcome.conversation_texts[1]

    def test_empty_command_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command("   ", _ctx(tmp))
        assert not outcome.ok
        assert outcome.conversation_texts == ()
        assert "Usage" in (outcome.error or "")

    def test_xml_special_chars_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = run_bash_mode_command("echo '<tag> & co'", _ctx(tmp))
        assert outcome.ok
        out_msg = outcome.conversation_texts[1]
        assert "&lt;tag&gt; &amp; co" in out_msg
        assert "<tag> & co" not in out_msg


class TestFinishBashMode:
    def test_fills_row_and_routes_texts_through_bridge(self) -> None:
        from src.services.bash_mode import BashModeOutcome
        from src.tui.app import ClawCodexTUI

        rows: list[tuple[str, dict]] = []
        appended: list[tuple] = []

        transcript = SimpleNamespace(
            finish_bash_io=lambda row, **kw: rows.append(("finish", kw)),
            append_system=lambda text, style="muted": rows.append(
                ("system", {"text": text})
            ),
        )
        fake = SimpleNamespace(
            _bash_inflight=True,
            _agent_bridge=SimpleNamespace(
                append_user_texts=lambda texts: appended.append(tuple(texts))
            ),
        )
        outcome = BashModeOutcome(
            command="echo hi",
            stdout="hi\n",
            ok=True,
            conversation_texts=(
                "<bash-input>echo hi</bash-input>",
                "<bash-stdout>hi\n</bash-stdout><bash-stderr></bash-stderr>",
            ),
        )
        ClawCodexTUI._finish_bash_mode(fake, outcome, object(), transcript)

        assert fake._bash_inflight is False
        assert rows[0][0] == "finish"
        assert rows[0][1]["command"] == "echo hi"
        assert appended == [outcome.conversation_texts]


class TestBridgeDeferral:
    """Review B1: texts arriving mid-run defer and drain in _finish."""

    def _bridge(self, tmp_path, monkeypatch):
        import src.services.session_storage as storage_mod
        from src.agent.session import Session
        from src.tool_system.context import ToolContext
        from src.tool_system.registry import ToolRegistry
        from src.tui.agent_bridge import AgentBridge
        from src.tui.state import AppState

        # Keep the real persister off the developer's ~/.clawcodex/sessions
        # (review note 2 — the established isolation pattern).
        monkeypatch.setattr(storage_mod, "SESSIONS_DIR", tmp_path / "sessions")

        session = Session.create("test", "test-model")
        bridge = AgentBridge(
            post_message=lambda _m: None,
            session=session,
            provider=MagicMock(model="m"),
            tool_registry=ToolRegistry(),
            tool_context=ToolContext(workspace_root=tmp_path),
            app_state=AppState(),
            run_worker=lambda *a, **k: None,
        )
        return bridge, session

    def test_idle_appends_immediately(self, tmp_path, monkeypatch) -> None:
        bridge, session = self._bridge(tmp_path, monkeypatch)
        before = len(session.conversation.messages)
        bridge.append_user_texts(("<bash-input>x</bash-input>",))
        assert len(session.conversation.messages) == before + 1

    def test_busy_defers_until_finish(self, tmp_path, monkeypatch) -> None:
        bridge, session = self._bridge(tmp_path, monkeypatch)
        before = len(session.conversation.messages)
        bridge._busy = True
        bridge.append_user_texts(
            ("<bash-input>x</bash-input>", "<bash-stdout></bash-stdout>")
        )
        # Nothing lands mid-run…
        assert len(session.conversation.messages) == before
        # …and the run's terminal path drains them in order.
        bridge._finish()
        msgs = session.conversation.messages
        assert len(msgs) == before + 2
        assert "<bash-input>" in str(msgs[-2].content)


@pytest.mark.asyncio
async def test_transcript_bash_rows_running_then_finish() -> None:
    pytest.importorskip("textual")
    from textual.app import App, ComposeResult

    from src.tui.widgets.transcript_view import TranscriptView

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield TranscriptView()

    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        long_out = "\n".join(f"l{i}" for i in range(50))
        row = view.append_bash_running("seq 50")
        await pilot.pause()
        assert len(view.query(".bash-io")) == 1  # echo row is synchronous
        view.finish_bash_io(
            row,
            command="seq 50",
            stdout=long_out,
            stderr="",
            exit_code=0,
            ok=True,
        )
        await pilot.pause()
        assert len(view.query(".bash-io")) == 1  # updated in place
        label, full = view._expandables[-1]
        assert label == "! seq 50"
        assert "l49" in full


@pytest.mark.asyncio
async def test_transcript_bash_stderr_shown_even_on_success() -> None:
    """git/npm write normal output to stderr with exit 0 (review M2)."""

    pytest.importorskip("textual")
    from textual.app import App, ComposeResult

    from src.tui.widgets.transcript_view import TranscriptView

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield TranscriptView()

    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        row = view.append_bash_running("git push")
        view.finish_bash_io(
            row,
            command="git push",
            stdout="",
            stderr="Everything up-to-date\n",
            exit_code=0,
            ok=True,
        )
        await pilot.pause()
        from tests.tui.test_transcript_c3b import _rendered

        assert "Everything up-to-date" in _rendered(view.snapshot())


class TestReplDispatch:
    def test_bang_routes_to_bash_mode_not_agent(self) -> None:
        from src.tui.screens.repl import REPLScreen

        calls: list[tuple[str, object]] = []
        fake = SimpleNamespace(
            app=SimpleNamespace(
                handle_local_slash_command=lambda text, t: False,
                run_bash_mode=lambda cmd, t: calls.append(("bash", cmd)),
                submit_to_agent=lambda text: calls.append(("agent", text)),
            ),
            transcript=SimpleNamespace(
                append_user=lambda text: calls.append(("user", text))
            ),
            status_bar=SimpleNamespace(
                set_busy=lambda: None, bump_turn=lambda: None
            ),
        )
        REPLScreen.on_prompt_submitted(
            fake, SimpleNamespace(text="!git status")
        )
        assert calls == [("bash", "git status")]

    def test_busy_refusal_row(self) -> None:
        from src.tui.app import ClawCodexTUI

        rows: list[str] = []
        history: list[str] = []
        fake = SimpleNamespace(
            history_store=SimpleNamespace(append=history.append),
            _bash_inflight=False,
            _agent_bridge=SimpleNamespace(busy=True),
        )
        transcript = SimpleNamespace(
            append_system=lambda text, style="muted": rows.append(text)
        )
        ClawCodexTUI.run_bash_mode(fake, "ls", transcript)
        assert any("Agent is working" in r for r in rows)
        # Refused commands still reach history (m11).
        assert history == ["!ls"]

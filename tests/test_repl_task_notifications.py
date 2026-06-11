"""Background-task completion notifications surfaced in the REPL.

Covers the pure formatter/turn-builder (``src/repl/task_notifications.py``) and
the thin REPL glue (``ClawcodexREPL._deliver_pending_task_notifications`` /
``_wake_idle_prompt`` / ``_notification_watcher``), exercised on a bare instance
(``__new__``) so the heavy constructor is skipped.
"""

from __future__ import annotations

import io
import threading
import time
from types import SimpleNamespace

import pytest

from src.repl.task_notifications import (
    build_notification_turn,
    format_completion_banner,
    format_completion_banner_xml,
    parse_task_id,
    render_banner,
)
from src.tasks.local_workflow import (
    complete_workflow_task,
    fail_workflow_task,
    register_workflow_task,
)
from src.task_registry import RuntimeTaskRegistry
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    peek_pending_notifications,
)
from src.workflow.progress import WorkflowProgress


@pytest.fixture(autouse=True)
def _clean_queue():
    clear_pending_notifications()
    yield
    clear_pending_notifications()


class _FakeRun:
    controller = SimpleNamespace(abort=lambda *a, **k: None)


def _completed_registry(*, task_id="t1", name="deep-research", result=None, tokens=5):
    reg = RuntimeTaskRegistry()
    prog = WorkflowProgress([{"title": "Search"}])
    prog.start_phase("Search")
    rec = prog.agent_started(0, "finder", "Search", "0")
    prog.agent_finished(rec, status="completed", tokens=tokens)
    register_workflow_task(
        task_id=task_id, run_id="r1", workflow_name=name, description="d",
        output_file="/tmp/journal.json", progress=prog, run=_FakeRun(), registry=reg,
    )
    complete_workflow_task(task_id, result=result or {"report": "Micron HBM edge"}, registry=reg)
    return reg


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_parse_task_id():
    xml = "<task-notification>\n<task-id>w94sx230j</task-id>\n<status>completed</status>\n</task-notification>"
    assert parse_task_id(xml) == "w94sx230j"
    assert parse_task_id("<task-notification></task-notification>") is None


def test_format_completion_banner_completed():
    reg = _completed_registry(tokens=847_700)
    lines = format_completion_banner(reg.get("t1"))
    blob = "\n".join(lines)
    assert "✔" in blob
    assert "deep-research" in blob
    assert "completed" in blob
    assert "1 agents" in blob          # one finished agent
    assert "847.7k tok" in blob        # compact tokens
    assert "journal → /tmp/journal.json" in blob


def test_format_completion_banner_failed_shows_error():
    reg = RuntimeTaskRegistry()
    register_workflow_task(
        task_id="t2", run_id="r2", workflow_name="deep-research", description="d",
        output_file="/tmp/j2.json", progress=WorkflowProgress([]), run=_FakeRun(), registry=reg,
    )
    fail_workflow_task("t2", error="synthesize: structured output not produced", registry=reg)
    blob = "\n".join(format_completion_banner(reg.get("t2")))
    assert "✗" in blob
    assert "failed" in blob
    assert "structured output not produced" in blob


def test_format_completion_banner_xml_fallback():
    xml = (
        "<task-notification>\n<task-id>x</task-id>\n<output-file>/tmp/j.json</output-file>\n"
        '<status>completed</status>\n<summary>Agent "deep-research" completed</summary>\n</task-notification>'
    )
    blob = "\n".join(format_completion_banner_xml(xml))
    assert "✔" in blob
    assert 'Agent "deep-research" completed' in blob
    assert "journal → /tmp/j.json" in blob


def test_render_banner_prefers_state_falls_back_to_xml():
    reg = _completed_registry()
    assert any("deep-research" in ln for ln in render_banner("<x/>", reg.get("t1")))
    # no state -> fall back to the envelope's summary
    xml = "<task-notification>\n<status>killed</status>\n<summary>Agent \"w\" was stopped</summary>\n</task-notification>"
    assert any("stopped" in ln for ln in render_banner(xml, None))


def test_build_notification_turn_wraps_envelopes():
    turn = build_notification_turn(["<task-notification>A</task-notification>", "<task-notification>B</task-notification>"])
    assert "<system-reminder>" in turn
    assert "background tasks" in turn
    assert "<task-notification>A</task-notification>" in turn
    assert "<task-notification>B</task-notification>" in turn


# ── REPL glue (bare instance via __new__) ─────────────────────────────────────


def _bare_repl():
    from rich.console import Console

    from src.repl.core import ClawcodexREPL

    repl = ClawcodexREPL.__new__(ClawcodexREPL)
    buf = io.StringIO()
    repl.console = Console(file=buf, width=100, force_terminal=False)
    repl._at_prompt = False
    repl._notif_stop = None
    return repl, buf


def test_deliver_prints_banner_and_feeds_agent():
    reg = _completed_registry()
    repl, buf = _bare_repl()
    repl.tool_context = SimpleNamespace(runtime_tasks=reg)
    turns: list[str] = []
    repl.chat = lambda text: turns.append(text)

    assert peek_pending_notifications()  # complete_workflow_task enqueued one
    assert repl._deliver_pending_task_notifications() is True

    out = buf.getvalue()
    assert "✔" in out and "deep-research" in out         # banner printed
    assert len(turns) == 1                                # exactly one agent turn
    assert "<task-notification>" in turns[0]              # envelope handed to agent
    assert "<system-reminder>" in turns[0]               # with the guiding preamble

    # queue is now empty -> a second call is a no-op (no extra turn)
    assert repl._deliver_pending_task_notifications() is False
    assert len(turns) == 1


def test_deliver_noop_on_empty_queue():
    repl, _ = _bare_repl()
    repl.tool_context = SimpleNamespace(runtime_tasks=RuntimeTaskRegistry())
    repl.chat = lambda text: pytest.fail("chat must not run with an empty queue")
    assert repl._deliver_pending_task_notifications() is False


# ── idle-prompt wake guard ────────────────────────────────────────────────────


class _FakeLoop:
    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed

    def call_soon_threadsafe(self, fn):  # run inline so the test is deterministic
        fn()


class _FakeBuffer:
    def __init__(self, text=""):
        self.text = text


class _FakeApp:
    def __init__(self, *, is_running=True, text=""):
        self.is_running = is_running
        self.loop = _FakeLoop()
        self.current_buffer = _FakeBuffer(text)
        self.exited_with = "UNSET"

    def exit(self, result=None):
        self.exited_with = result


def _wake_with(app):
    repl, _ = _bare_repl()
    repl.prompt_session = SimpleNamespace(app=app)
    repl._wake_idle_prompt()
    return app


def test_wake_exits_when_idle_and_empty():
    app = _wake_with(_FakeApp(is_running=True, text=""))
    assert app.exited_with == ""  # prompt() will return "" -> no-op turn


def test_wake_leaves_half_typed_input_untouched():
    app = _wake_with(_FakeApp(is_running=True, text="git comm"))
    assert app.exited_with == "UNSET"  # never clobber the user's buffer


def test_wake_noop_when_app_not_running():
    app = _wake_with(_FakeApp(is_running=False, text=""))
    assert app.exited_with == "UNSET"


def test_wake_noop_when_no_session():
    repl, _ = _bare_repl()
    repl.prompt_session = None
    repl._wake_idle_prompt()  # must not raise


# ── watcher wakes an idle prompt on a pending notification ────────────────────


def test_watcher_wakes_prompt_when_idle_and_pending():
    repl, _ = _bare_repl()
    woke = threading.Event()
    repl._wake_idle_prompt = woke.set  # type: ignore[method-assign]
    repl._at_prompt = True

    _completed_registry()  # enqueues a notification onto the global queue
    stop = threading.Event()
    t = threading.Thread(target=repl._notification_watcher, args=(stop,), daemon=True)
    t.start()
    try:
        assert woke.wait(2.0), "watcher should wake the idle prompt within 2s"
    finally:
        stop.set()
        t.join(timeout=1.0)


def test_watcher_idle_false_does_not_wake():
    repl, _ = _bare_repl()
    woke = threading.Event()
    repl._wake_idle_prompt = woke.set  # type: ignore[method-assign]
    repl._at_prompt = False  # not at the prompt -> never wake

    _completed_registry()
    stop = threading.Event()
    t = threading.Thread(target=repl._notification_watcher, args=(stop,), daemon=True)
    t.start()
    try:
        assert not woke.wait(0.8)
    finally:
        stop.set()
        t.join(timeout=1.0)


# ── end-to-end: production launcher → enqueue → REPL delivery ──────────────────


class _MiniRunner:
    """Minimal AgentRunner — returns a structured result without a live model."""

    async def run(self, spec, *, abort, index):
        from src.workflow.types import AgentOutcome

        if spec.schema is not None:
            return AgentOutcome(structured={"echo": spec.prompt}, tokens=12)
        return AgentOutcome(text="Micron's HBM edge is its 1-beta node.", tokens=12)


def test_end_to_end_launch_to_repl_delivery(tmp_path):
    import asyncio

    from src.workflow.launch import run_workflow_task

    reg = RuntimeTaskRegistry()
    source = (
        'meta = {"name": "deep-research", "description": "d", "phases": [{"title": "Go"}]}\n'
        'phase("Go")\n'
        'r = await agent("research micron hbm")\n'
        'return {"report": r}\n'
    )
    result = asyncio.run(
        run_workflow_task(
            source=source,
            runner=_MiniRunner(),
            registry=reg,
            task_id="we2e",
            run_id="wf_e2e",
            output_file=str(tmp_path / "journal.json"),
        )
    )
    assert result.ok
    assert reg.get("we2e").status == "completed"
    # the launcher's completion path enqueued exactly one envelope
    assert len(peek_pending_notifications()) == 1

    # the REPL drains it: banner to console + one agent turn carrying the result
    repl, buf = _bare_repl()
    repl.tool_context = SimpleNamespace(runtime_tasks=reg)
    turns: list[str] = []
    repl.chat = lambda text: turns.append(text)
    assert repl._deliver_pending_task_notifications() is True

    out = buf.getvalue()
    assert "✔" in out and "deep-research" in out
    assert len(turns) == 1
    assert "Micron's HBM edge" in turns[0]  # the workflow's result reached the agent

"""Integration tests for the agent-server's workflow surfaces.

The dynamic-workflow UX used to live in the deleted Rich REPL / Textual TUI
(#566); these tests pin its agent-server replacements:

  * the ``ultracode`` keyword appends the authoring ``<system-reminder>`` to
    the model-visible user turn (and only to that turn),
  * ``set_effort`` handles ``ultracode`` (session mode on/off, read-only
    report, workflows-disabled gating),
  * the ``workflows`` / ``list_workflow_commands`` / ``workflow_command``
    controls (report text, catalog, directive expansion, gating), and
  * the worker loop drains finished-task ``<task-notification>`` envelopes:
    one banner frame per task + ONE internal summarization turn that skips
    the ultracode reminder.

They reuse the spawn-handle harness from ``test_agent_server_e2e`` (real
``_build_runtime`` with the provider stubbed — no network).
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from src.server.agent_server import AgentServerConfig, make_spawn_agent
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    enqueue_pending_notification,
)
from src.workflow.ultracode import is_ultracode_session, reset_ultracode
from tests.server.test_agent_server_e2e import (
    _RECORDED_TURNS,
    _patches,
    _RecordingProvider,
    _TextProvider,
    _wait_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _workflow_state_hygiene(monkeypatch):
    """The ultracode session flag and the notification queue are process-global;
    isolate every test from its neighbors (and from the developer's env)."""
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    reset_ultracode()
    clear_pending_notifications()
    yield
    reset_ultracode()
    clear_pending_notifications()


@contextlib.asynccontextmanager
async def _spawned(tmp_path, provider_cls, config: AgentServerConfig | None = None):
    """A live agent handle (worker running) + its message generator."""
    from src.tool_system.registry import ToolRegistry

    with contextlib.ExitStack() as stack:
        for p in _patches(provider_cls, ToolRegistry([])):
            stack.enter_context(p)
        spawn = make_spawn_agent(config or AgentServerConfig())
        handle = await spawn("wf_test", str(tmp_path), None)
        gen = handle.messages_from_agent()
        init = await asyncio.wait_for(gen.__anext__(), timeout=10)
        assert init["subtype"] == "init"
        try:
            yield handle, gen
        finally:
            await handle.shutdown()
            with contextlib.suppress(Exception):
                await gen.aclose()


async def _control(handle, gen, rid: str, request: dict) -> dict:
    """Send one control_request and return its reply payload."""
    await handle.send_to_agent({"type": "control_request", "request_id": rid, "request": request})
    for _ in range(20):
        msg = await asyncio.wait_for(gen.__anext__(), timeout=5)
        if msg.get("type") == "control_response" and msg["response"].get("request_id") == rid:
            return msg["response"]["response"]
    raise AssertionError(f"no reply for {rid}")


def _session_of(handle):
    """The underlying ``_AgentSession`` (send_to_agent is a bound method)."""
    return handle.send_to_agent.__self__


def _last_user_message(turn: str) -> str:
    """Last message of a ``_RecordingProvider`` turn record (`` || ``-joined)."""
    return turn.split(" || ")[-1]


# ─── ultracode keyword injection ──────────────────────────────────────────────


async def test_ultracode_keyword_appends_reminder(tmp_path):
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "ultracode: build a report tool"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 1)
        turn = _last_user_message(_RECORDED_TURNS[0])
        assert "ultracode: build a report tool" in turn
        assert "WRITE a reusable" in turn, "keyword did not append the authoring reminder"

        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "just a plain question"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 2)
        assert "WRITE a reusable" not in _last_user_message(_RECORDED_TURNS[1])


async def test_ultracode_session_mode_reminds_every_turn(tmp_path):
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r == {"ok": True, "effort": "ultracode", "ultracode": True}
        assert is_ultracode_session()

        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "refactor the parser"}}
        )
        assert await _wait_for(lambda: len(_RECORDED_TURNS) == 1)
        assert "Ultracode is on for this session" in _last_user_message(_RECORDED_TURNS[0])


# ─── set_effort: ultracode + levels + gating ──────────────────────────────────


async def test_set_effort_levels_and_ultracode_roundtrip(tmp_path):
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)

        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is True and r["effort"] == "ultracode"
        assert is_ultracode_session()
        assert sess._effort is None, "ultracode must not touch the reasoning level"

        # A real level exits ultracode mode.
        r = await _control(handle, gen, "e2", {"subtype": "set_effort", "effort": "high"})
        assert r == {"ok": True, "effort": "high", "ultracode": False}
        assert not is_ultracode_session()
        assert sess._effort == "high"

        # Bare /effort is a read-only report (no clearing).
        r = await _control(handle, gen, "e3", {"subtype": "set_effort"})
        assert r == {"ok": True, "effort": "high", "ultracode": False}
        assert sess._effort == "high"

        # Explicit auto clears the level (and would exit ultracode mode).
        r = await _control(handle, gen, "e4", {"subtype": "set_effort", "effort": "auto"})
        assert r == {"ok": True, "effort": "default", "ultracode": False}
        assert sess._effort is None

        # Unknown value → error, nothing mutated.
        r = await _control(handle, gen, "e5", {"subtype": "set_effort", "effort": "bogus"})
        assert r["ok"] is False and "invalid effort" in r["error"]


async def test_set_effort_ultracode_gated_when_workflows_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is False and "disabled" in r["error"]
        assert not is_ultracode_session()


# ─── workflows control (the /workflows report) ────────────────────────────────


async def test_workflows_control_reports_runs(tmp_path):
    from types import SimpleNamespace

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "w1", {"subtype": "workflows"})
        assert r["ok"] is True and "No workflow runs" in r["text"]

        # Seed a run into the session's live registry — same object the
        # Workflow tool records into.
        sess = _session_of(handle)
        sess.tool_context.runtime_tasks.upsert(
            SimpleNamespace(
                id="local_workflow_9",
                type="local_workflow",
                status="running",
                workflow_name="deep-research",
                run_id="wf_seed01",
                progress=None,
            )
        )
        r = await _control(handle, gen, "w2", {"subtype": "workflows"})
        assert r["ok"] is True
        assert "deep-research  [running]" in r["text"]
        assert "(run: wf_seed01)" in r["text"]


async def test_workflows_control_gated_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "w1", {"subtype": "workflows"})
        assert r["ok"] is False and "disabled" in r["error"]


# ─── workflow command catalog + dispatch ──────────────────────────────────────

_SAVED_WF = 'meta = {"name": "triage", "description": "Sort issues", "phases": []}\nreturn 1\n'


async def test_list_workflow_commands_includes_bundled_and_saved(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "triage.py").write_text(_SAVED_WF, encoding="utf-8")

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "l1", {"subtype": "list_workflow_commands"})
        assert r["ok"] is True
        by_name = {c["name"]: c for c in r["commands"]}
        assert "deep-research" in by_name  # bundled
        assert by_name["triage"]["description"] == "Sort issues"
        # The interactive /workflows viewer is NOT a prompt command.
        assert "workflows" not in by_name


async def test_list_workflow_commands_empty_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(handle, gen, "l1", {"subtype": "list_workflow_commands"})
        assert r["ok"] is True and r["commands"] == []


async def test_workflow_command_expands_directive(tmp_path):
    wf_dir = tmp_path / ".clawcodex" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "triage.py").write_text(_SAVED_WF, encoding="utf-8")

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(
            handle, gen, "d1",
            {"subtype": "workflow_command", "name": "triage", "args": "the open bug list"},
        )
        assert r["ok"] is True
        assert str(wf_dir / "triage.py") in r["prompt"]
        assert "the open bug list" in r["prompt"], "$ARGUMENTS was not substituted"
        assert "$ARGUMENTS" not in r["prompt"]
        assert r["notice"] == "⚡ launching workflow /triage"

        r = await _control(
            handle, gen, "d2", {"subtype": "workflow_command", "name": "nope", "args": ""}
        )
        assert r["ok"] is False and "unknown workflow command" in r["error"]

        r = await _control(handle, gen, "d3", {"subtype": "workflow_command"})
        assert r["ok"] is False


async def test_workflow_command_gated_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        r = await _control(
            handle, gen, "d1", {"subtype": "workflow_command", "name": "deep-research", "args": "x"}
        )
        assert r["ok"] is False and "disabled" in r["error"]


# ─── task-notification delivery (worker loop drain) ───────────────────────────

_WF_ENVELOPE = (
    "<task-notification><task-id>local_workflow_7</task-id>"
    "<status>completed</status><summary>Workflow deep-research completed</summary>"
    "<output-file>/tmp/wf_7.jsonl</output-file>"
    "<result>saved to /tmp/report.md</result></task-notification>"
)
_AGENT_ENVELOPE = (
    "<task-notification><task-id>local_agent_3</task-id>"
    "<status>completed</status><summary>Background agent finished: map the auth module</summary>"
    "<result>see notes</result></task-notification>"
)


async def test_notification_drain_emits_banner_and_summary_turn(tmp_path):
    _RECORDED_TURNS.clear()
    frames: list[dict] = []
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        async def _collect():
            with contextlib.suppress(Exception):
                async for msg in gen:
                    frames.append(msg)

        collector = asyncio.get_running_loop().create_task(_collect())
        try:
            enqueue_pending_notification(value=_WF_ENVELOPE)
            enqueue_pending_notification(value=_AGENT_ENVELOPE)

            # Worker's idle poll (0.5s) drains both → 2 banners + ONE turn.
            assert await _wait_for(
                lambda: len([f for f in frames if f.get("subtype") == "task_notification"]) == 2,
                timeout=10,
            ), "banner frames not emitted"
            banners = [f for f in frames if f.get("subtype") == "task_notification"]
            assert banners[0]["type"] == "system"
            assert banners[0]["task_id"] == "local_workflow_7"
            assert "✔ Workflow deep-research completed" in banners[0]["message"]
            assert "run journal → /tmp/wf_7.jsonl" in banners[0]["message"]
            # The agent envelope banners as its own summary — not as "workflow".
            assert banners[1]["task_id"] == "local_agent_3"
            assert "map the auth module" in banners[1]["message"]

            # Both envelopes are delivered (normally as ONE batched turn; a
            # worker poll landing between the two enqueues may split them —
            # assert delivery across all turns rather than the batch shape).
            assert await _wait_for(
                lambda: "local_workflow_7" in "".join(_RECORDED_TURNS)
                and "local_agent_3" in "".join(_RECORDED_TURNS),
                timeout=10,
            ), "both envelopes must be delivered to the model"
            assert "background tasks you launched have finished" in _RECORDED_TURNS[0]
            # The summarization turn also streams a normal result frame.
            assert await _wait_for(
                lambda: any(f.get("type") == "result" for f in frames), timeout=10
            )
        finally:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector


async def test_notification_turn_is_internal_no_ultracode_reminder(tmp_path):
    """Session-mode ultracode must not decorate system-generated turns — an
    envelope can never trigger workflow authoring."""
    _RECORDED_TURNS.clear()
    async with _spawned(tmp_path, _RecordingProvider) as (handle, gen):
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "ultracode"})
        assert r["ok"] is True

        enqueue_pending_notification(value=_WF_ENVELOPE)
        assert await _wait_for(lambda: len(_RECORDED_TURNS) >= 1, timeout=10)
        turn = _last_user_message(_RECORDED_TURNS[0])
        assert "background tasks you launched have finished" in turn
        assert "Ultracode is on for this session" not in turn

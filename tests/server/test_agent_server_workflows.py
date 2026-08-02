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


async def test_set_effort_accepts_the_full_claude_ladder(tmp_path):
    """``xhigh``/``max`` are real Claude levels and must be settable here.

    They were rejected by a hardcoded ``(minimal|low|medium|high)`` list,
    so ``/effort xhigh`` failed in the interactive TUI while the same value
    worked via ``--effort``, ``/effort`` on the other surfaces, and
    settings.effort. Both probed on claude-opus-5 2026-07-25. The ladder is
    now exactly VALID_EFFORT_VALUES — see
    test_set_effort_rejects_minimal_on_the_claude_ladder for the one value
    deliberately NOT carried over.
    """
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)
        for i, level in enumerate(("xhigh", "max", "low", "medium", "high")):
            r = await _control(
                handle, gen, f"l{i}", {"subtype": "set_effort", "effort": level}
            )
            assert r == {"ok": True, "effort": level, "ultracode": False}, level
            assert sess._effort == level, level

        # The error text must enumerate what is actually accepted, so a
        # rejected value tells the user the real ladder.
        r = await _control(handle, gen, "bad", {"subtype": "set_effort", "effort": "bogus"})
        assert r["ok"] is False
        for level in ("low", "medium", "high", "xhigh", "max"):
            assert level in r["error"], f"{level} missing from {r['error']!r}"


async def test_effort_routing_matches_the_provider_wire_shape(tmp_path):
    """Anthropic takes ``output_config.effort``; OpenAI-compat takes a body field.

    Sending the OpenAI shape to Anthropic is a hard 400 (probed 2026-07-25:
    ``reasoning_effort: Extra inputs are not permitted``), which used to
    break every request after a ``/effort`` in an interactive Anthropic
    session.

    That split now lives ENTIRELY at the wire boundary in
    ``query.py::_call_model_sync`` (covered by
    tests/test_query_openai_compat_effort.py, which asserts the actual kwargs
    each family receives). Routing's own job shrank to "hand the level over,
    unwrapped, for both families" — so that is what this pins.

    It used to wrap OpenAI-compat providers in an ``_EffortProvider`` and
    return ``thinking_effort=None``. Once query.py learned to emit
    ``reasoning_effort`` itself, the two injection sites collided: query.py
    filled ``extra_body`` from ``settings.effort`` first and the wrapper's
    ``setdefault`` no-op'd, so an explicit ``/effort`` was silently discarded
    in favour of the persisted setting. The wrapper was deleted; asserting the
    provider comes back UNWRAPPED is what keeps a second injection site from
    reappearing.
    """
    from unittest.mock import MagicMock

    from src.providers.anthropic_provider import AnthropicProvider

    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)

        # No effort set → untouched provider, no thinking_effort.
        sess._effort = None
        assert sess._turn_effort_routing() == (sess.provider, None)

        # Anthropic → the real provider plus the level; query.py turns it
        # into output_config.effort.
        sess.provider = AnthropicProvider(api_key="sk-test", model="claude-opus-5")
        sess._effort = "xhigh"
        provider, thinking_effort = sess._turn_effort_routing()
        assert provider is sess.provider
        assert thinking_effort == "xhigh"

        # OpenAI-compatible → same shape. The provider must NOT be wrapped:
        # query.py is the single injection site for reasoning_effort.
        sess.provider = MagicMock(name="openai-compat")
        provider, thinking_effort = sess._turn_effort_routing()
        assert provider is sess.provider
        assert thinking_effort == "xhigh"


async def test_effort_reaches_the_query_loop_kwarg(tmp_path, monkeypatch):
    """The seam that actually delivers interactive effort to the wire.

    ``_turn_effort_routing`` returning ``"xhigh"`` is inert unless the turn
    passes it to ``run_query_as_agent_loop`` as ``thinking_effort`` — that
    single kwarg is what ``resolve_thinking_effort`` turns into
    ``output_config.effort``. Deleting it leaves every other effort test
    green, so spy on the call itself.

    Two harness details this test exists to encode: the spy must be a
    coroutine function (the worker invokes the loop via ``asyncio.run(...)``,
    which rejects an async generator with ``ValueError: a coroutine was
    expected``), and it must be patched at its SOURCE module — the worker
    imports it locally inside ``_run_turn`` (agent_server.py:3393), so
    ``src.server.agent_server.run_query_as_agent_loop`` does not exist as a
    module attribute to patch.
    """
    seen: dict = {}

    from src.providers.anthropic_provider import AnthropicProvider
    from src.utils.abort_controller import AbortError

    class _AnthropicShaped(AnthropicProvider):
        """Real Anthropic class (so is_anthropic_wire is True), never called
        over the network — the spy replaces the whole query loop."""

        def __init__(self, *args, **kwargs):
            super().__init__(api_key="sk-test", model="claude-opus-5")

    async def _spy(*args, **kwargs):
        seen.update(kwargs)
        raise AbortError()  # unwind the turn cleanly, no envelope assertions

    async with _spawned(tmp_path, _AnthropicShaped) as (handle, gen):
        sess = _session_of(handle)
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "xhigh"})
        assert r["ok"] is True

        monkeypatch.setattr(
            "src.query.agent_loop_compat.run_query_as_agent_loop", _spy
        )
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "hi"}}
        )
        assert await _wait_for(lambda: "thinking_effort" in seen), f"loop not called: {seen!r}"

    assert seen["thinking_effort"] == "xhigh", (
        "interactive /effort must arrive as thinking_effort — without it the "
        "level never becomes output_config.effort"
    )


async def test_launch_effort_flag_seeds_the_session(tmp_path):
    """``--effort`` must apply interactively, not only on headless ``-p``.

    The flag was plumbed solely into HeadlessOptions, so
    ``clawcodex --model claude-opus-5 --effort xhigh`` (no ``-p``) parsed it
    and silently discarded it. It now rides AgentServerConfig into the
    session's ``/effort`` level.
    """
    config = AgentServerConfig(effort="xhigh")
    async with _spawned(tmp_path, _TextProvider, config) as (handle, gen):
        sess = _session_of(handle)
        assert sess._effort == "xhigh"
        # The point here is only that the launch flag SEEDED a level at all;
        # routing hands it over unwrapped for either family, and the
        # per-family wire shape is pinned by
        # test_effort_routing_matches_the_provider_wire_shape.
        provider, thinking_effort = sess._turn_effort_routing()
        assert provider is sess.provider and thinking_effort == "xhigh"

        # A later /effort still wins over the launch flag, and auto clears.
        r = await _control(handle, gen, "e1", {"subtype": "set_effort", "effort": "low"})
        assert r["effort"] == "low" and sess._effort == "low"
        r = await _control(handle, gen, "e2", {"subtype": "set_effort", "effort": "auto"})
        assert r["effort"] == "default" and sess._effort is None


@pytest.mark.parametrize("seed", ["minimal", "bogus", "", "  ", 5, True])
async def test_launch_effort_flag_ignores_off_ladder_values(tmp_path, seed):
    """An off-ladder ``--effort`` must be dropped, not seeded verbatim.

    Seeding it would resurrect the trap ``_do_set_effort`` rejects: a value
    outside VALID_THINKING_EFFORT_LEVELS makes resolve_thinking_effort fall
    back to settings.effort, so ``--effort minimal`` could put ``max`` on
    the wire while the init frame's badge showed "minimal". Validated at the
    seed rather than only in argparse because --stdio/--print-connect
    callers reach AgentServerConfig without passing a parser — which is also
    why non-str values are covered: a ``.strip()`` on an int would raise
    inside _build_runtime, and that turns into init_error, failing the whole
    session over a cosmetic setting.
    """
    async with _spawned(tmp_path, _TextProvider, AgentServerConfig(effort=seed)) as (
        handle,
        gen,
    ):
        assert _session_of(handle)._effort is None


async def test_launch_effort_flag_is_normalized(tmp_path):
    """Case is normalized at the seed, matching /effort's ``.lower()``.

    The OpenAI-compat wire sends the level verbatim as ``reasoning_effort``
    (query.py injects it at the wire boundary), so an unnormalized "MAX"
    would go out as-is.
    """
    async with _spawned(tmp_path, _TextProvider, AgentServerConfig(effort=" MAX ")) as (
        handle,
        gen,
    ):
        assert _session_of(handle)._effort == "max"


async def test_set_effort_rejects_minimal_on_the_claude_ladder(tmp_path):
    """``minimal`` is a GPT-5 level, and accepting it here was a trap.

    It is absent from VALID_THINKING_EFFORT_LEVELS, so on the Anthropic path
    ``resolve_thinking_effort`` would treat it as "nothing requested" and
    silently substitute ``settings.effort`` — ``/effort minimal`` could emit
    ``max`` while the TUI echoed "minimal".
    """
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        sess = _session_of(handle)
        r = await _control(handle, gen, "m1", {"subtype": "set_effort", "effort": "minimal"})
        assert r["ok"] is False
        assert "minimal" not in r["error"].split("(")[-1], r["error"]
        assert sess._effort is None


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


class _EmptyProvider:
    """Always returns a degenerate turn: no text, no tool calls.

    Drives the agent loop into ``Terminal(reason="empty_response")`` after the
    empty-turn retry budget is spent — a real early stop, produced by the real
    loop, rather than a stubbed terminal.
    """

    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = model or "fake"

    def chat(self, messages, tools=None, **kw):
        from src.providers.base import ChatResponse

        return ChatResponse(
            content="",
            model=self.model,
            usage={"input_tokens": 3, "output_tokens": 0},
            finish_reason="stop",
            tool_uses=None,
        )

    def chat_stream_response(self, *a, **kw):  # force fallback to chat()
        raise NotImplementedError


async def test_turn_outcome_reflects_an_early_stop(tmp_path):
    """A turn the agent loop cut short must not be reported as a success.

    ``_run_turn`` hardcoded ``subtype="success"`` for the post-loop outcome
    regardless of why the loop stopped, so on the TUI / VS Code path a
    guard-killed or empty turn was indistinguishable from a completed one.
    Three consumers gate on this field: ``_maybe_judge_goal`` fed such a turn
    to the /goal judge as evidence of progress, ``_maybe_review_memories``
    learned from it, and the cron loop rearmed on it.

    BEHAVIOURAL on purpose. The first version of this test asserted on
    ``inspect.getsource`` strings and passed against an implementation that
    computed the right subtype and then discarded it — source archaeology
    cannot tell "derives the subtype" from "derives it and throws it away".
    """
    async with _spawned(tmp_path, _EmptyProvider) as (handle, gen):
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "go"}}
        )
        result = None
        for _ in range(60):
            msg = await asyncio.wait_for(gen.__anext__(), timeout=15)
            if msg.get("type") == "result":
                result = msg
                break
        assert result is not None, "no result frame emitted"
        assert result["subtype"] == "error_during_execution", (
            f"an early stop must not report success; got {result['subtype']!r}"
        )
        assert result["is_error"] is True
        assert "empty response" in str(result.get("result", "")).lower(), (
            "the explanation must reach the caller, not an empty string"
        )


async def test_a_normal_turn_still_reports_success(tmp_path):
    """The default path must be untouched."""
    async with _spawned(tmp_path, _TextProvider) as (handle, gen):
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "hi"}}
        )
        result = None
        for _ in range(60):
            msg = await asyncio.wait_for(gen.__anext__(), timeout=15)
            if msg.get("type") == "result":
                result = msg
                break
        assert result is not None
        assert result["subtype"] == "success"
        assert result["is_error"] is False


async def test_early_stop_map_excludes_the_finished_reasons(tmp_path):
    """The map is the single source both surfaces read, so pin its shape:
    reasons meaning "the model finished" must be absent so they fall through
    to the success default."""
    from src.query.transitions import EARLY_STOP_SUBTYPES

    for reason in ("completed", "hook_stopped", "stop_hook_prevented"):
        assert reason not in EARLY_STOP_SUBTYPES, reason
    for subtype in EARLY_STOP_SUBTYPES.values():
        assert subtype != "success"


def test_early_stop_goal_verdict_is_continue_and_consumes_budget():
    """A /goal loop must AUTO-CONTINUE past a cut-short turn, not die.

    Two halves, and the second is what keeps it safe:

    1. The turn is not judged — there is no output to weigh, and feeding the
       "[Stopped: …]" sentinel to the judge is how a cut-short turn gets
       mistaken for progress. A synthetic ``continue`` stands in, which is the
       same fail-open verdict ``judge_goal`` returns on its own errors.
    2. It still routes through ``apply_verdict``, so ``turns_used`` ticks and
       the goal's own cap bounds it. Enqueuing a continuation directly would
       let a turn that keeps stopping early retry forever.

    Regression context: before the turn subtype was derived from the terminal
    reason, a max_turns turn reached the judge as "success", was judged
    not-done, and the loop retried. Skipping it outright would have silently
    killed loops that used to recover.
    """
    from src.goals import GoalManager

    mgr = GoalManager("wf_test", judge=None, default_max_turns=3)
    mgr.set("make the tests pass")

    seen = []
    for _ in range(6):
        if not mgr.is_active():
            break
        decision = mgr.apply_verdict(
            "continue",
            "the last turn stopped early (error_during_execution) and "
            "produced no result to evaluate",
            False,
        )
        seen.append(decision["should_continue"])

    assert seen and seen[0] is True, (
        "an early stop must continue the goal loop, not end it"
    )
    assert False in seen or not mgr.is_active(), (
        "the loop must still be bounded by the goal's turn cap — an "
        "always-early-stopping turn cannot retry forever"
    )
    assert len(seen) <= 4, f"cap not enforced: {len(seen)} iterations"


def test_early_stop_enqueues_a_goal_continuation():
    """BEHAVIOURAL: drive ``_maybe_continue_goal`` itself with a cut-short turn.

    The contract test above pins that ``apply_verdict("continue")`` continues
    and stays bounded, but it would still pass if ``_maybe_continue_goal``
    bailed out before ever producing that verdict — which is exactly what the
    first version of this change did. This asserts the continuation actually
    lands in the inbox, and that NO judge was consulted (there is no output to
    judge, and asking about a "[Stopped: …]" sentinel invites a wrong answer).

    Driven against a bare session with the I/O collaborators stubbed rather
    than the ``_spawned`` harness: that one runs a live worker thread which
    CONSUMES the inbox, so asserting on the queue would race it.
    """
    import queue
    import threading

    from src.goals import GoalManager
    from src.server import agent_server as srv

    sess = object.__new__(srv._AgentSession)
    sess._lock = threading.RLock()
    sess._inbox = queue.Queue()
    sess.session_id = "s"
    sess.provider = None
    sess.session = None
    sess._emit = lambda *a, **k: None
    sess._save_session = lambda: None
    sess._goal_snapshot_locked = lambda: (None, 0)
    sess._goal_mgr = GoalManager("s", judge=None, default_max_turns=5)
    sess._goal_mgr.set("make the tests pass")

    judged = []
    sess._goal_mgr.judge = lambda *a, **k: judged.append(a) or "DONE"
    # ``_goal_manager()`` rebinds ``.judge`` on every call (so a mid-goal
    # /model switch is picked up), which would clobber the stub above.
    sess._goal_manager = lambda: sess._goal_mgr

    srv._AgentSession._maybe_continue_goal(
        sess,
        {
            "subtype": "error_during_execution",
            "response_text": "[Stopped: repeated tool failures detected]",
        },
    )

    assert not judged, (
        "a cut-short turn must not be handed to the judge as evidence"
    )
    queued = []
    while not sess._inbox.empty():
        queued.append(sess._inbox.get_nowait())
    assert any(isinstance(i, dict) and i.get("__goal__") for i in queued), (
        "the goal loop must AUTO-CONTINUE past an early stop, not die "
        f"silently; inbox={queued!r}"
    )


def test_a_normal_turn_still_reaches_the_goal_judge():
    """The default path must be untouched: a real answer IS judged."""
    import queue
    import threading

    from src.goals import GoalManager
    from src.server import agent_server as srv

    sess = object.__new__(srv._AgentSession)
    sess._lock = threading.RLock()
    sess._inbox = queue.Queue()
    sess.session_id = "s"
    sess.provider = None
    sess.session = None
    sess._emit = lambda *a, **k: None
    sess._save_session = lambda: None
    sess._goal_snapshot_locked = lambda: (None, 0)
    sess._goal_mgr = GoalManager("s", judge=None, default_max_turns=5)
    sess._goal_mgr.set("make the tests pass")

    judged = []

    def _judge(*a, **k):
        judged.append(a)
        return "DONE"

    sess._goal_mgr.judge = _judge
    sess._goal_manager = lambda: sess._goal_mgr
    srv._AgentSession._maybe_continue_goal(
        sess, {"subtype": "success", "response_text": "I fixed the tests."}
    )
    assert judged, "a completed turn must still be judged"


def _bare_goal_session(max_turns: int = 3):
    """A session with only the I/O collaborators stubbed.

    NOT the ``_spawned`` harness: that runs a live worker which CONSUMES
    ``_inbox``, so queue assertions race it.
    """
    import queue
    import threading

    from src.goals import GoalManager
    from src.server import agent_server as srv

    sess = object.__new__(srv._AgentSession)
    sess._lock = threading.RLock()
    sess._inbox = queue.Queue()
    sess.session_id = "s"
    sess.provider = None
    sess.session = None
    sess._emit = lambda *a, **k: None
    sess._save_session = lambda: None
    sess._goal_snapshot_locked = lambda: (None, 0)
    sess._goal_mgr = GoalManager("s", judge=None, default_max_turns=max_turns)
    sess._goal_mgr.set("make the tests pass")
    sess._goal_manager = lambda: sess._goal_mgr
    return sess, srv


def test_early_stop_continuations_are_bounded_by_the_goal_cap():
    """A turn that keeps stopping early must NOT retry forever.

    THE safety property of the auto-continue design, and the one my own
    mutation testing missed: forcing ``should_continue = True`` after
    ``apply_verdict`` (bypassing the ``turns_used`` tick) passed every other
    test in this file, because they assert only that *something* was enqueued
    — which a direct enqueue satisfies just as well.

    This drives the real ``_maybe_continue_goal`` repeatedly against a cap of
    3 and asserts the continuations actually STOP. It fails if the budget tick
    is bypassed.
    """
    sess, srv = _bare_goal_session(max_turns=3)
    outcome = {
        "subtype": "error_during_execution",
        "response_text": "[Stopped: repeated tool failures detected]",
    }

    continuations = 0
    for _ in range(10):
        srv._AgentSession._maybe_continue_goal(sess, outcome)
        drained = 0
        while not sess._inbox.empty():
            sess._inbox.get_nowait()
            drained += 1
        if not drained:
            break
        continuations += 1

    assert continuations >= 1, "an early stop must continue the goal at least once"
    assert continuations < 10, (
        f"the goal cap must bound early-stop retries; got {continuations} "
        "continuations — the budget tick is being bypassed"
    )
    assert not sess._goal_mgr.is_active(), (
        "the goal must end once its turn budget is spent"
    )


def test_user_cancel_and_provider_error_do_not_continue_the_goal():
    """``cancelled`` / ``error`` are the USER or the PROVIDER ending the turn,
    not the harness cutting it short — so they must END the goal loop, not
    retry it. A blanket ``!= "success"`` made ESC during a /goal loop
    re-enqueue the work the user had just killed, and made a provider 5xx
    retry up to the whole turn budget.

    Paired with a POSITIVE control below, because this harness swallows
    exceptions: a "nothing was enqueued" assertion would otherwise pass for
    the wrong reason if a stub were missing.
    """
    for subtype in ("cancelled", "error"):
        sess, srv = _bare_goal_session()
        srv._AgentSession._maybe_continue_goal(
            sess, {"subtype": subtype, "response_text": ""}
        )
        assert sess._inbox.empty(), f"{subtype} must not continue the goal"

    # POSITIVE CONTROL: the same harness DOES enqueue for a loop early stop,
    # so the assertions above are meaningful rather than vacuous.
    sess, srv = _bare_goal_session()
    srv._AgentSession._maybe_continue_goal(
        sess,
        {"subtype": "error_during_execution", "response_text": "[Stopped: x]"},
    )
    assert not sess._inbox.empty(), (
        "control failed — the harness enqueues nothing at all, so the "
        "negative assertions above prove nothing"
    )


def test_early_stop_respects_the_verdict_decision():
    """The continuation must come FROM ``apply_verdict``, not around it.

    THE test the shipped defect needed. A stray

        if early_stop:
            should_continue = True

    after ``apply_verdict`` was committed by accident and reached main. It is
    not a runaway — ``apply_verdict`` still runs before it, so ``turns_used``
    still ticks and the goal still deactivates at its cap (measured: 3
    continuations instead of 2 at cap=3). That is exactly why a cap test does
    NOT catch it, and why asserting "something was enqueued" does not either.

    What it does break is authority: the goal's own decision to stop is
    overridden, buying one extra model turn past the budget. So assert the
    negative directly — when the verdict says stop, nothing is enqueued.
    """
    sess, srv = _bare_goal_session()

    real_apply = sess._goal_mgr.apply_verdict

    def _stop(*a, **k):
        decision = dict(real_apply(*a, **k))
        decision["should_continue"] = False
        return decision

    sess._goal_mgr.apply_verdict = _stop
    srv._AgentSession._maybe_continue_goal(
        sess,
        {"subtype": "error_during_execution", "response_text": "[Stopped: x]"},
    )
    assert sess._inbox.empty(), (
        "a continuation was enqueued despite apply_verdict saying stop — the "
        "verdict is being overridden rather than honoured"
    )

    # POSITIVE CONTROL: the same harness DOES enqueue when the verdict allows,
    # so the assertion above cannot pass vacuously.
    sess2, _ = _bare_goal_session()
    srv._AgentSession._maybe_continue_goal(
        sess2,
        {"subtype": "error_during_execution", "response_text": "[Stopped: x]"},
    )
    assert not sess2._inbox.empty(), "control failed — nothing ever enqueues"


class _TieredCacheProvider:
    """Many small requests whose cache reads only cross a tier once summed.

    Each request is ~15.4K prompt tokens — far below gpt-5.6-luna's 272K
    long-context threshold — but the loop's cumulative total reaches 385K.
    """

    PER_REQUEST_USAGE = {
        "input_tokens": 400,
        "output_tokens": 300,
        "cache_read_input_tokens": 15_000,
        "cache_creation_input_tokens": 0,
    }
    TURNS = 25

    def __init__(self, api_key=None, base_url=None, model=None):
        self.model = "openai/gpt-5.6-luna"
        self._calls = 0

    def chat(self, messages, tools=None, **kw):
        from src.providers.base import ChatResponse

        self._calls += 1
        last = self._calls >= self.TURNS
        return ChatResponse(
            content="done" if last else "thinking",
            model=self.model,
            usage=dict(self.PER_REQUEST_USAGE),
            finish_reason="end_turn" if last else "tool_use",
            tool_uses=None
            if last
            else [
                {
                    "id": f"tool_{self._calls}",
                    "name": "Bash",
                    "input": {"command": "true", "description": "noop"},
                }
            ],
        )

    def chat_stream_response(self, *a, **kw):  # force fallback to chat()
        raise NotImplementedError


async def test_turn_cost_is_not_priced_from_the_cumulative_usage(tmp_path):
    """A long loop must not be billed at the long-context rate.

    ``get_pricing`` picks a tier from a PER-REQUEST threshold, but
    ``result.usage`` is the sum across every loop turn. Once the cumulative
    dict carried cache reads — which sum to roughly turns x conversation
    size — pricing it crossed a boundary no single request came near, and
    this turn billed at 1.76x its true cost.

    The cost is now a delta of ``cost_tracker``'s running total, which prices
    each response as it arrives with the tier chosen from that one request.

    BEHAVIOURAL: asserted on the emitted ``total_cost_usd``, because the
    failure mode is a plausible-looking number rather than an error.
    """
    from src.services.pricing import compute_cost

    per_request = _TieredCacheProvider.PER_REQUEST_USAGE
    turns = _TieredCacheProvider.TURNS
    model = "openai/gpt-5.6-luna"

    truth = sum(compute_cost(model, per_request) for _ in range(turns))
    aggregate = {k: v * turns for k, v in per_request.items()}
    if_priced_as_aggregate = compute_cost(model, aggregate)
    assert if_priced_as_aggregate > truth * 1.5, "fixture must cross the tier"

    async with _spawned(tmp_path, _TieredCacheProvider) as (handle, gen):
        await handle.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": "go"}}
        )
        result = None
        for _ in range(200):
            msg = await asyncio.wait_for(gen.__anext__(), timeout=20)
            if msg.get("type") == "result":
                result = msg
                break

    assert result is not None, "no result frame emitted"
    reported = float(result.get("total_cost_usd") or 0.0)
    assert reported > 0, "the turn reported no cost at all"
    assert reported < if_priced_as_aggregate * 0.8, (
        f"turn billed at the long-context rate: {reported} vs aggregate "
        f"{if_priced_as_aggregate} (true {truth})"
    )

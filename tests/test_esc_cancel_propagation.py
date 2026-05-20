"""Regression tests for ESC cancellation propagating into subagents.

Before this fix, the AgentBridge / QueryEngine wired their AbortController
into the agent loop's ``cancel_signal`` parameter but never assigned it to
``tool_context.abort_controller``. As a consequence:

* Long-running tools that read ``context.abort_controller`` (Bash, Agent
  subagents, the streaming executor, tool hooks) saw ``None`` and ran to
  completion regardless of ESC.
* Sync Agent subagents in particular created a *fresh* AbortController
  inside ``run_agent`` (because the parent's was None) — the user's ESC
  press had nowhere to land, so subagents could chew through dozens of
  tool calls before naturally finishing.

These tests pin the new behaviour:

* The agent loop re-raises ``AbortError`` rather than burying it in a
  synthetic tool-result message.
* A cancel that fires *during* a tool dispatch is noticed before the
  next tool call (or the next API turn) starts.
* The TUI bridge and the REPL query engine both plumb the controller
  onto the tool context, so subagents and other downstream tools see
  the same signal the UI tripped.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.providers.base import ChatResponse
from src.query.agent_loop_compat import run_query_as_agent_loop_sync as run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.tool_system.build_tool import build_tool
from src.utils.abort_controller import AbortController, AbortError


from src.providers.anthropic_provider import AnthropicProvider as _RealAnthropicProvider


class _FakeAnthropic(_RealAnthropicProvider):
    """Stub provider that genuinely IS an AnthropicProvider subclass.

    Stage-4 critic S1: the previous monkeypatch approach
    (``monkeypatch.setattr(query_mod, "AnthropicProvider", ...)``)
    was silently broken — query.py imports AnthropicProvider at
    function-level scope, so the module-level setattr never
    shadowed the local binding. The isinstance check still saw the
    real class. Subclassing the real class makes
    ``isinstance(_FakeAnthropic(), AnthropicProvider)`` True
    naturally without any patching.
    """

    def __init__(self, responses: list[ChatResponse]):
        # Skip the real ``__init__`` (it constructs an SDK client we
        # don't need). Set the fields query.py reads on the provider.
        self._responses = list(responses)
        self.calls = 0
        self.api_key = "fake"
        self.base_url = None
        self.model = "claude-test"

    def chat_stream_response(self, *_args: Any, **_kwargs: Any) -> ChatResponse:
        raise NotImplementedError  # force the non-stream path

    def chat(self, *_args: Any, **_kwargs: Any) -> ChatResponse:
        self.calls += 1
        if not self._responses:
            return ChatResponse(
                content="",
                model="test",
                usage={"input_tokens": 0, "output_tokens": 0},
                finish_reason="end_turn",
                tool_uses=None,
            )
        return self._responses.pop(0)


def _patch_anthropic_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op kept for back-compat with the original test bodies.

    Pre-Stage-4 this monkeypatched ``agent_loop._is_anthropic_provider``
    to make a non-subclass fake look Anthropic-shape. Post-Stage-4,
    ``_FakeAnthropic`` IS an AnthropicProvider subclass (see above),
    so the isinstance check in ``query._call_model_sync`` returns
    True naturally and no patching is needed. Kept callable so the
    existing test bodies don't need editing.
    """
    return


def _make_context(workspace: Path) -> ToolContext:
    return ToolContext(workspace_root=workspace)


def _build_registry_with_blocking_tool(
    block_event: threading.Event,
    abort_check: dict[str, bool],
    entered_event: threading.Event,
) -> ToolRegistry:
    """Build a registry with a single ``Slow`` tool that polls its abort signal.

    The tool sets ``entered_event`` on its first poll so the test thread
    can wait for tool entry *before* tripping the abort — a deterministic
    handshake replaces the previous sleep-based race. The tool then
    blocks until either (a) the abort signal fires, or (b) ``block_event``
    is set explicitly by the test. Recording into ``abort_check`` lets us
    assert that the tool actually observed the signal coming from
    ``context.abort_controller`` (rather than completing on its own).
    """

    def _call(_input: dict[str, Any], context: ToolContext) -> ToolResult:
        controller = getattr(context, "abort_controller", None)
        signal = getattr(controller, "signal", None) if controller else None
        abort_check["controller_present"] = controller is not None
        entered_event.set()
        # Spin until aborted or the escape hatch trips. A real tool would
        # poll its inner work loop the same way the Bash supervisor does
        # in ``_run_bash_with_abort``. The 2s escape hatch turns a stuck
        # test into a loud assertion failure rather than a hang, so a
        # regression is still visible in CI logs.
        for _ in range(200):  # ~2 seconds at 10ms cadence
            if signal is not None and signal.aborted:
                abort_check["saw_abort"] = True
                return ToolResult(
                    name="Slow", output={"interrupted": True}, is_error=False
                )
            if block_event.wait(timeout=0.01):
                break
        return ToolResult(name="Slow", output={"ran_to_completion": True})

    tool = build_tool(
        name="Slow",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        call=_call,
        prompt=lambda: "Slow no-op",
        description=lambda _i: "slow",
    )
    registry = ToolRegistry()
    registry.register(tool)
    return registry


def test_agent_loop_propagates_cancel_set_via_tool_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ESC trips ``tool_context.abort_controller`` mid-dispatch → loop unwinds.

    Models the TUI bridge contract after the fix: the bridge writes its
    controller to ``tool_context.abort_controller`` before the worker
    thread starts the loop. A cancel that fires while a long tool is
    running must (a) reach the running tool through ``context.abort_controller``
    and (b) unwind the outer loop without burying the abort in a tool
    error.
    """
    _patch_anthropic_check(monkeypatch)

    block_event = threading.Event()
    entered_event = threading.Event()
    abort_check: dict[str, bool] = {}
    registry = _build_registry_with_blocking_tool(
        block_event, abort_check, entered_event
    )

    provider = _FakeAnthropic(
        responses=[
            ChatResponse(
                content="",
                model="test",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="tool_use",
                tool_uses=[{"id": "call_1", "name": "Slow", "input": {}}],
            ),
            ChatResponse(  # would only be reached if the abort were ignored
                content="should never run",
                model="test",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]
    )

    controller = AbortController()
    context = _make_context(tmp_path)
    # Mirror the bridge's submit(): plumb the controller onto the context
    # before kicking off the loop.
    context.abort_controller = controller

    from src.agent.conversation import Conversation

    conv = Conversation()
    conv.add_user_message("kick it off")

    def _trip_when_tool_enters() -> None:
        # Deterministic handshake: wait for the Slow tool to actually
        # enter its poll loop before we trip the abort. This avoids a
        # sleep-based race on CI where the scheduler might keep the
        # main thread running long enough that the abort fires before
        # dispatch reaches the tool. The 5s ceiling turns a wedged
        # test into a loud failure instead of an indefinite hang.
        assert entered_event.wait(timeout=5.0), "Slow tool never entered"
        controller.abort("user_interrupt")

    threading.Thread(target=_trip_when_tool_enters, daemon=True).start()

    with pytest.raises(AbortError):
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=context,
            max_turns=5,
            stream=False,
            cancel_signal=controller.signal,
        )

    # The Slow tool MUST have actually observed the abort via the context.
    assert abort_check.get("controller_present") is True
    assert abort_check.get("saw_abort") is True
    # The loop must NOT have issued a second API call to the model after
    # cancellation — the abort should propagate before the next turn.
    assert provider.calls == 1


def test_agent_loop_does_not_swallow_abort_error_as_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that raises ``AbortError`` is propagated, not stringified.

    The previous ``except Exception`` swallowed every non-system exception
    into a synthetic ``Error: ...`` tool result and continued the loop.
    That meant a tool which raised AbortError directly (rare, but legal —
    e.g. a tool that calls ``signal.throw_if_aborted()``) would have its
    user-interrupt converted into a regular error result and the next
    API turn would still fire.
    """
    _patch_anthropic_check(monkeypatch)

    def _call(_input: dict[str, Any], _context: ToolContext) -> ToolResult:
        raise AbortError("user_interrupt")

    tool = build_tool(
        name="Raiser",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        call=_call,
        prompt=lambda: "raises",
        description=lambda _i: "raises",
    )
    registry = ToolRegistry()
    registry.register(tool)

    provider = _FakeAnthropic(
        responses=[
            ChatResponse(
                content="",
                model="test",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="tool_use",
                tool_uses=[{"id": "call_1", "name": "Raiser", "input": {}}],
            ),
        ]
    )

    controller = AbortController()
    context = _make_context(tmp_path)
    context.abort_controller = controller

    from src.agent.conversation import Conversation

    conv = Conversation()
    conv.add_user_message("kick it off")

    with pytest.raises(AbortError):
        run_agent_loop(
            conversation=conv,
            provider=provider,
            tool_registry=registry,
            tool_context=context,
            max_turns=5,
            stream=False,
            cancel_signal=controller.signal,
        )

    # The loop must not have issued a follow-up API call after the abort.
    assert provider.calls == 1


def test_query_engine_plumbs_abort_controller_into_tool_context(
    tmp_path: Path,
) -> None:
    """REPL's QueryEngine wires its controller onto ``tool_context`` at init.

    Before the fix the engine's controller was passed only as
    ``QueryParams.abort_controller``; subagents and Bash, which read
    ``context.abort_controller``, saw ``None``. The engine must now mirror
    the controller onto the shared context so the same signal that the
    query loop checks at turn boundaries is also visible to downstream
    tools.
    """
    from src.query.engine import QueryEngine, QueryEngineConfig

    context = ToolContext(workspace_root=tmp_path)
    # Pre-condition: a freshly constructed context now carries a default
    # (untripped) controller from the dataclass factory. The engine must
    # OVERWRITE this with its own controller — otherwise the engine's
    # ``interrupt()`` would trip a controller no tool can see.
    default_ctrl = context.abort_controller
    assert default_ctrl is not None
    assert default_ctrl.signal.aborted is False

    cfg = QueryEngineConfig(
        cwd=tmp_path,
        provider=MagicMock(),
        tool_registry=ToolRegistry(),
        tools=[],
        tool_context=context,
    )
    engine = QueryEngine(cfg)

    # Post-fix: the engine's controller has replaced the dataclass default.
    assert context.abort_controller is engine._abort_controller
    assert context.abort_controller is not default_ctrl

    # Interrupting the engine trips the context-visible signal.
    engine.interrupt()
    assert context.abort_controller.signal.aborted is True

    # Reset replaces the controller on both the engine and the context.
    engine.reset_abort_controller()
    assert context.abort_controller is engine._abort_controller
    assert context.abort_controller.signal.aborted is False


def test_agent_bridge_plumbs_abort_controller_into_tool_context(
    tmp_path: Path,
) -> None:
    """TUI bridge writes its per-run controller onto the tool context.

    The Agent subagent path (``run_agent``) inherits the parent's
    controller via ``parent_context.abort_controller``. If the bridge
    fails to plumb that field, the subagent creates a fresh controller
    that ESC never trips — exactly the regression this test guards.
    """
    from src.agent import Session
    from src.tui.agent_bridge import AgentBridge
    from src.tui.state import AppState

    posted: list[Any] = []

    def _post(msg: Any) -> None:
        posted.append(msg)

    # ``_run_worker`` swallows the agent thread so the bridge stays
    # in this thread for the assertions — we never actually call
    # ``run_agent_loop`` in this unit test.
    def _no_run_worker(*_args: Any, **_kwargs: Any) -> None:
        return None

    context = ToolContext(workspace_root=tmp_path)
    # The dataclass factory always installs a default (untripped)
    # controller; the bridge replaces it on each ``submit()``.
    default_ctrl = context.abort_controller
    assert default_ctrl is not None
    assert default_ctrl.signal.aborted is False

    bridge = AgentBridge(
        post_message=_post,
        session=Session.create("test", "test-model"),
        provider=MagicMock(),
        tool_registry=ToolRegistry(),
        tool_context=context,
        app_state=AppState(),
        run_worker=_no_run_worker,
    )

    submitted = bridge.submit("hello")
    assert submitted is True

    # The controller created inside submit() must be visible on the
    # shared tool context — this is the wiring that lets subagents
    # honour ESC. It must REPLACE the dataclass default (otherwise the
    # bridge's ``cancel()`` would trip a controller no tool can see).
    assert context.abort_controller is bridge._abort_controller
    assert context.abort_controller is not default_ctrl

    # Cancelling trips both objects (they are the same controller).
    aborted_ctrl = context.abort_controller
    assert bridge.cancel() is True
    assert aborted_ctrl.signal.aborted is True

    # Finishing a run swaps in a FRESH (untripped) controller so the
    # next prompt doesn't start with a stale aborted signal that would
    # short-circuit every tool dispatch. The field is non-optional, so
    # we install a new controller rather than clearing to ``None``.
    bridge._finish()
    assert context.abort_controller is not None
    assert context.abort_controller is not aborted_ctrl
    assert context.abort_controller.signal.aborted is False

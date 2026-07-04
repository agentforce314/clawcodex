"""Regression tests for headless SIGINT cancellation.

Before this fix, ``run_headless`` only caught ``KeyboardInterrupt``
between agent-loop turns. Tools that wrap long blocking IO (the Bash
supervisor's ``subprocess.wait()``, an Agent subagent's nested asyncio
loop) absorbed Ctrl-C silently until the Python interpreter reached a
safe bytecode boundary — minutes later, in the worst case.

This file pins the new behaviour:

* ``run_headless`` constructs an ``AbortController`` and plumbs it onto
  the ``ToolContext``, so every reader (Bash supervisor, streaming
  executor, tool hooks, subagent inheritance) sees the same signal.
* The agent loop's ``cancel_signal`` parameter receives the same signal
  so the loop's turn-boundary checks unwind promptly.
* The SIGINT helper is **context-aware** via a shared
  ``_InAgentLoopFlag``:
    - **In-flight** (mid-``run_agent_loop``): two-strike. First SIGINT
      trips the controller (cooperative unwind through abort-aware
      sites). Second SIGINT raises ``KeyboardInterrupt`` directly as
      the force-quit escape hatch.
    - **Idle** (blocked on stdin between prompts, e.g.
      ``StreamJsonReader``): first SIGINT raises ``KeyboardInterrupt``
      immediately so the blocking read returns. Cooperative abort
      would be wrong here — Python 3 PEP 475 auto-retries ``EINTR``'d
      reads when the handler didn't raise, so a cooperative path
      would hang the stdin read.
* Whichever path fired, ``run_headless`` exits 130 (shell parity for
  SIGINT) and emits a stream-json ``ResultEvent(subtype="cancelled")``.
  JSON output mode reports ``subtype: "cancelled"``.
* When the helper runs off the main thread (or on a platform without
  SIGINT support), the install is a no-op and headless still works.
"""
from __future__ import annotations

import io
import json
import signal as _signal
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.cli_core import UserInputMessage
from src.entrypoints import HeadlessOptions, run_headless
from src.entrypoints import headless as headless_mod
from src.providers.base import ChatResponse
from src.utils.abort_controller import AbortController, AbortError


class _CancellingProvider:
    """Provider whose first response triggers a tool-use that raises AbortError."""

    def __init__(self, api_key: str, base_url=None, model=None, *, on_chat=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "fake-model"
        self.calls = 0
        self._on_chat = on_chat

    def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self._on_chat is not None:
            self._on_chat()
        return ChatResponse(
            content="",
            model=self.model,
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="tool_use",
            tool_uses=[{"id": "call_1", "name": "FakeTool", "input": {}}],
        )

    def chat_stream(self, *_args, **_kwargs):
        raise NotImplementedError


def _patch_provider_only(monkeypatch, *, on_chat=None):
    """Patch the provider getters and registry with cancelling fakes."""

    from src.tool_system.protocol import ToolResult
    from src.tool_system.build_tool import build_tool

    def _fake_tool_call(_input, context):
        # Read the controller from the context so the test can pin
        # that the wiring is in place (context.abort_controller is what
        # tools see when they introspect the abort signal).
        # If the controller is tripped, return an interrupted result;
        # otherwise, trip it ourselves to simulate the SIGINT firing
        # mid-tool.
        if not context.abort_controller.signal.aborted:
            context.abort_controller.abort("user_interrupt")
        return ToolResult(name="FakeTool", output={"interrupted": True})

    fake_tool = build_tool(
        name="FakeTool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        call=_fake_tool_call,
        prompt=lambda: "test tool",
        description=lambda _i: "fake",
    )

    class _Registry:
        _tools = [fake_tool]

        def list_tools(self):
            return list(self._tools)

        def dispatch(self, call, context):
            return _fake_tool_call(call.input, context)

    def _fake_provider_class(provider_name):
        return lambda api_key, base_url=None, model=None: _CancellingProvider(
            api_key, base_url, model, on_chat=on_chat
        )

    monkeypatch.setattr(headless_mod, "get_provider_class", _fake_provider_class)
    monkeypatch.setattr(
        headless_mod,
        "get_provider_config",
        lambda name: {"api_key": "test-key", "default_model": "fake-model"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    # ENTRY-2: startup validation reads the REAL provider registry (the
    # shared helper, not headless's module aliases faked above) — stub it
    # out here; it has its own dedicated tests (test_startup_validation.py).
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.get_provider_validation_error",
        lambda name: None,
    )
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _Registry()
    )


def test_headless_mid_tool_cancel_emits_cancelled_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tool that trips the controller mid-dispatch yields exit 130.

    Models the SIGINT-mid-tool path: the user presses Ctrl-C, the
    signal handler trips the AbortController, and the next safe
    boundary check in the agent loop unwinds. ``stream-json`` output
    must include a ``subtype: "cancelled"`` ResultEvent (with
    ``is_error: False`` — the user cancelled, not the run failed),
    and the headless return code must be 130 for shell parity. No
    ``assistant`` event should leak before the result — the partial
    output is dropped on the cancel path.
    """
    _patch_provider_only(monkeypatch)

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="kick off a tool",
            output_format="stream-json",
            skip_permissions=True,
            stdout=stdout,
            stderr=stderr,
            workspace_root=tmp_path,
        )
    )

    assert code == 130
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    result_events = [p for p in parsed if p.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0]["subtype"] == "cancelled"
    assert result_events[0]["is_error"] is False
    assert "error" not in result_events[0] or not result_events[0]["error"]
    # No assistant event should leak on the cancel path — the partial
    # response (if any) is dropped along with the rest of the run state.
    assert not [p for p in parsed if p.get("type") == "assistant"]


def test_headless_json_mode_reports_cancelled_subtype(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--output-format json`` reports ``subtype: cancelled`` on SIGINT.

    Previously the only non-success exit codes were 1 (error) and 130
    (KeyboardInterrupt), and the json payload coerced 130 to
    ``subtype: "success"`` to avoid the error flag — misleading for a
    user who explicitly cancelled. The new mapping distinguishes the
    cancellation case so downstream consumers can tell ``success``,
    ``cancelled``, and ``error`` apart.
    """
    _patch_provider_only(monkeypatch)

    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="kick off a tool",
            output_format="json",
            skip_permissions=True,
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 130
    payload = json.loads(stdout.getvalue().strip())
    assert payload["subtype"] == "cancelled"
    # The is_error flag is still False — the user cancelled, this is
    # not an exceptional condition from the run's perspective.
    assert payload["is_error"] is False


def test_headless_idle_sigint_during_stdin_read_emits_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: SIGINT during the stream-json stdin read exits cleanly.

    Models the bug the Critic flagged: a Ctrl-C landing while
    ``StreamJsonReader.__iter__`` is blocked on the next stdin line
    must produce exit 130 + a ``subtype: "cancelled"`` ResultEvent,
    not a Python traceback. The idle-mode SIGINT handler raises
    ``KeyboardInterrupt`` to unblock the read; the for-loop's
    ``except (AbortError, KeyboardInterrupt)`` then emits the
    cancelled event in exactly one place.

    Reproduces by patching ``headless_mod.StreamJsonReader`` so the
    iterator raises ``KeyboardInterrupt`` on its second ``__next__``
    (after delivering one prompt, blocking on the second — exactly
    what a SIGINT would cause on a real stdin read).
    """
    _patch_provider_only(
        monkeypatch,
    )

    class _SigintingReader:
        """Iterator yielding one input then raising KeyboardInterrupt.

        Models the blocking ``StreamJsonReader.__iter__`` returning
        normally for the first line and getting interrupted on the
        second — exactly what the idle-mode SIGINT path produces.
        """

        def __init__(self, *_args, **_kwargs):
            self._yielded = False

        def __iter__(self):
            return self

        def __next__(self):
            if not self._yielded:
                self._yielded = True
                return UserInputMessage(text="first prompt", raw={})
            # Simulate the idle-mode handler raising while blocked
            # on the next stdin read.
            raise KeyboardInterrupt

    monkeypatch.setattr(headless_mod, "StreamJsonReader", _SigintingReader)

    # The provider's first turn must NOT use tools — we want the agent
    # loop to complete cleanly so the cancel comes from the *iterator's*
    # next step, not from inside the agent loop. A plain text response
    # achieves that.
    class _TextOnceProvider:
        def __init__(self, *_args, **_kwargs):
            self._consumed = False
            self.model = "fake-model"

        def chat(self, *_args, **_kwargs):
            self._consumed = True
            return ChatResponse(
                content="ok",
                model="fake-model",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="end_turn",
                tool_uses=None,
            )

        def chat_stream(self, *_args, **_kwargs):
            raise NotImplementedError

    monkeypatch.setattr(
        headless_mod, "get_provider_class", lambda _name: _TextOnceProvider
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt=None,
            input_format="stream-json",
            output_format="stream-json",
            skip_permissions=True,
            stdin=io.StringIO(),  # unused — our patched reader ignores it
            stdout=stdout,
            stderr=stderr,
            workspace_root=tmp_path,
        )
    )

    assert code == 130, f"expected exit 130, got {code}; stdout: {stdout.getvalue()}"
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    result_events = [p for p in parsed if p.get("type") == "result"]
    assert len(result_events) == 1
    assert result_events[0]["subtype"] == "cancelled"
    assert result_events[0]["is_error"] is False
    # The first prompt's assistant reply IS emitted — it completed
    # before the cancel landed. Pin that: cancellation must not erase
    # already-shipped work, only short-circuit further iteration.
    assistant_events = [p for p in parsed if p.get("type") == "assistant"]
    assert len(assistant_events) == 1
    assert assistant_events[0]["text"] == "ok"


def test_install_sigint_handler_in_loop_two_strike() -> None:
    """Cooperative mode: first strike trips controller, second raises.

    With ``in_agent_loop.value == True`` (the agent loop is in flight),
    the first SIGINT cooperatively trips the controller so abort-aware
    sites can unwind gracefully. The second SIGINT is the force-quit
    escape hatch: it raises ``KeyboardInterrupt`` directly and also
    re-installs the platform default handler as defense in depth — a
    rare third strike landing during unwind then terminates the process
    via SIGINT's default action rather than re-entering our handler.
    """
    controller = AbortController()
    in_loop = headless_mod._InAgentLoopFlag()
    in_loop.value = True
    stderr = io.StringIO()

    # Capture the previous handler so we can restore at the end.
    previous = _signal.getsignal(_signal.SIGINT)
    try:
        restore = headless_mod._install_sigint_handler(controller, in_loop, stderr)

        # Look up the installed handler and simulate signal delivery.
        installed = _signal.getsignal(_signal.SIGINT)
        assert callable(installed)
        assert installed is not previous  # actually installed something

        # First strike: trip the controller, no raise.
        installed(_signal.SIGINT, None)
        assert controller.signal.aborted is True
        assert controller.signal.reason == "user_interrupt"
        assert "Cancelling" in stderr.getvalue()

        # Second strike: must raise KeyboardInterrupt as the force-
        # quit escape hatch. The handler also restores the default
        # SIGINT handler so a third hit terminates the process the
        # ordinary way.
        with pytest.raises(KeyboardInterrupt):
            installed(_signal.SIGINT, None)

        restore()
    finally:
        # Defensive: ensure we don't leak our handler to other tests.
        _signal.signal(_signal.SIGINT, previous)


def test_install_sigint_handler_idle_raises_immediately() -> None:
    """Idle mode: first strike raises so blocking stdin reads return.

    When ``in_agent_loop.value == False`` (between agent-loop runs, e.g.
    blocked on ``StreamJsonReader``'s stdin read for the next input),
    a cooperative abort would be a UX regression: Python 3 PEP 475
    auto-retries ``EINTR``-interrupted reads when the signal handler
    didn't raise, so the read would keep blocking and the user would
    have to hit Ctrl-C twice to exit. Idle-mode SIGINT must raise
    ``KeyboardInterrupt`` directly to make the read return.
    """
    controller = AbortController()
    in_loop = headless_mod._InAgentLoopFlag()
    in_loop.value = False  # idle on stdin
    stderr = io.StringIO()

    previous = _signal.getsignal(_signal.SIGINT)
    try:
        restore = headless_mod._install_sigint_handler(controller, in_loop, stderr)
        installed = _signal.getsignal(_signal.SIGINT)

        # First strike while idle must raise, not just trip the controller.
        with pytest.raises(KeyboardInterrupt):
            installed(_signal.SIGINT, None)
        # The controller is NOT cooperatively tripped on idle — there's
        # nothing to cooperatively unwind; we just exit the process.
        assert controller.signal.aborted is False

        restore()
    finally:
        _signal.signal(_signal.SIGINT, previous)


def test_install_sigint_handler_off_main_thread_is_noop() -> None:
    """Calling the installer from a worker thread is a safe no-op.

    ``signal.signal()`` only works in the main thread of the main
    interpreter. An SDK consumer that drives ``run_headless`` from a
    worker thread (or an embedded interpreter that disables signals)
    must not crash on install — cancellation falls back to the agent
    loop's natural turn-boundary checks, which is the pre-fix behaviour.
    """
    controller = AbortController()
    in_loop = headless_mod._InAgentLoopFlag()
    stderr = io.StringIO()
    result: dict[str, object] = {}

    def _thread_body() -> None:
        try:
            restore = headless_mod._install_sigint_handler(
                controller, in_loop, stderr
            )
            result["restore_callable"] = callable(restore)
            # Restore is a no-op when install was skipped; call it
            # anyway and ensure it doesn't raise.
            restore()
            result["restored_cleanly"] = True
        except Exception as exc:  # pragma: no cover — failure path
            result["error"] = exc

    t = threading.Thread(target=_thread_body)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "install_sigint_handler hung on a worker thread"
    assert "error" not in result, f"install raised on a worker thread: {result.get('error')}"
    assert result.get("restore_callable") is True
    assert result.get("restored_cleanly") is True
    # The controller must still be untripped — there's no signal to deliver.
    assert controller.signal.aborted is False

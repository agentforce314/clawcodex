"""Integration tests for the headless (``--print``) CLI path.

These tests bypass the real provider and tool registry by monkey-patching the
wiring inside ``src.entrypoints.headless`` so we can exercise the stdout
contract without any network IO.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from src.entrypoints import HeadlessOptions, run_headless
from src.entrypoints import headless as headless_mod
from src.providers.base import ChatResponse


class _FakeProvider:
    """Minimal stand-in for an LLM provider.

    ``responses`` is a list of ``ChatResponse`` to return in order. If tool
    calls are requested, they must match the shape
    ``{"id": str, "name": str, "input": dict}``.
    """

    def __init__(self, api_key: str, base_url=None, model=None, *, responses=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "fake-model"
        self._responses = list(responses or [])

    def chat(self, messages, tools=None, **kwargs):
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)

    def chat_stream(self, messages, tools=None, **kwargs):
        raise NotImplementedError


class _FakeRegistry:
    def list_tools(self):
        return []

    def remove_tool(self, name):
        # run_headless unregisters AskUserQuestion (no user on
        # this surface); real ToolRegistry returns a bool.
        return False


@pytest.fixture
def fake_wiring(monkeypatch):
    """Patch provider/tool wiring with fakes that require no API key."""

    scripted_responses: list[ChatResponse] = []

    def _fake_provider_class(provider_name):
        def _ctor(api_key, base_url=None, model=None):
            return _FakeProvider(api_key, base_url, model, responses=list(scripted_responses))

        return _ctor

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
    monkeypatch.setattr(headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry())

    return scripted_responses


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=text,
        model="fake-model",
        usage={"input_tokens": 5, "output_tokens": len(text.split())},
        finish_reason="end_turn",
        tool_uses=None,
    )


# ---------------------------------------------------------------------------
# text output


def test_headless_text_output_prints_assistant_reply(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("Hello, human!"))

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="text",
            stdout=stdout,
            stderr=stderr,
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    assert stdout.getvalue().strip() == "Hello, human!"


def test_headless_text_reads_prompt_from_stdin_when_dash(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("from-stdin"))

    code = run_headless(
        HeadlessOptions(
            prompt="-",
            output_format="text",
            stdin=io.StringIO("piped prompt"),
            stdout=(out := io.StringIO()),
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    assert "from-stdin" in out.getvalue()


# ---------------------------------------------------------------------------
# json output


def test_headless_json_output_emits_single_object(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("json reply"))

    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    payload = json.loads(stdout.getvalue().strip())
    assert payload["type"] == "result"
    assert payload["subtype"] == "success"
    assert payload["result"] == "json reply"
    assert payload["provider"] == "anthropic"
    assert payload["num_turns"] == 1
    assert payload["usage"]["input_tokens"] == 5


# ---------------------------------------------------------------------------
# stream-json output


def test_headless_stream_json_emits_system_assistant_result(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("stream reply"))

    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
    parsed = [json.loads(l) for l in lines]
    types = [ev["type"] for ev in parsed]
    assert types[0] == "system"
    assert "assistant" in types
    assert types[-1] == "result"
    assistant = next(ev for ev in parsed if ev["type"] == "assistant")
    assert assistant["text"] == "stream reply"
    result = parsed[-1]
    assert result["result"] == "stream reply"
    assert result["num_turns"] == 1
    assert result["subtype"] == "success"


def test_headless_stream_json_input_requires_matching_output(fake_wiring, tmp_path):
    stderr = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        run_headless(
            HeadlessOptions(
                prompt="hi",
                input_format="stream-json",
                output_format="text",
                stdout=io.StringIO(),
                stderr=stderr,
                workspace_root=tmp_path,
            )
        )
    assert excinfo.value.code == 2


def test_headless_stream_json_multi_turn_from_stdin(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("A"))
    fake_wiring.append(_text_response("B"))

    stdin = io.StringIO(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "one"}}),
                json.dumps({"type": "user", "message": {"content": "two"}}),
            ]
        )
        + "\n"
    )
    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            output_format="stream-json",
            input_format="stream-json",
            stdin=stdin,
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    parsed = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assistants = [ev for ev in parsed if ev["type"] == "assistant"]
    assert [ev["text"] for ev in assistants] == ["A", "B"]
    result = parsed[-1]
    assert result["num_turns"] == 2
    assert "A" in result["result"] and "B" in result["result"]


# ---------------------------------------------------------------------------
# permission handling in headless mode


def test_headless_without_skip_permissions_installs_auto_deny_handler(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("ok"))

    captured: dict = {}
    # Headless now routes through ``run_query_as_agent_loop`` (the F.1
    # adapter) instead of the legacy ``run_agent_loop``. Patch the
    # actual production call site.
    original = headless_mod.run_query_as_agent_loop

    async def _capture(*args, **kwargs):
        captured["tool_context"] = kwargs["tool_context"]
        return await original(*args, **kwargs)

    import src.entrypoints.headless as mod
    mod.run_query_as_agent_loop = _capture  # type: ignore[assignment]
    try:
        code = run_headless(
            HeadlessOptions(
                prompt="hi",
                output_format="text",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                workspace_root=tmp_path,
            )
        )
    finally:
        mod.run_query_as_agent_loop = original  # type: ignore[assignment]

    assert code == 0
    ctx = captured["tool_context"]
    assert ctx.options.is_non_interactive_session is True
    # Non-interactive mode installs an auto-deny handler that replies deny.
    from src.permissions.types import PermissionAskRequest

    reply = ctx.permission_handler(
        PermissionAskRequest(tool_name="Bash", message="needs approval")
    )
    assert reply.behavior == "deny"


def test_headless_with_skip_permissions_clears_handler(fake_wiring, tmp_path):
    fake_wiring.append(_text_response("ok"))

    captured: dict = {}
    # Headless now routes through ``run_query_as_agent_loop`` (the F.1
    # adapter) instead of the legacy ``run_agent_loop``. Patch the
    # actual production call site.
    original = headless_mod.run_query_as_agent_loop

    async def _capture(*args, **kwargs):
        captured["tool_context"] = kwargs["tool_context"]
        return await original(*args, **kwargs)

    import src.entrypoints.headless as mod
    mod.run_query_as_agent_loop = _capture  # type: ignore[assignment]
    try:
        run_headless(
            HeadlessOptions(
                prompt="hi",
                output_format="text",
                skip_permissions=True,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                workspace_root=tmp_path,
            )
        )
    finally:
        mod.run_query_as_agent_loop = original  # type: ignore[assignment]

    ctx = captured["tool_context"]
    assert ctx.permission_handler is None
    assert ctx.allow_docs is True
    assert ctx.options.is_non_interactive_session is True


# ---------------------------------------------------------------------------
# flag validation


def test_headless_invalid_output_format_exits_2(fake_wiring, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run_headless(
            HeadlessOptions(
                prompt="hi",
                output_format="bogus",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                workspace_root=tmp_path,
            )
        )
    assert excinfo.value.code == 2


def test_headless_empty_prompt_exits_2(fake_wiring, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run_headless(
            HeadlessOptions(
                prompt="",
                output_format="text",
                stdin=io.StringIO(""),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                workspace_root=tmp_path,
            )
        )
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# persisted-model resolution (the one deliberate behaviour change in the
# model-restore delta: headless previously ignored `settings.model` entirely,
# so a `/model` switch had to be re-stated with `--model` on every `-p` run)


def test_headless_uses_the_persisted_model_when_no_flag(fake_wiring, tmp_path, monkeypatch):
    fake_wiring.append(_text_response("ok"))
    monkeypatch.setattr(
        "src.settings.settings.get_persisted_model",
        lambda name, **kw: "persisted-model",
    )
    seen: list[str | None] = []
    inner = headless_mod.get_provider_class

    def _capture(provider_name):
        ctor = inner(provider_name)

        def _wrapped(api_key, base_url=None, model=None):
            seen.append(model)
            return ctor(api_key, base_url, model)

        return _wrapped

    monkeypatch.setattr(headless_mod, "get_provider_class", _capture)
    run_headless(
        HeadlessOptions(
            prompt="hi", output_format="text",
            stdout=io.StringIO(), stderr=io.StringIO(), workspace_root=tmp_path,
        )
    )
    assert seen == ["persisted-model"]


def test_headless_explicit_model_beats_the_persisted_one(fake_wiring, tmp_path, monkeypatch):
    # TS precedence (main.tsx:1984): explicit ?? persisted ?? default.
    fake_wiring.append(_text_response("ok"))
    monkeypatch.setattr(
        "src.settings.settings.get_persisted_model",
        lambda name, **kw: "persisted-model",
    )
    seen: list[str | None] = []
    inner = headless_mod.get_provider_class

    def _capture(provider_name):
        ctor = inner(provider_name)

        def _wrapped(api_key, base_url=None, model=None):
            seen.append(model)
            return ctor(api_key, base_url, model)

        return _wrapped

    monkeypatch.setattr(headless_mod, "get_provider_class", _capture)
    run_headless(
        HeadlessOptions(
            prompt="hi", model="explicit-model", output_format="text",
            stdout=io.StringIO(), stderr=io.StringIO(), workspace_root=tmp_path,
        )
    )
    assert seen == ["explicit-model"]


def test_headless_falls_back_to_the_provider_default(fake_wiring, tmp_path, monkeypatch):
    fake_wiring.append(_text_response("ok"))
    monkeypatch.setattr("src.settings.settings.get_persisted_model", lambda name, **kw: "")
    seen: list[str | None] = []
    inner = headless_mod.get_provider_class

    def _capture(provider_name):
        ctor = inner(provider_name)

        def _wrapped(api_key, base_url=None, model=None):
            seen.append(model)
            return ctor(api_key, base_url, model)

        return _wrapped

    monkeypatch.setattr(headless_mod, "get_provider_class", _capture)
    run_headless(
        HeadlessOptions(
            prompt="hi", output_format="text",
            stdout=io.StringIO(), stderr=io.StringIO(), workspace_root=tmp_path,
        )
    )
    assert seen == ["fake-model"]        # provider_config default


def test_headless_passes_provider_is_explicit_when_provider_flagged(
    fake_wiring, tmp_path, monkeypatch
):
    # A persisted FUSION model replaces the session provider, so it must not
    # win over a typed --provider; headless has to forward that signal.
    fake_wiring.append(_text_response("ok"))
    calls: list[dict] = []

    def _spy(name, **kw):
        calls.append({"name": name, **kw})
        return ""

    monkeypatch.setattr("src.settings.settings.get_persisted_model", _spy)
    run_headless(
        HeadlessOptions(
            prompt="hi", provider_name="openrouter", output_format="text",
            stdout=io.StringIO(), stderr=io.StringIO(), workspace_root=tmp_path,
        )
    )
    assert calls and calls[0]["provider_is_explicit"] is True


# ---------------------------------------------------------------------------
# early-stop visibility — a run the harness cut short is not a "success"


def _stub_loop(monkeypatch, *, reason, text="stopped", turns=3):
    """Make the agent loop return a Terminal instead of a normal completion.

    Patches the seam headless actually consumes. Driving a real guard trip
    would need a tool registry with failing tools; what is under test here is
    the mapping from terminal reason to result subtype, not the guard.
    """
    from src.query.agent_loop_compat import AgentLoopRunResult
    from src.query.transitions import Terminal

    async def fake_loop(*_a, **_k):
        return AgentLoopRunResult(
            response_text=text,
            usage={"input_tokens": 100, "output_tokens": 20},
            num_turns=turns,
            terminal=Terminal(reason=reason) if reason else None,
        )

    monkeypatch.setattr(headless_mod, "run_query_as_agent_loop", fake_loop)


def _result_event(raw: str) -> dict:
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("type") == "result":
            return obj
    raise AssertionError(f"no result event in output: {raw!r}")


@pytest.mark.parametrize(
    "reason,expected_subtype",
    [
        ("tool_failure_loop", "error_during_execution"),
        ("max_turns", "error_max_turns"),
        # blocking_limit / prompt_too_long are the worst of the set: their
        # explanation never reaches response_text, so they reported success
        # with an EMPTY result — a clean completion with no evidence at all.
        ("blocking_limit", "error_during_execution"),
        ("prompt_too_long", "error_during_execution"),
        ("image_error", "error_during_execution"),
    ],
)
def test_early_stop_is_not_reported_as_success_stream_json(
    fake_wiring, tmp_path, monkeypatch, reason, expected_subtype
):
    """THE regression guard.

    A tool-failure-loop trip or a max_turns stop used to emit
    ``subtype: "success", is_error: false``, so a batch runner recorded a
    clean completion that merely scored zero — which is how 31-second guard
    kills went unnoticed on terminal-bench until the raw trajectories were
    read. Subtypes mirror the reference (QueryEngine.ts:891, :1142).
    """
    _stub_loop(monkeypatch, reason=reason)
    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    event = _result_event(stdout.getvalue())
    assert event["subtype"] == expected_subtype
    assert event["is_error"] is True


@pytest.mark.parametrize(
    "reason,expected_subtype",
    [
        ("tool_failure_loop", "error_during_execution"),
        ("max_turns", "error_max_turns"),
    ],
)
def test_early_stop_subtype_in_json_output(
    fake_wiring, tmp_path, monkeypatch, reason, expected_subtype
):
    _stub_loop(monkeypatch, reason=reason)
    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    event = _result_event(stdout.getvalue())
    assert event["subtype"] == expected_subtype
    assert event["is_error"] is True


def test_early_stop_keeps_the_full_metrics_block(
    fake_wiring, tmp_path, monkeypatch
):
    """Eval adapters read tokens/cost off this event.

    Suppressing it, or replacing it with a bare error, would trade a
    silent-success bug for a missing-data bug — the reference keeps usage and
    num_turns on error_during_execution too (QueryEngine.ts:1142-1153).
    """
    _stub_loop(monkeypatch, reason="tool_failure_loop", text="Stopped: repeated tool failures detected.", turns=4)
    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    event = _result_event(stdout.getvalue())
    assert event["usage"], "usage must survive — adapters read tokens from here"
    assert event["num_turns"] == 4
    assert "repeated tool failures" in event["result"], (
        "the stop explanation must still reach the caller"
    )


def test_normal_completion_still_reports_success(
    fake_wiring, tmp_path, monkeypatch
):
    """The default path must be untouched: no terminal, no early-stop flag."""
    _stub_loop(monkeypatch, reason=None, text="all done")
    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    event = _result_event(stdout.getvalue())
    assert event["subtype"] == "success"
    assert event["is_error"] is False
    assert code == 0


def test_early_stop_does_not_change_the_exit_code(
    fake_wiring, tmp_path, monkeypatch
):
    """Deliberate: the process exit code stays 0.

    NOT because the emission is gated on ``exit_code == 0`` — that gate is
    movable. Because Harbor raises ``NonZeroAgentExitCodeError`` on a
    non-zero code, the trial then records an ``exception.txt``, and
    ``eval/harbor/compare_trajectories.py`` treats such trials as "killed by
    harness" and EXCLUDES them from the step means. Flipping the code would
    right-censor every guard trip out of the very comparison this change
    exists to make honest.

    A documented divergence from the reference, which exits
    ``is_error ? 1 : 0`` (print.ts:1069-1070). The trade: a shell caller
    doing ``clawcodex -p ...; echo $?`` cannot detect an early stop, and must
    read the result event's subtype instead.
    """
    _stub_loop(monkeypatch, reason="tool_failure_loop")
    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    assert code == 0
    assert _result_event(stdout.getvalue())["subtype"] == "error_during_execution"


@pytest.mark.parametrize("reason", ["hook_stopped", "stop_hook_prevented", "completed"])
def test_deliberately_unmapped_terminals_stay_success(
    fake_wiring, tmp_path, monkeypatch, reason
):
    """Hook stops are the operator's own policy working as configured, not the
    harness cutting a run short. Pinned so the exclusion reads as a decision
    rather than an oversight — and so widening the map is a conscious act."""
    _stub_loop(monkeypatch, reason=reason)
    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="stream-json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    assert _result_event(stdout.getvalue())["subtype"] == "success"


def test_early_stop_survives_a_later_normal_prompt(fake_wiring, tmp_path, monkeypatch):
    """Multi-prompt stream-json: ONE result event covers the whole run.

    Every other field on it is cumulative (num_turns, usage, text), so an
    early stop on prompt N must not be erased by prompt N+1 completing
    normally — otherwise the explanation sits in ``result`` while ``subtype``
    claims success, which is the original bug wearing a disguise.
    """
    from src.query.agent_loop_compat import AgentLoopRunResult
    from src.query.transitions import Terminal

    calls = {"n": 0}

    async def fake_loop(*_a, **_k):
        calls["n"] += 1
        first = calls["n"] == 1
        return AgentLoopRunResult(
            response_text="[Stopped: repeated tool failures detected]" if first else "ok",
            usage={"input_tokens": 10, "output_tokens": 2},
            num_turns=2,
            terminal=Terminal(reason="tool_failure_loop") if first else Terminal(reason="completed"),
        )

    monkeypatch.setattr(headless_mod, "run_query_as_agent_loop", fake_loop)
    stdin = io.StringIO(
        json.dumps({"type": "user", "message": {"role": "user", "content": "one"}}) + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": "two"}}) + "\n"
    )
    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt=None,
            input_format="stream-json",
            output_format="stream-json",
            stdin=stdin,
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    assert calls["n"] == 2, "both prompts must have run"
    event = _result_event(stdout.getvalue())
    assert event["subtype"] == "error_during_execution", (
        "an early stop anywhere in the run must survive to the result event"
    )
    assert event["is_error"] is True


def test_cancelled_is_not_flagged_as_an_error(fake_wiring, tmp_path, monkeypatch):
    """Exit-code precedence: 130 must still win, and cancelled must NOT be
    flagged is_error — the pairing is deliberate."""
    from src.query.agent_loop_compat import AgentLoopRunResult
    from src.query.transitions import Terminal
    from src.utils.abort_controller import AbortError

    async def fake_loop(*_a, **_k):
        raise AbortError("user_interrupt")

    monkeypatch.setattr(headless_mod, "run_query_as_agent_loop", fake_loop)
    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )
    event = _result_event(stdout.getvalue())
    assert code == 130
    assert event["subtype"] == "cancelled"
    assert event["is_error"] is False, "cancelled is not an error"


def test_text_format_reports_the_early_stop_on_stderr(
    fake_wiring, tmp_path, monkeypatch
):
    """The default format has no structured field, and it is what a plain
    shell caller sees."""
    _stub_loop(monkeypatch, reason="tool_failure_loop", text="stopped")
    stderr = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="text",
            stdout=io.StringIO(),
            stderr=stderr,
            workspace_root=tmp_path,
        )
    )
    assert "stopped early" in stderr.getvalue()
    assert "tool_failure_loop" in stderr.getvalue()


@pytest.mark.parametrize(
    "exit_code,early,expected",
    [
        (0, None, ("success", False)),
        (0, "error_during_execution", ("error_during_execution", True)),
        (0, "error_max_turns", ("error_max_turns", True)),
        (130, None, ("cancelled", False)),
        # THE collision: an early stop earlier in the run, then a cancel.
        # Reachable at runtime only via a /goal continuation, which is why
        # the derivation is pinned as a pure function instead.
        (130, "error_during_execution", ("cancelled", False)),
        (1, None, ("error", True)),
        (1, "error_during_execution", ("error", True)),
    ],
)
def test_subtype_and_is_error_never_disagree(exit_code, early, expected):
    """``is_error`` must be derived from the subtype, never recomputed.

    An independent expression let ``cancelled`` come back flagged as an
    error, contradicting the deliberate pairing that a user interrupt is not
    a failure.
    """
    assert headless_mod._json_result_subtype(exit_code, early) == expected


# ---------------------------------------------------------------------------
# usage accounting in the emitted result


def _cached_response(text: str, *, cache_read: int) -> ChatResponse:
    """A response whose prompt was mostly served from the cache."""
    return ChatResponse(
        content=text,
        model="fake-model",
        usage={
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": 2,
        },
        finish_reason="end_turn",
        tool_uses=None,
    )


def test_headless_result_usage_sums_cache_tokens(fake_wiring, tmp_path):
    """The emitted `result.usage` must be a complete billing total.

    Drives `run_headless` rather than the accumulation helper: an earlier
    version of these assertions called the helper directly and passed with
    the call site reverted to the old inline loop.
    """
    fake_wiring.append(_cached_response("hi", cache_read=1000))

    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    usage = payload["usage"]
    assert usage["cache_read_input_tokens"] == 1000, (
        "cumulative cache tokens never reached the emitted result"
    )
    assert usage["cache_creation_input_tokens"] == 2
    # input_tokens keeps its meaning — cache misses only
    assert usage["input_tokens"] == 5


def test_headless_result_usage_keeps_last_snapshot_unsummed(fake_wiring, tmp_path):
    """`last_*` are a snapshot of the most recent response, not a running sum.

    Summing them across turns produces a number that measures nothing, and it
    ships in this payload.
    """
    fake_wiring.append(_cached_response("hi", cache_read=1000))

    stdout = io.StringIO()
    run_headless(
        HeadlessOptions(
            prompt="hi",
            output_format="json",
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    usage = json.loads(stdout.getvalue())["usage"]
    assert usage["last_cache_read_input_tokens"] == 1000
    assert usage["last_input_tokens"] == 5


def test_headless_multi_prompt_sums_cache_but_not_the_last_snapshot(
    fake_wiring, tmp_path
):
    """Two prompts: cumulative keys ADD, `last_*` keys REPLACE.

    One prompt cannot tell the two apart — the generic
    `total = total + value` loop and the split accumulator agree on a single
    accumulation, which is why a mutant reverting the call site survived a
    single-prompt test. The divergence only appears from the second prompt on.
    """
    fake_wiring.append(_cached_response("A", cache_read=1000))
    fake_wiring.append(_cached_response("B", cache_read=3000))

    stdin = io.StringIO(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "one"}}),
                json.dumps({"type": "user", "message": {"content": "two"}}),
            ]
        )
        + "\n"
    )
    stdout = io.StringIO()
    code = run_headless(
        HeadlessOptions(
            output_format="stream-json",
            input_format="stream-json",
            stdin=stdin,
            stdout=stdout,
            stderr=io.StringIO(),
            workspace_root=tmp_path,
        )
    )

    assert code == 0
    parsed = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    usage = parsed[-1]["usage"]

    # cumulative: both prompts' cache reads
    assert usage["cache_read_input_tokens"] == 4000
    assert usage["cache_creation_input_tokens"] == 4
    assert usage["input_tokens"] == 10

    # snapshot: the SECOND prompt's value alone, not 1000 + 3000
    assert usage["last_cache_read_input_tokens"] == 3000, (
        "last_* was summed across prompts, which measures nothing"
    )
    assert usage["last_input_tokens"] == 5

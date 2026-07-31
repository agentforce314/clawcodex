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

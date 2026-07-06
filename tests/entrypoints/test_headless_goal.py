"""Headless /goal loop: ``clawcodex -p "/goal <condition>"`` runs the
evaluate-continue loop to completion in one invocation (CC docs/en/goal
§Run non-interactively).

Reuses test_headless_cli.py's fake-wiring pattern. The FakeProvider's script
queue serves BOTH the conversation turns (the query loop calls
``provider.chat``) and the evaluator side-calls (``build_judge_callable``
also calls ``provider.chat``), so scripts interleave: turn, judge, turn,
judge, …
"""

from __future__ import annotations

import io

import pytest

from src.entrypoints import HeadlessOptions, run_headless
from src.entrypoints import headless as headless_mod
from src.providers.base import ChatResponse


class _FakeProvider:
    def __init__(self, api_key: str, base_url=None, model=None, *, responses=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or "fake-model"
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)

    def chat_stream(self, messages, tools=None, **kwargs):
        raise NotImplementedError


class _FakeRegistry:
    def list_tools(self):
        return []


class _Wiring:
    """Fixture handle: ``script`` feeds providers created after it's filled;
    ``created`` records every provider instance for call inspection."""

    def __init__(self) -> None:
        self.script: list[ChatResponse] = []
        self.created: list[_FakeProvider] = []

    # list-ish conveniences so tests read naturally
    def extend(self, items) -> None:
        self.script.extend(items)

    def append(self, item) -> None:
        self.script.append(item)


@pytest.fixture
def fake_wiring(monkeypatch):
    wiring = _Wiring()

    def _fake_provider_class(provider_name):
        def _ctor(api_key, base_url=None, model=None):
            p = _FakeProvider(api_key, base_url, model, responses=list(wiring.script))
            wiring.created.append(p)
            return p

        return _ctor

    monkeypatch.setattr(headless_mod, "get_provider_class", _fake_provider_class)
    monkeypatch.setattr(
        headless_mod,
        "get_provider_config",
        lambda name: {"api_key": "test-key", "default_model": "fake-model"},
    )
    monkeypatch.setattr(headless_mod, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(
        "src.entrypoints.provider_validation.get_provider_validation_error",
        lambda name: None,
    )
    monkeypatch.setattr(
        headless_mod, "build_default_registry", lambda provider=None: _FakeRegistry()
    )
    # /goal gates: trust the tmp workspace; hooks enabled regardless of the
    # developer machine's real settings. The fake returns a REAL
    # SettingsSchema (defaults: hooks enabled) so every other load_settings
    # caller in the turn path keeps working, and the get_settings cache is
    # reset around the test so the fake can't leak into later tests.
    import src.settings.settings as settings_mod
    from src.settings.types import SettingsSchema

    monkeypatch.setattr(
        "src.services.startup_gates.check_trust_accepted", lambda root: True
    )
    monkeypatch.setattr(
        settings_mod, "load_settings", lambda *a, **k: SettingsSchema()
    )
    monkeypatch.setattr(settings_mod, "_settings_cache", None)
    yield wiring
    settings_mod._settings_cache = None


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        content=text,
        model="fake-model",
        usage={"input_tokens": 5, "output_tokens": 3},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _run(prompt: str, tmp_path, **opt_kwargs):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_headless(HeadlessOptions(
        prompt=prompt,
        output_format="text",
        stdout=stdout,
        stderr=stderr,
        workspace_root=tmp_path,
        **opt_kwargs,
    ))
    return code, stdout.getvalue(), stderr.getvalue()


def test_goal_loop_runs_until_evaluator_says_done(fake_wiring, tmp_path):
    fake_wiring.extend([
        _text("first attempt — still failing"),                       # turn 1
        _text('{"verdict": "continue", "reason": "tests still red"}'),  # judge 1
        _text("fixed everything; tests pass"),                        # turn 2
        _text('{"verdict": "done", "reason": "tests green"}'),        # judge 2
    ])
    code, out, err = _run("/goal make the tests pass", tmp_path)
    assert code == 0
    # Both conversation turns aggregate to stdout; goal progress on stderr.
    assert "first attempt" in out
    assert "fixed everything" in out
    assert "Goal set" in err
    assert "↻" in err and "tests still red" in err
    assert "✓ Goal achieved" in err


def test_goal_continuation_prompt_carries_reason(fake_wiring, tmp_path):
    fake_wiring.extend([
        _text("attempt"),
        _text('{"verdict": "continue", "reason": "missing the docs entry"}'),
        _text("done now"),
        _text('{"verdict": "done", "reason": "ok"}'),
    ])
    code, _, _ = _run("/goal write the docs", tmp_path)
    assert code == 0
    provider = fake_wiring.created[0]
    assert not provider._responses  # every scripted response was consumed

    def _flat(call) -> str:
        return "\n".join(
            str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", ""))
            for m in call["messages"]
        )

    continuation_turns = [
        c for c in provider.calls
        if "[Continuing toward your standing goal]" in _flat(c)
    ]
    assert continuation_turns, "no continuation turn reached the provider"
    text = _flat(continuation_turns[-1])
    assert "Goal: write the docs" in text
    assert "missing the docs entry" in text  # evaluator reason as guidance
    # Judge side-calls carry the strict-JSON system prompt; conversation
    # turns must not.
    judge_calls = [
        c for c in provider.calls
        if "strict judge" in str(c["kwargs"].get("system", ""))
    ]
    assert len(judge_calls) == 2


def test_goal_budget_exhaustion_exits_nonzero(fake_wiring, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.settings.settings.get_settings",
        lambda *a, **k: __import__("types").SimpleNamespace(goal_max_turns=2),
    )
    fake_wiring.extend([
        _text("attempt 1"),
        _text('{"verdict": "continue", "reason": "nope"}'),
        _text("attempt 2"),
        _text('{"verdict": "continue", "reason": "still nope"}'),
        # budget 2 exhausted after judge 2 — no further calls
    ])
    code, out, err = _run("/goal unreachable condition", tmp_path)
    assert code == 1
    assert "turns used" in err  # budget pause message
    assert "not achieved" in err


def test_bare_goal_prints_status_without_running_a_turn(fake_wiring, tmp_path):
    code, out, err = _run("/goal", tmp_path)
    assert code == 0
    assert "No active goal" in out


def test_goal_clear_prints_and_exits(fake_wiring, tmp_path):
    code, out, err = _run("/goal clear", tmp_path)
    assert code == 0
    assert "No active goal" in out


def test_goal_untrusted_workspace_refused(fake_wiring, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.startup_gates.check_trust_accepted", lambda root: False
    )
    code, out, err = _run("/goal do something", tmp_path)
    assert code == 1
    assert "trusted workspace" in err


def test_plain_prompt_unaffected_by_goal_machinery(fake_wiring, tmp_path):
    fake_wiring.append(_text("plain answer"))
    code, out, err = _run("just a question", tmp_path)
    assert code == 0
    assert "plain answer" in out
    assert "[goal]" not in err

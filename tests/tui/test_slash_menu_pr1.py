"""Slash-command menu parity fixes (TUI UX PR 1).

Locks in four behaviors ported from the ink reference's command typeahead
(``commandSuggestions.ts`` / ``useTypeahead.tsx``):

* **Enter executes** the highlighted command — both when the ``Input`` is
  focused and after arrow-navigation moved focus onto the popup
  (``OptionList.OptionSelected``). Previously Enter either filled the text
  (needing a second Enter) or did nothing at all after arrow-nav.
* **Enter fills, not executes, arg-taking commands** (``/<name> ``) so the
  user can type the argument — matching TS ``applyCommandSuggestion`` which
  executes iff ``type !== 'prompt' || argNames.length === 0``.
* **Tab completes without executing** (``/<name> ``).
* **Prefix-only filtering** — no loose subsequence matches
  (``/st`` must not surface ``/cost``/``/history``; ``/co`` not ``/doctor``).
* **Display capped at 6 rows** (TS ``Math.min(6, …)``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.commands import CommandSuggestion
from src.tui.widgets.prompt_input import (
    PromptInput,
    PromptSubmitted,
    _MAX_VISIBLE_SUGGESTIONS,
    _options_from_suggestions,
)


# ---- fixtures: a deterministic rich suggestion set ------------------------

_SUGGESTIONS: list[CommandSuggestion] = [
    CommandSuggestion(name="help", description="Show help"),
    CommandSuggestion(name="stream", description="Toggle streaming"),
    CommandSuggestion(name="cost", description="Show cost"),
    CommandSuggestion(name="history", description="Open history"),
    CommandSuggestion(name="compact", description="Compact context"),
    CommandSuggestion(name="doctor", description="Diagnose"),
    CommandSuggestion(name="model", description="Pick model"),
    CommandSuggestion(name="memory", description="Edit memory"),
    CommandSuggestion(name="clear", description="Clear transcript"),
    # arg-taking command (prompt-style): Enter should FILL, not execute.
    CommandSuggestion(name="greet", description="Greet", arg_names=("name",)),
    # alias-only prefix: name has no "bg" prefix, alias does.
    CommandSuggestion(name="tasks", description="Tasks", aliases=("bg",)),
    # hyphenated / namespaced names — TS Fuse matches on name parts split
    # by ``[:_-]``, so ``/notes`` and ``/ceo`` must surface these.
    CommandSuggestion(name="release-notes", description="Release notes"),
    CommandSuggestion(name="plan-ceo-review", description="CEO review"),
]


def _ids(partial: str) -> list[str]:
    return [opt.id for opt in _options_from_suggestions(_SUGGESTIONS, partial)]


# ---- pure-function filtering tests (no app) -------------------------------


def test_prefix_only_excludes_subsequence_matches():
    rows = _ids("st")
    assert "/stream" in rows
    # "st" appears mid-word in cost/history but is NOT a prefix → excluded.
    assert "/cost" not in rows
    assert "/history" not in rows


def test_prefix_co_excludes_doctor():
    rows = _ids("co")
    assert set(rows) == {"/cost", "/compact"}
    assert "/doctor" not in rows  # subsequence "c..o" must not match


def test_prefix_m_matches_only_prefixes():
    rows = _ids("m")
    assert "/model" in rows and "/memory" in rows
    # stream/compact contain 'm' but not as a prefix.
    assert "/stream" not in rows
    assert "/compact" not in rows


def test_alias_prefix_still_matches():
    assert "/tasks" in _ids("bg")


def test_part_prefix_matches_namespaced_commands():
    # TS Fuse partKey: a prefix of any [:_-] part matches.
    assert "/release-notes" in _ids("notes")
    assert "/plan-ceo-review" in _ids("ceo")
    # ...but only at a part boundary — "eo" is mid-part in "ceo", not a
    # part prefix, so it must NOT match (no unbounded subsequence).
    assert "/plan-ceo-review" not in _ids("eo")


def test_exact_name_ranks_first():
    # "/co" → cost (4) sorts before compact (7) by length tiebreak.
    assert _ids("co")[0] == "/cost"


def test_display_capped_at_six():
    assert _MAX_VISIBLE_SUGGESTIONS == 6
    # Empty partial returns every command, but display is capped.
    assert len(_ids("")) <= 6


# ---- widget harness -------------------------------------------------------


class _Host(App):
    def __init__(self, prompt: PromptInput) -> None:
        super().__init__()
        self._prompt = prompt
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield self._prompt

    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        self.submitted.append(message.text)


def _make_prompt() -> PromptInput:
    return PromptInput(
        words_provider=lambda: [],
        suggestions_provider=lambda: list(_SUGGESTIONS),
    )


async def _type(pilot, text: str) -> None:
    for ch in text:
        await pilot.press("/" if ch == "/" else ch)
    await pilot.pause()


@pytest.mark.asyncio
async def test_enter_executes_zero_arg_command_without_navigation():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _type(pilot, "/he")  # only /help matches
        await pilot.press("enter")
        await pilot.pause()
        assert host.submitted == ["/help"]
        assert prompt.current_text() == ""  # input cleared on execute


@pytest.mark.asyncio
async def test_enter_after_arrow_nav_executes_highlighted():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _type(pilot, "/")  # empty partial → insertion order
        await pilot.press("down")  # move highlight to index 1 = /stream
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # The headline bug: this used to be a no-op (OptionSelected unhandled).
        assert host.submitted == ["/stream"]
        assert prompt.current_text() == ""


@pytest.mark.asyncio
async def test_tab_fills_without_executing():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _type(pilot, "/mod")  # only /model
        await pilot.press("tab")
        await pilot.pause()
        assert host.submitted == []  # Tab never runs the command
        assert prompt.current_text() == "/model "  # filled with trailing space


@pytest.mark.asyncio
async def test_enter_fills_arg_taking_command():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _type(pilot, "/gr")  # /greet, arg_names=("name",)
        await pilot.press("enter")
        await pilot.pause()
        # Arg-taking command fills so the user can type the argument.
        assert host.submitted == []
        assert prompt.current_text() == "/greet "

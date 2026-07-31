"""/fusion command grammar.

The command owns parsing and message text; persistence is covered by
``tests/providers/test_fusion_models.py`` and the control bridge by
``tests/server/test_fusion_control.py``. Global config is isolated per test
by the autouse fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.command_system.fusion_command import fusion_command_call
from src.command_system.types import CommandContext

BASE = "deepseek:deepseek-v4-pro"
VISION = "openrouter:google/gemini-2.5-flash"


@pytest.fixture(autouse=True)
def _configured_providers(monkeypatch):
    import src.providers.fusion_models as mod

    monkeypatch.setattr(
        mod, "_configured_providers", lambda: ["deepseek", "openrouter", "zai"]
    )


@pytest.fixture
def ctx(tmp_path):
    return CommandContext(
        workspace_root=tmp_path,
        cwd=tmp_path,
        conversation=None,
        cost_tracker=None,
        history=None,
        app_state_store=None,
        provider=None,
    )


def run(args: str, ctx) -> str:
    return fusion_command_call(args, ctx).value


# ── list ─────────────────────────────────────────────────────────────────


def test_bare_lists_and_teaches_when_empty(ctx):
    out = run("", ctx)
    assert "No fusion models saved" in out
    # An empty list is the first thing a new user sees, so it explains what
    # the feature is for rather than just saying "none".
    assert "deepseek-v4-pro" in out
    assert "/fusion create" in out


@pytest.mark.parametrize("alias", ["list", "ls", "status"])
def test_list_aliases(alias, ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    assert "dsv" in run(alias, ctx)


def test_list_shows_both_halves_and_the_select_hint(ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    out = run("list", ctx)
    assert BASE in out
    assert VISION in out
    assert "/model" in out


# ── create ───────────────────────────────────────────────────────────────


def test_create_reports_both_halves(ctx):
    out = run(f"create dsv {BASE} {VISION}", ctx)
    assert "Created fusion model 'dsv'" in out
    assert BASE in out
    assert VISION in out
    assert "/model dsv" in out


@pytest.mark.parametrize("alias", ["create", "add", "new"])
def test_create_aliases(alias, ctx):
    assert "Created" in run(f"{alias} n-{alias} {BASE} {VISION}", ctx)


def test_create_without_name_derives_one(ctx):
    out = run(f"create {BASE} {VISION}", ctx)
    assert "deepseek-v4-pro-V" in out


def test_create_with_extra_whitespace(ctx):
    # Pasting the multi-line example collapses to arbitrary whitespace.
    assert "Created" in run(f"create   dsv    {BASE}     {VISION}", ctx)


def test_create_wrong_arity_shows_usage(ctx):
    out = run("create only-one-arg", ctx)
    assert "Usage: /fusion create" in out


def test_create_bad_selector_names_the_half(ctx):
    assert "base" in run(f"create x nocolon {VISION}", ctx)
    assert "vision" in run(f"create x {BASE} nocolon", ctx)


def test_create_surfaces_validation_errors_verbatim(ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    assert "already exists" in run(f"create dsv {BASE} {VISION}", ctx)


def test_create_rejects_text_only_vision_model(ctx):
    # The glm-5.2 trap, surfaced at the command layer.
    out = run(f"create x {BASE} zai:glm-5.2", ctx)
    assert "does not support image input" in out
    assert "glm-4.5v" in out  # points at the models that do


# ── delete / enable / disable ────────────────────────────────────────────


@pytest.mark.parametrize("alias", ["delete", "remove", "rm", "del"])
def test_delete_aliases(alias, ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    assert "Deleted" in run(f"{alias} dsv", ctx)


def test_delete_unknown_lists_known(ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    out = run("delete nope", ctx)
    assert "No fusion model named 'nope'" in out
    assert "Known: dsv" in out


def test_delete_wrong_arity(ctx):
    assert "Usage: /fusion delete <name>" in run("delete", ctx)


def test_disable_then_enable(ctx):
    run(f"create dsv {BASE} {VISION}", ctx)
    out = run("disable dsv", ctx)
    assert "disabled" in out
    assert "re-enabled" in out       # says the config is kept
    assert "enabled" in run("enable dsv", ctx)


def test_enable_unknown(ctx):
    assert "No fusion model named" in run("enable nope", ctx)


def test_enable_wrong_arity_names_the_verb(ctx):
    assert "Usage: /fusion enable <name>" in run("enable", ctx)
    assert "Usage: /fusion disable <name>" in run("disable", ctx)


# ── help / unknown ───────────────────────────────────────────────────────


@pytest.mark.parametrize("arg", ["help", "-h", "--help"])
def test_help(arg, ctx):
    out = run(arg, ctx)
    assert "Usage: /fusion" in out
    assert "provider>:<model" in out
    # A copy-pasteable example is the fastest path to a working setup.
    assert "/fusion create deepseek-v4-pro-V" in out


def test_unknown_action_shows_usage(ctx):
    out = run("frobnicate", ctx)
    assert "Unknown /fusion action 'frobnicate'" in out
    assert "Usage: /fusion" in out


def test_action_is_case_insensitive(ctx):
    assert "Created" in run(f"CREATE dsv {BASE} {VISION}", ctx)
    assert "dsv" in run("LIST", ctx)


def test_result_is_always_text_type(ctx):
    # The control bridge reads ``.value`` and the TUI prints it; a non-text
    # result type would be dropped.
    for args in ["", "help", "bogus", f"create dsv {BASE} {VISION}", "delete dsv"]:
        assert fusion_command_call(args, ctx).type == "text"

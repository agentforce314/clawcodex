"""Tests for the ``/model`` command (Phase 7 — port of TS local-jsx).

Mirrors the ``/theme``/``/effort`` test layout. ``/model`` is the **inverse** of
``/export`` at the TUI dispatch layer: the TUI keeps intercepting ``/model`` (+ ``/models``)
→ ``open_dialog="model"``; the ``ModelCommand`` serves the registry-consulting surfaces.
It is **functional** — it sets the live ``provider.model`` (the channel inference reads) via
``ctx.provider`` — so the tests drive a ``FakeProvider`` with a settable ``.model`` and a
``get_available_models()`` list.

Sections:
  * A — metadata + registration.
  * B — bridge-safety by type + TUI dispatch inversion (``/model`` AND ``/models``).
  * C — set-by-name (headless): alias-resolve + membership; not-found; honest-failure; empty-list.
  * D — picker: set / cancel ("Kept model as …") / no-models.
  * E — info / help / refresh (+ effort suffix from settings).
  * F — null surface: no-args picker raises; engine clean error; arg path works headless.
  * G — D1: the REPL wires ``provider`` into its command context.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.command_system import (
    MODEL_COMMAND,
    ModelCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import (
    CommandType,
    InteractiveOutcome,
    InteractiveUnavailableError,
    NullUIHost,
)
from src.models.model import canonical_model_name, display_name

_SONNET = "claude-sonnet-4-20250514"
_OPUS = "claude-opus-4-20250514"


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #
class FakeUIHost:
    def __init__(self, *, pick=None):
        self._pick = pick
        self.select_calls: list[dict] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {
                "title": title,
                "values": [o.value for o in options],
                "labels": [o.label for o in options],
                "descriptions": [o.description for o in options],
                "current": current,
            }
        )
        return self._pick

    async def prompt_text(self, title, *, default="", placeholder=None):
        return None

    async def display(self, title, body):
        return None


class FakeProvider:
    def __init__(self, model=_SONNET, models=None):
        self.model = model
        self._models = [_SONNET, _OPUS] if models is None else list(models)

    def get_available_models(self):
        return list(self._models)


def _ctx(tmp_path: Path, *, ui=None, provider=None):
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, ui=ui, provider=provider
    )


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


@pytest.fixture(autouse=True)
def _no_effort(monkeypatch):
    """Default: no persisted effort, so ``current`` has no effort suffix unless a test
    overrides it (the suffix reads ``settings.effort`` via ``get_settings``)."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.settings.settings.get_settings",
        lambda *a, **k: SimpleNamespace(effort=""),
    )


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """#280: ``_apply`` now persists the model choice to the global
    config — point the writer at a per-test file (the theme/vim-command
    pattern) so tests never touch the developer's real config."""
    import src.config as cfg

    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_model_registered_in_builtins_and_aggregator():
    assert "model" in {c.name for c in get_builtin_commands()}
    assert "model" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_model_metadata_mirrors_ts():
    assert isinstance(MODEL_COMMAND, ModelCommand)
    assert MODEL_COMMAND.name == "model"
    assert MODEL_COMMAND.description == "Set the AI model"
    assert MODEL_COMMAND.argument_hint == "[model]"
    assert MODEL_COMMAND.command_type == CommandType.INTERACTIVE
    assert MODEL_COMMAND.disable_model_invocation is True
    assert MODEL_COMMAND.is_hidden is False


# --------------------------------------------------------------------------- #
# B. Bridge-safety + TUI dispatch inversion
# --------------------------------------------------------------------------- #
def test_model_blocked_from_bridge_by_type():
    assert is_bridge_safe_command(MODEL_COMMAND) is False


@pytest.mark.parametrize("name", ["/model", "/models"])
def test_dispatch_local_command_intercepts_model(name):
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        name, session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is True
    assert res.open_dialog == "model"


# --------------------------------------------------------------------------- #
# C. Set-by-name (headless)
# --------------------------------------------------------------------------- #
async def test_set_by_alias_resolves_and_sets(tmp_path):
    prov = FakeProvider(model=_SONNET)
    out = await MODEL_COMMAND.run("opus", _ctx(tmp_path, provider=prov))
    assert isinstance(out, InteractiveOutcome)
    assert prov.model == canonical_model_name("opus") == _OPUS
    assert out.message == f"Set model to {display_name(_OPUS)}"
    assert out.display == "user"


async def test_set_full_id(tmp_path):
    prov = FakeProvider(model=_OPUS)
    out = await MODEL_COMMAND.run(_SONNET, _ctx(tmp_path, provider=prov))
    assert prov.model == _SONNET
    assert out.message == f"Set model to {display_name(_SONNET)}"


async def test_set_unknown_is_not_found_and_does_not_change(tmp_path):
    prov = FakeProvider(model=_SONNET)
    out = await MODEL_COMMAND.run("bogus", _ctx(tmp_path, provider=prov))
    assert out.message == "Model 'bogus' not found"
    assert out.display == "system"
    assert prov.model == _SONNET  # unchanged


async def test_set_without_provider_is_honest_failure(tmp_path):
    out = await MODEL_COMMAND.run("opus", _ctx(tmp_path, provider=None))
    assert out.message == "Model unavailable (no active provider)."
    assert out.display == "system"


async def test_set_permissive_when_provider_lists_nothing(tmp_path):
    # Unknown provider (empty list) => membership skipped => the id is set.
    prov = FakeProvider(model=_SONNET, models=[])
    out = await MODEL_COMMAND.run("zai/glm-5", _ctx(tmp_path, provider=prov))
    assert prov.model == "zai/glm-5"  # actually changed (not tautological)
    assert out.message == f"Set model to {display_name('zai/glm-5')}"


# --------------------------------------------------------------------------- #
# D. Picker
# --------------------------------------------------------------------------- #
async def test_picker_sets_model(tmp_path):
    prov = FakeProvider(model=_SONNET)
    ui = FakeUIHost(pick=_OPUS)
    out = await MODEL_COMMAND.run("", _ctx(tmp_path, ui=ui, provider=prov))
    assert prov.model == _OPUS
    assert out.message == f"Set model to {display_name(_OPUS)}"
    assert out.display == "user"
    call = ui.select_calls[0]
    assert call["values"] == prov.get_available_models()
    assert call["current"] == _SONNET  # seeded from provider.model


async def test_picker_cancel_keeps_model(tmp_path):
    prov = FakeProvider(model=_SONNET)
    ui = FakeUIHost(pick=None)
    out = await MODEL_COMMAND.run("", _ctx(tmp_path, ui=ui, provider=prov))
    assert out.message == f"Kept model as {display_name(_SONNET)}"
    assert out.display == "system"
    assert prov.model == _SONNET  # unchanged


async def test_picker_no_models(tmp_path):
    prov = FakeProvider(model=_SONNET, models=[])
    ui = FakeUIHost(pick=_OPUS)
    out = await MODEL_COMMAND.run("", _ctx(tmp_path, ui=ui, provider=prov))
    assert out.message == "No models available."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# E. Info / help / refresh
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("arg", ["current", "status", "show", "?"])
async def test_info_shows_current(tmp_path, arg):
    prov = FakeProvider(model=_OPUS)
    out = await MODEL_COMMAND.run(arg, _ctx(tmp_path, provider=prov))
    assert out.message == f"Current model: {display_name(_OPUS)}"
    assert out.display == "user"


async def test_info_includes_effort_suffix(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.settings.settings.get_settings",
        lambda *a, **k: SimpleNamespace(effort="high"),
    )
    prov = FakeProvider(model=_OPUS)
    out = await MODEL_COMMAND.run("current", _ctx(tmp_path, provider=prov))
    assert out.message == f"Current model: {display_name(_OPUS)} (effort: high)"


@pytest.mark.parametrize("arg", ["help", "-h", "--help"])
async def test_help(tmp_path, arg):
    prov = FakeProvider()
    before = prov.model
    out = await MODEL_COMMAND.run(arg, _ctx(tmp_path, provider=prov))
    assert out.message.startswith("Run /model to open the model selection menu")
    assert out.display == "system"
    assert prov.model == before  # no change


async def test_refresh_not_supported(tmp_path):
    prov = FakeProvider()
    out = await MODEL_COMMAND.run("refresh", _ctx(tmp_path, provider=prov))
    assert out.message == "Model refresh is not supported."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# F. Null surface (only the no-args picker needs a UI)
# --------------------------------------------------------------------------- #
async def test_picker_raises_on_null_surface(tmp_path):
    prov = FakeProvider()
    with pytest.raises(InteractiveUnavailableError):
        await MODEL_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost(), provider=prov))


async def test_engine_errors_cleanly_on_null_surface(tmp_path):
    prov = FakeProvider()
    reg = _registry_with(MODEL_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, provider=prov)
    assert ctx.ui is None  # engine substitutes NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/model")  # no args => picker => needs a surface

    assert result.success is False
    assert result.error is not None
    assert "interactive surface" in result.error


async def test_engine_arg_path_succeeds_headless(tmp_path):
    prov = FakeProvider(model=_SONNET)
    reg = _registry_with(MODEL_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, provider=prov)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/model opus")

    assert result.success is True
    assert prov.model == _OPUS


# --------------------------------------------------------------------------- #
# G. D1 — the REPL wires provider into its command context
# --------------------------------------------------------------------------- #
def test_repl_wires_provider_into_command_context():
    import inspect

    from src.repl.core import ClawcodexREPL

    src_text = inspect.getsource(ClawcodexREPL._init_command_system)
    assert "provider=self.provider" in src_text


# --------------------------------------------------------------------------- #
# H. #280: the choice persists for the next launch
# --------------------------------------------------------------------------- #
async def test_set_persists_choice_for_restart(tmp_path):
    """/model <name> writes settings.model paired with the provider key;
    get_persisted_model restores it for the same provider only."""
    from src.providers.anthropic_provider import AnthropicProvider

    prov = AnthropicProvider(api_key="test", model="claude-sonnet-4-6")
    out = await MODEL_COMMAND.run("claude-opus-4-6", _ctx(tmp_path, provider=prov))
    assert "Set model to" in out.message

    from src.settings.settings import get_persisted_model, invalidate_settings_cache

    invalidate_settings_cache()  # simulate restart
    assert get_persisted_model("anthropic", cwd=tmp_path) == "claude-opus-4-6"
    assert get_persisted_model("glm", cwd=tmp_path) is None


async def test_set_with_unknown_provider_class_does_not_pair(tmp_path):
    """FakeProvider isn't a registered provider class — the model is
    written unpaired and therefore not restored (fail-safe)."""
    prov = FakeProvider(model=_SONNET)
    await MODEL_COMMAND.run("opus", _ctx(tmp_path, provider=prov))

    from src.settings.settings import get_persisted_model, invalidate_settings_cache

    invalidate_settings_cache()
    assert get_persisted_model("anthropic", cwd=tmp_path) is None

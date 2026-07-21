"""PLUGINS-1 — the karpathy-guidelines bundled builtin plugin.

Registry was ported 6/6 but INERT (zero registrations/consumers); this
phase ports the one bundled plugin verbatim and inits it at startup.
"""
from __future__ import annotations

import pytest

from src.plugins.builtin_plugins import (
    clear_builtin_plugins,
    get_builtin_plugin_definition,
    get_builtin_plugins,
)
from src.plugins.init_builtin import init_builtin_plugins


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_builtin_plugins()
    yield
    clear_builtin_plugins()


def test_init_registers_karpathy_default_disabled():
    init_builtin_plugins()
    init_builtin_plugins()  # idempotent
    ps = get_builtin_plugins()
    assert [p.name for p in ps["disabled"]] == ["karpathy-guidelines"]
    assert ps["enabled"] == []


def test_prompt_verbatim_pin():
    """The prompt is model-facing eval-tuned prose, mechanically extracted
    from the TS template literal — pin its size and section headers."""
    from src.plugins.karpathy_guidelines import KARPATHY_GUIDELINES_PROMPT as P

    # 2351 in the TS original; +3 for the deliberate context-file rebrand
    # in the header (# CLAUDE.md -> # CLAWCODEX.md).
    assert len(P) == 2354
    for header in (
        "## 1. Think Before Coding",
        "## 2. Simplicity First",
        "## 3. Surgical Changes",
        "## 4. Goal-Driven Execution",
    ):
        assert header in P


def test_skill_shape_and_content():
    """The registry filter requires a real Skill instance; its content is
    the verbatim prompt (args handling is the skill runner's job)."""
    from src.skills.model import Skill
    from src.plugins.karpathy_guidelines import KARPATHY_GUIDELINES_PROMPT

    init_builtin_plugins()
    d = get_builtin_plugin_definition("karpathy-guidelines")
    skill = d.skills[0]
    assert isinstance(skill, Skill)
    assert skill.content == KARPATHY_GUIDELINES_PROMPT
    assert skill.user_invocable is True
    assert skill.loaded_from == "plugin"


def test_skill_command_exposed_only_when_enabled(monkeypatch, tmp_path):
    """commands.ts:401 analog: the skill surfaces as a command ONLY when the
    plugin is enabled (settings overlay over default_enabled=False)."""
    from src.command_system.aggregator import get_commands

    init_builtin_plugins()
    d1 = tmp_path / "a"; d1.mkdir()
    d2 = tmp_path / "b"; d2.mkdir()  # distinct cwd — get_commands caches per cwd
    names_disabled = {c.name for c in get_commands(cwd=str(d1))}
    assert "karpathy-guidelines" not in names_disabled  # default off

    import src.settings.settings as settings_mod

    class _S:
        extra = {"enabledPlugins": {"karpathy-guidelines@builtin": True}}
    monkeypatch.setattr(settings_mod, "load_settings", lambda **kw: _S())
    names_enabled = {c.name for c in get_commands(cwd=str(d2))}
    assert "karpathy-guidelines" in names_enabled


def test_enabled_command_renders_guidelines(monkeypatch, tmp_path):
    """B1 execution pin (critic): the enabled command must RENDER the
    guidelines — both the headless fallback and the skill-runner path."""
    import src.settings.settings as settings_mod

    class _S:
        extra = {"enabledPlugins": {"karpathy-guidelines@builtin": True}}
    monkeypatch.setattr(settings_mod, "load_settings", lambda **kw: _S())
    init_builtin_plugins()
    from src.command_system.aggregator import get_commands

    import asyncio

    cmd = next(c for c in get_commands(cwd=str(tmp_path)) if c.name == "karpathy-guidelines")
    rendered = asyncio.run(cmd.get_prompt_for_command("", None))
    # Content-block list (the canonical prompt-command shape).
    text = "".join(
        b.get("text", "") for b in rendered if isinstance(b, dict)
    ) if isinstance(rendered, list) else str(rendered)
    assert "## 1. Think Before Coding" in text
    assert len(text) >= 2351


def test_skill_tool_resolves_enabled_plugin_skill(monkeypatch):
    """B2 pin: get_registered_skill (the Skill tool's resolution path) finds
    the ENABLED plugin skill; a disabled plugin's skill never leaks."""
    import src.settings.settings as settings_mod
    from src.skills.loader import get_registered_skill

    init_builtin_plugins()
    assert get_registered_skill("karpathy-guidelines") is None  # default off

    class _S:
        extra = {"enabledPlugins": {"karpathy-guidelines@builtin": True}}
    monkeypatch.setattr(settings_mod, "load_settings", lambda **kw: _S())
    skill = get_registered_skill("karpathy-guidelines")
    assert skill is not None
    assert "## 1. Think Before Coding" in (skill.markdown_content or skill.content)


def test_string_list_override_is_disabled(monkeypatch):
    """TS parity: enabledPlugins values are boolean|string[]; only literal
    True enables (userSetting === true)."""
    import src.settings.settings as settings_mod

    class _S:
        extra = {"enabledPlugins": {"karpathy-guidelines@builtin": ["1.0.0"]}}
    monkeypatch.setattr(settings_mod, "load_settings", lambda **kw: _S())
    init_builtin_plugins()
    ps = get_builtin_plugins()
    assert [p.name for p in ps["disabled"]] == ["karpathy-guidelines"]

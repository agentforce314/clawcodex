"""Tests for src/command_system/aggregator.py.

Port-parity tests for get_commands(cwd) / clear_commands_cache() — the unified
command aggregator (typescript/src/commands.ts:473-541). Covers: builtins+skills
merge, availability + is_enabled filtering (re-evaluated fresh each call), name
de-duplication under the "builtins own their names" rule, per-cwd skill-cache
memoization, and skill-loader-failure resilience.
"""

from __future__ import annotations

import pytest

import src.skills.loader as skills_loader
from src.command_system import aggregator
from src.command_system.aggregator import clear_commands_cache, get_commands
from src.command_system.types import CommandAvailability, PromptCommand
from src.skills.model import Skill


@pytest.fixture(autouse=True)
def _clear_cmd_cache():
    """The skill-command cache is process-global (lru_cache); isolate each test."""
    clear_commands_cache()
    yield
    clear_commands_cache()


def test_baseline_includes_builtins():
    names = {c.name for c in get_commands()}
    assert {"auto-fix", "review", "help", "compact"} <= names


def test_no_duplicate_names():
    names = [c.name for c in get_commands()]
    assert len(names) == len(set(names))


def test_disabled_builtin_is_filtered_out(monkeypatch):
    disabled = PromptCommand(
        name="zzz-disabled", description="d", is_enabled=lambda: False,
    )
    monkeypatch.setattr(aggregator, "get_builtin_commands", lambda: [disabled])
    monkeypatch.setattr(skills_loader, "get_all_skills", lambda **kw: [])
    assert "zzz-disabled" not in {c.name for c in get_commands()}


def test_availability_gate_excludes_when_not_subscriber(monkeypatch):
    gated = PromptCommand(
        name="zzz-claude-ai",
        description="d",
        availability=[CommandAvailability.CLAUDE_AI],
    )
    monkeypatch.setattr(aggregator, "get_builtin_commands", lambda: [gated])
    monkeypatch.setattr(skills_loader, "get_all_skills", lambda **kw: [])

    excluded = {c.name for c in get_commands(is_claude_ai_subscriber=False)}
    assert "zzz-claude-ai" not in excluded

    included = {c.name for c in get_commands(is_claude_ai_subscriber=True)}
    assert "zzz-claude-ai" in included


def test_builtin_wins_dedupe_when_both_enabled(monkeypatch):
    builtin_help = PromptCommand(name="help", description="builtin", source="builtin")
    skill_help = Skill(name="help", description="skill", loaded_from="skills")
    monkeypatch.setattr(aggregator, "get_builtin_commands", lambda: [builtin_help])
    monkeypatch.setattr(skills_loader, "get_all_skills", lambda **kw: [skill_help])

    helps = [c for c in get_commands() if c.name == "help"]
    assert len(helps) == 1
    assert helps[0].source == "builtin"


def test_builtin_wins_dedupe_even_when_builtin_disabled(monkeypatch):
    """Reserve-name-first rule: a disabled builtin still claims its name, so a
    same-named *enabled* skill cannot leak through. Guards the deliberate
    divergence from TS's filter-then-dedupe ordering.
    """
    disabled_builtin = PromptCommand(
        name="zzz", description="builtin", source="builtin", is_enabled=lambda: False,
    )
    enabled_skill = Skill(name="zzz", description="skill", loaded_from="skills")
    monkeypatch.setattr(
        aggregator, "get_builtin_commands", lambda: [disabled_builtin]
    )
    monkeypatch.setattr(skills_loader, "get_all_skills", lambda **kw: [enabled_skill])

    assert "zzz" not in {c.name for c in get_commands()}


def test_skill_load_is_memoized_per_cwd(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_get_all_skills(*, project_root=None, user_skills_dir=None):
        calls["n"] += 1
        return []

    # Patch the module attribute: the function-local `from ..skills.loader import
    # get_all_skills` inside _load_skill_commands_cached resolves against this.
    monkeypatch.setattr(skills_loader, "get_all_skills", fake_get_all_skills)

    cwd = str(tmp_path)
    get_commands(cwd)
    get_commands(cwd)
    assert calls["n"] == 1  # second call served from the per-cwd cache

    clear_commands_cache()
    get_commands(cwd)
    assert calls["n"] == 2  # cache cleared -> recomputed


def test_skill_load_failure_is_resilient(monkeypatch, tmp_path):
    def boom(*, project_root=None, user_skills_dir=None):
        raise RuntimeError("skill discovery exploded")

    monkeypatch.setattr(skills_loader, "get_all_skills", boom)

    # A crashing skill loader must not take down command listing; builtins survive.
    assert "help" in {c.name for c in get_commands(str(tmp_path))}

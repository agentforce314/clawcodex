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
from src.command_system.aggregator import (
    clear_commands_cache,
    get_commands,
    get_skill_tool_commands,
    get_slash_command_tool_skills,
)
from src.command_system.skills_integration import skill_to_prompt_command
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


# ---------------------------------------------------------------------------
# P0-4 model-tool exposure views (Phase 3): get_skill_tool_commands /
# get_slash_command_tool_skills — typescript/src/commands.ts:587-632.
#
# Strategy: drive the real get_commands pipeline by patching the same two seams
# the tests above use (get_builtin_commands + skills_loader.get_all_skills), so
# Skill objects round-trip through skill_to_prompt_command (this also exercises
# the R2 has_user_specified_description propagation end-to-end). The autouse
# _clear_cmd_cache fixture clears the view caches too (clear_commands_cache now
# resets all three lru_caches), isolating each test.
# ---------------------------------------------------------------------------


def _skill(
    name: str,
    *,
    loaded_from: str = "skills",
    disable_model_invocation: bool = False,
    has_user_specified_description: bool = False,
    when_to_use=None,
    description: str = "a description",
) -> Skill:
    return Skill(
        name=name,
        description=description,
        loaded_from=loaded_from,
        disable_model_invocation=disable_model_invocation,
        has_user_specified_description=has_user_specified_description,
        when_to_use=when_to_use,
    )


def _install(monkeypatch, *, builtins=(), skills=()):
    """Point the aggregator at a controlled builtin + skill set."""
    monkeypatch.setattr(aggregator, "get_builtin_commands", lambda: list(builtins))
    monkeypatch.setattr(skills_loader, "get_all_skills", lambda **kw: list(skills))


# -- get_skill_tool_commands -------------------------------------------------


def test_skill_tool_commands_includes_disk_and_bundled(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        skills=[_skill("disk", loaded_from="skills"), _skill("bun", loaded_from="bundled")],
    )
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert {"disk", "bun"} <= names


def test_skill_tool_commands_excludes_builtins(monkeypatch, tmp_path):
    builtin = PromptCommand(name="init", description="d", source="builtin")
    _install(monkeypatch, builtins=[builtin], skills=[_skill("real", loaded_from="skills")])
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert "init" not in names
    assert "real" in names


def test_skill_tool_commands_excludes_disable_model_invocation(monkeypatch, tmp_path):
    # Security boundary: a skill the author marked non-model-invocable must never
    # be advertised to the model (mirrors /permissions, which is also excluded by
    # the command_type==PROMPT gate as a non-prompt command).
    _install(
        monkeypatch,
        skills=[_skill("secret", loaded_from="skills", disable_model_invocation=True)],
    )
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert "secret" not in names


def test_skill_tool_commands_d1a_user_project_included(monkeypatch, tmp_path):
    # D-1a regression guard: a user/project disk skill with only an auto-derived
    # description (has_user_specified_description=False, when_to_use=None) MUST be
    # included. A literal port of TS `loadedFrom==='skills'` would wrongly drop it.
    _install(
        monkeypatch,
        skills=[
            _skill("u", loaded_from="user"),
            _skill("p", loaded_from="project"),
        ],
    )
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert {"u", "p"} <= names


def test_skill_tool_commands_managed_without_metadata_excluded(monkeypatch, tmp_path):
    # The flip side of D-1a: 'managed' is NOT in the auto-include bucket, so a
    # managed skill with no author description / when_to_use is withheld.
    _install(monkeypatch, skills=[_skill("m", loaded_from="managed")])
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert "m" not in names


def test_skill_tool_commands_managed_with_description_included(monkeypatch, tmp_path):
    # ...but the escape hatch fires the moment it has an author-written description.
    _install(
        monkeypatch,
        skills=[_skill("m", loaded_from="managed", has_user_specified_description=True)],
    )
    names = {c.name for c in get_skill_tool_commands(str(tmp_path))}
    assert "m" in names


# -- get_slash_command_tool_skills -------------------------------------------


def test_slash_skills_requires_description_or_when_to_use(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        skills=[
            _skill("bare", loaded_from="skills"),  # no desc flag, no when_to_use
            _skill("described", loaded_from="skills", has_user_specified_description=True),
        ],
    )
    names = {c.name for c in get_slash_command_tool_skills(str(tmp_path))}
    assert "bare" not in names
    assert "described" in names


def test_slash_skills_user_project_with_when_to_use_included(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        skills=[_skill("u", loaded_from="user", when_to_use="when editing")],
    )
    names = {c.name for c in get_slash_command_tool_skills(str(tmp_path))}
    assert "u" in names


def test_slash_skills_includes_disable_model_invocation(monkeypatch, tmp_path):
    # Deliberate TS asymmetry: here disable_model_invocation is an *inclusion*
    # clause (not an exclusion). With a description present, a managed skill that
    # is NOT in the bucket still counts via disable_model_invocation=True.
    _install(
        monkeypatch,
        skills=[
            _skill(
                "dmi",
                loaded_from="managed",
                disable_model_invocation=True,
                has_user_specified_description=True,
            )
        ],
    )
    names = {c.name for c in get_slash_command_tool_skills(str(tmp_path))}
    assert "dmi" in names


def test_slash_skills_excludes_builtins(monkeypatch, tmp_path):
    builtin = PromptCommand(
        name="review", description="d", source="builtin",
        has_user_specified_description=True,
    )
    _install(monkeypatch, builtins=[builtin], skills=[])
    names = {c.name for c in get_slash_command_tool_skills(str(tmp_path))}
    assert "review" not in names


def test_slash_skills_returns_empty_on_failure(monkeypatch, tmp_path):
    # The whole body is wrapped in try/except -> () (TS parity; skills non-critical).
    def boom(cwd=None, **kw):
        raise RuntimeError("aggregator exploded")

    monkeypatch.setattr(aggregator, "get_commands", boom)
    assert get_slash_command_tool_skills(str(tmp_path)) == ()


# -- R2 propagation + caching ------------------------------------------------


def test_has_user_specified_description_propagates():
    # R2: skill_to_prompt_command must carry the loader-computed flag through so
    # the views' description clause can fire.
    on = skill_to_prompt_command(_skill("x", has_user_specified_description=True))
    off = skill_to_prompt_command(_skill("y", has_user_specified_description=False))
    assert on.has_user_specified_description is True
    assert off.has_user_specified_description is False


def test_views_are_memoized_and_cleared(monkeypatch, tmp_path):
    calls = {"n": 0}

    def counting(cwd=None, **kw):
        calls["n"] += 1
        return []

    monkeypatch.setattr(aggregator, "get_commands", counting)
    cwd = str(tmp_path)
    get_skill_tool_commands(cwd)
    get_skill_tool_commands(cwd)
    assert calls["n"] == 1  # second call served from the view's cwd cache

    clear_commands_cache()
    get_skill_tool_commands(cwd)
    assert calls["n"] == 2  # clear_commands_cache resets the view cache too

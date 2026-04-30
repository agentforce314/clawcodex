"""Group A — Unified registry (covers DEV-1).

Verifies that disk-loaded skills (including nested-namespace skills) and
bundled skills are reachable through the single ``get_all_skills`` /
``SkillTool`` entry point. Before DEV-1 the SkillTool only saw the
narrower ``PromptSkill`` registry; these tests pin the unified path so a
regression on the wiring fails loudly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled_skills import (
    BundledSkillDefinition,
    clear_bundled_skills,
    register_bundled_skill,
)
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    get_all_skills,
    get_registered_skill,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools import SkillTool


# ----------------------------------------------------------------------
# Test isolation: every test gets a clean ``HOME`` (so ``~/.claude`` /
# ``~/.clawcodex`` lookups don't leak from the developer's machine) and
# every skill cache is cleared before AND after each test.
# ----------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Strip every env knob that would inject extra skill dirs.
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_MANAGED_CONFIG_DIR",
        "CLAWCODEX_SKILLS_DIR",
        "CLAUDE_SKILLS_DIR",
        "CLAWCODEX_MANAGED_SKILLS_DIR",
        "CLAUDE_CODE_BARE_MODE",
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
    ):
        monkeypatch.delenv(var, raising=False)
    # Send the managed-policy lookup to an empty tmp dir so /etc/claude
    # never affects results.
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    yield home


@pytest.fixture(autouse=True)
def _clean_skill_state() -> Iterator[None]:
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()
    yield
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ======================================================================
# A1. Flat project skill at ``.claude/skills/foo/SKILL.md`` is
# invokable through SkillTool with skill="foo".
# ======================================================================


def test_flat_project_skill_invokable_via_skilltool(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "foo" / "SKILL.md",
        "---\ndescription: foo skill\n---\nfoo body",
    )

    ctx = ToolContext(workspace_root=project)
    result = SkillTool.call({"skill": "foo"}, ctx)
    out = result.output
    assert out["success"] is True, f"expected success, got: {out}"
    assert out["commandName"] == "foo"
    assert "foo body" in out["prompt"]


# ======================================================================
# A2. Nested namespace lookup — the previously-broken case from DEV-1.
# ``.claude/skills/git/commit/SKILL.md`` must be invokable as
# ``skill: "git:commit"`` through SkillTool.
# ======================================================================


def test_nested_namespace_skill_invokable_as_colon_form(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "git" / "commit" / "SKILL.md",
        "---\ndescription: git commit skill\n---\nWrite a commit message",
    )

    ctx = ToolContext(workspace_root=project)
    result = SkillTool.call({"skill": "git:commit"}, ctx)
    out = result.output
    assert out["success"] is True, (
        "nested-namespace lookup is the previously-broken case from "
        f"DEV-1; SkillTool must resolve 'git:commit' through the "
        f"unified registry. Got: {out}"
    )
    assert out["commandName"] == "git:commit"
    assert "Write a commit message" in out["prompt"]


def test_nested_namespace_appears_in_get_all_skills(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "deep" / "nested" / "thing" / "SKILL.md",
        "---\ndescription: deep skill\n---\nbody",
    )

    skills = get_all_skills(project_root=project)
    by_name = {s.name: s for s in skills}
    # `_get_skill_command_name` joins ALL relative-path segments with
    # `:` (every dir between the skills root and the skill folder
    # becomes a namespace component).
    assert "deep:nested:thing" in by_name, (
        f"expected nested namespace 'deep:nested:thing' in {sorted(by_name)}"
    )


# ======================================================================
# A3. Bundled skills registered via ``register_bundled_skill`` are
# discoverable through SkillTool.
# ======================================================================


def test_bundled_skill_discoverable_through_skilltool(
    tmp_path: Path, isolated_home: Path
) -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="bundled-thing",
            description="A bundled skill",
            get_prompt_for_command=lambda args: f"bundled prompt: {args}",
        )
    )

    project = tmp_path / "proj"
    project.mkdir()
    ctx = ToolContext(workspace_root=project)
    result = SkillTool.call({"skill": "bundled-thing", "args": "hello"}, ctx)
    out = result.output
    assert out["success"] is True
    assert out["commandName"] == "bundled-thing"
    assert "bundled prompt: hello" in out["prompt"]
    assert out["loadedFrom"] == "bundled"


# ======================================================================
# A4. Listing via ``get_all_skills(project_root=tmp)`` returns the union
# of bundled + disk-loaded skills.
# ======================================================================


def test_get_all_skills_returns_union_of_bundled_and_disk(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "diskskill" / "SKILL.md",
        "---\ndescription: disk skill\n---\nbody",
    )
    register_bundled_skill(
        BundledSkillDefinition(
            name="bundledskill",
            description="bundled",
            get_prompt_for_command=lambda a: a,
        )
    )

    skills = get_all_skills(project_root=project)
    names = {s.name for s in skills}
    assert "diskskill" in names, f"missing disk skill in {names}"
    assert "bundledskill" in names, f"missing bundled skill in {names}"


# ======================================================================
# A5. ``CLAWCODEX_SKILLS_DIR`` + the user-skills bucket are merged into
# the unified registry — env-var dirs are reachable via SkillTool.
# ======================================================================


def test_env_var_user_skill_dir_is_reachable_via_skilltool(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_skills = tmp_path / "extra_user_skills"
    _write_skill(
        user_skills / "envskill" / "SKILL.md",
        "---\ndescription: env skill\n---\nbody-from-env",
    )
    monkeypatch.setenv("CLAWCODEX_SKILLS_DIR", str(user_skills))

    project = tmp_path / "proj"
    project.mkdir()
    ctx = ToolContext(workspace_root=project)
    result = SkillTool.call({"skill": "envskill"}, ctx)
    out = result.output
    assert out["success"] is True
    assert "body-from-env" in out["prompt"]
    assert out["loadedFrom"] == "user"


# ======================================================================
# A6. ``get_registered_skill`` is the public lookup used by SkillTool's
# validate path. Calling ``get_all_skills`` first must populate it.
# ======================================================================


def test_get_registered_skill_populated_after_get_all_skills(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "lookup-me" / "SKILL.md",
        "---\ndescription: lookup\n---\nbody",
    )

    # Before populating, the registry should not have it.
    assert get_registered_skill("lookup-me") is None

    get_all_skills(project_root=project)
    found = get_registered_skill("lookup-me")
    assert found is not None
    assert found.name == "lookup-me"
    assert found.markdown_content == "body"

"""QA-2 — End-to-end real-skill tests.

Drops three example SKILL.md files (under ``tests/fixtures/skills/``)
into a workspace's ``.claude/skills/`` tree (via tmp-path symlink) and
exercises them through the full ``SkillTool`` pipeline:

    fixture SKILL.md  ──►  load via get_all_skills  ──►  invoke via
    SkillTool.call    ──►  rendered prompt with all transforms applied

The fixtures (live on disk under ``tests/fixtures/skills/``):

  - ``commit-helper``     simple skill with args, ``${CLAUDE_SKILL_DIR}``,
                          ``${CLAUDE_SESSION_ID}``, allowed-tools.
  - ``frontend/add-component``
                          nested-namespace skill (resolves as
                          ``frontend:add-component``); has ``when_to_use``
                          and a named arg.
  - ``lint-py``           conditional skill (paths-gated) that ALSO has a
                          ``!`pwd``` shell block — exercises both the
                          conditional-activation gate and the shell-exec-
                          in-prompt path end-to-end.

Plus one bundled-skill invocation (``simplify`` from DEV-5) to cover
the bundled-path wiring through SkillTool.

Acceptance criteria covered:
  1. ``pytest tests/test_skills_e2e.py -v`` passes.
  2. ``commit-helper`` test asserts arg, ``${CLAUDE_SKILL_DIR}``,
     ``${CLAUDE_SESSION_ID}``, base-dir header all present.
  3. ``frontend:add-component`` invocation by namespaced name succeeds.
  4. ``lint-py`` not invokable until
     ``activate_conditional_skills_for_paths`` matches.
  5. ``simplify`` bundled skill exercised end-to-end through SkillTool.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled import init_bundled_skills
from src.skills.bundled_skills import clear_bundled_skills
from src.skills.loader import (
    activate_conditional_skills_for_paths,
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    get_all_skills,
    get_conditional_skill_count,
    get_registered_skill,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools import SkillTool


FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "skills"
FIXTURE_NAMES = ("commit-helper", "frontend", "lint-py")


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate every env knob that would inject a non-fixture skill dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CLAWCODEX_SKILLS_DIR",
        "CLAUDE_SKILLS_DIR",
        "CLAWCODEX_MANAGED_SKILLS_DIR",
        "CLAUDE_CODE_BARE_MODE",
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
    ):
        monkeypatch.delenv(var, raising=False)
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


@pytest.fixture
def project_with_fixtures(tmp_path: Path, isolated_home: Path) -> Path:
    """Construct a workspace whose ``.claude/skills/`` mirrors the
    fixture catalogue.

    Each fixture under ``tests/fixtures/skills/<name>/`` is copied to
    ``<workspace>/.claude/skills/<name>/`` so the unified loader picks
    them up via the project-skills walk. We copy (rather than symlink)
    so the dedup-by-realpath logic doesn't collapse them with the
    fixtures dir itself.
    """
    project = tmp_path / "proj"
    skills_root = project / ".claude" / "skills"
    skills_root.mkdir(parents=True)

    for name in FIXTURE_NAMES:
        src = FIXTURES_ROOT / name
        dst = skills_root / name
        shutil.copytree(src, dst)

    return project


# ======================================================================
# 1. ``commit-helper`` — args + ${CLAUDE_SKILL_DIR} + ${CLAUDE_SESSION_ID}
#    + allowed-tools metadata + base-dir header.
# ======================================================================


def test_commit_helper_renders_all_substitutions(
    project_with_fixtures: Path,
) -> None:
    ctx = ToolContext(workspace_root=project_with_fixtures)
    ctx.session_id = "S-e2e-001"

    result = SkillTool.call(
        {"skill": "commit-helper", "args": "feat"},
        ctx,
    )
    out = result.output
    assert out["success"] is True, f"unexpected failure: {out}"
    prompt = out["prompt"]

    skill_dir = project_with_fixtures / ".claude" / "skills" / "commit-helper"
    expected_dir_str = str(skill_dir.resolve())

    # (a) base-dir header — exact format pinned by render_skill_prompt.
    expected_header = f"Base directory for this skill: {expected_dir_str}\n\n"
    assert prompt.startswith(expected_header), (
        f"missing/mangled base-dir header.\n"
        f"  expected start: {expected_header!r}\n"
        f"  actual start:   {prompt[:200]!r}"
    )

    # (b) $scope arg substitution — `feat` lands in the body.
    assert "in `feat` scope" in prompt

    # (c) ${CLAUDE_SKILL_DIR} resolved to the skill's actual abs path.
    assert f"Skill base: {expected_dir_str}" in prompt
    assert "${CLAUDE_SKILL_DIR}" not in prompt

    # (d) ${CLAUDE_SESSION_ID} resolved.
    assert "Session: S-e2e-001" in prompt
    assert "${CLAUDE_SESSION_ID}" not in prompt

    # (e) allowed-tools metadata propagated to the tool result.
    assert out["allowedTools"] == ["Bash", "Read"], (
        f"allowed-tools metadata didn't ride through: {out!r}"
    )
    assert out["loadedFrom"] == "project"


# ======================================================================
# 2. ``frontend:add-component`` — nested namespace + when_to_use +
#    named arg substitution.
# ======================================================================


def test_frontend_add_component_namespaced_invocation(
    project_with_fixtures: Path,
) -> None:
    # Listing first — confirms the namespace-construction logic.
    skills = get_all_skills(project_root=project_with_fixtures)
    by_name = {s.name: s for s in skills}
    assert "frontend:add-component" in by_name, (
        f"expected nested namespace 'frontend:add-component' in {sorted(by_name)}"
    )

    # `when_to_use` field rides onto the loaded Skill.
    assert by_name["frontend:add-component"].when_to_use == (
        "When the user asks to add a new React component."
    )

    # Invoke through SkillTool by the namespaced name.
    ctx = ToolContext(workspace_root=project_with_fixtures)
    result = SkillTool.call(
        {"skill": "frontend:add-component", "args": "Button"},
        ctx,
    )
    out = result.output
    assert out["success"] is True
    assert out["commandName"] == "frontend:add-component"
    # Named-arg substitution: `$name` → `Button`.
    assert "src/components/Button.tsx" in out["prompt"]


# ======================================================================
# 3. ``lint-py`` — conditional skill: NOT invokable until
#    ``activate_conditional_skills_for_paths`` matches.
#    Bonus: once activated, the embedded ``!`pwd`` shell block runs
#    end-to-end through BashTool, demonstrating shell-exec coverage.
# ======================================================================


def test_lint_py_not_invokable_until_activated(
    project_with_fixtures: Path,
) -> None:
    # Initial registry walk: ``lint-py`` is conditional, so it should
    # be held back from the unconditional list.
    skills = get_all_skills(project_root=project_with_fixtures)
    names = {s.name for s in skills}
    assert "lint-py" not in names, (
        f"conditional skill must NOT appear before path activation; "
        f"got: {sorted(names)}"
    )
    assert get_conditional_skill_count() >= 1, (
        "conditional bucket should hold lint-py"
    )

    # Activation with a non-matching path keeps it dormant.
    activated = activate_conditional_skills_for_paths(
        [str(project_with_fixtures / "docs" / "x.md")],
        str(project_with_fixtures),
    )
    assert "lint-py" not in activated

    # Activation with a matching path flips it on.
    py_path = project_with_fixtures / "src" / "foo.py"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("# placeholder")
    activated = activate_conditional_skills_for_paths(
        [str(py_path)], str(project_with_fixtures)
    )
    assert "lint-py" in activated, (
        "conditional skill should activate on matching path; "
        f"activated={activated}"
    )

    # After activation, `get_all_skills` must return lint-py — the
    # unified loader splices `_dynamic_skills` (where activated
    # conditionals live) into `_skill_registry` so the canonical
    # SkillTool lookup path sees them.
    skills_after = get_all_skills(project_root=project_with_fixtures)
    names_after = {s.name for s in skills_after}
    assert "lint-py" in names_after, (
        f"after activation, lint-py should be reachable via "
        f"get_all_skills; got: {sorted(names_after)}"
    )


def test_lint_py_after_activation_runs_shell_block_through_skilltool(
    project_with_fixtures: Path,
) -> None:
    """Once activated, lint-py's `!`pwd`` shell block must execute
    end-to-end via BashTool when invoked through SkillTool.

    The fix for QA bug #14 makes `get_all_skills` splice
    `_dynamic_skills` into `_skill_registry`, so the canonical SkillTool
    lookup path resolves activated conditional skills without any
    test-side promotion.
    """
    # Prime the registry + activate.
    get_all_skills(project_root=project_with_fixtures)
    py_path = project_with_fixtures / "src" / "foo.py"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("# placeholder")
    activate_conditional_skills_for_paths(
        [str(py_path)], str(project_with_fixtures)
    )

    ctx = ToolContext(workspace_root=project_with_fixtures)
    result = SkillTool.call({"skill": "lint-py"}, ctx)

    out = result.output
    assert out["success"] is True, f"lint-py invocation failed: {out}"
    prompt = out["prompt"]

    # Shell block ran: `!`pwd`` is replaced by the actual cwd path.
    assert "Working directory: " in prompt
    # The literal block is gone (substituted).
    assert "!`pwd`" not in prompt
    # The substituted output looks like a real path (starts with `/`).
    # We don't pin the exact path because the test runs in whatever
    # cwd pytest invoked from; what matters is that *something* path-
    # shaped landed there.
    after_marker = prompt.split("Working directory: ", 1)[1].split("\n", 1)[0]
    assert after_marker.startswith("/"), (
        f"shell exec should have produced a path; got: {after_marker!r}"
    )


# ======================================================================
# 4. Bundled skill — exercises DEV-5's init orchestrator wiring through
#    SkillTool. Picks ``simplify`` per the spec example.
# ======================================================================


def test_simplify_bundled_skill_invokable_through_skilltool(
    project_with_fixtures: Path,
) -> None:
    # DEV-5: init orchestrator registers the bundled-skill catalogue.
    init_bundled_skills()

    # Sanity: simplify is in the registry after init.
    skills = get_all_skills(project_root=project_with_fixtures)
    by_name = {s.name: s for s in skills}
    assert "simplify" in by_name, (
        f"bundled `simplify` missing after init; got: {sorted(by_name)}"
    )
    assert by_name["simplify"].loaded_from == "bundled"

    # Invoke through SkillTool — exercises the bundled `get_prompt_for_command`
    # branch in `_run_markdown_skill`.
    ctx = ToolContext(workspace_root=project_with_fixtures)
    result = SkillTool.call(
        {"skill": "simplify", "args": "focus on caching"},
        ctx,
    )
    out = result.output
    assert out["success"] is True, f"bundled simplify failed: {out}"
    prompt = out["prompt"]

    # Pin the bundled prompt's contract (matches DEV-5 simplify.py):
    assert "Phase 1: Identify Changes" in prompt
    assert "Phase 2: Launch Three Review Agents in Parallel" in prompt
    # User-supplied args land in the "Additional Focus" block.
    assert "focus on caching" in prompt
    # Bundled skills don't get a base-dir header (they have no skill_root).
    assert "Base directory for this skill" not in prompt
    assert out["loadedFrom"] == "bundled"


# ======================================================================
# 5. Catalogue listing — what the model sees in its system reminder.
#    All three disk fixtures + at least one bundled skill, all reachable
#    from the unified registry.
# ======================================================================


def test_catalogue_lists_disk_and_bundled_skills_together(
    project_with_fixtures: Path,
) -> None:
    init_bundled_skills()
    skills = get_all_skills(project_root=project_with_fixtures)
    names = {s.name for s in skills}

    # Disk fixtures (lint-py is conditional, so excluded from the
    # unconditional listing — that's by design).
    assert "commit-helper" in names
    assert "frontend:add-component" in names
    assert "lint-py" not in names, (
        "lint-py is conditional and must not appear until activated"
    )

    # Bundled (DEV-5).
    assert "simplify" in names

    # Source labels: disk skills report `project`, bundled report `bundled`.
    by_name = {s.name: s for s in skills}
    assert by_name["commit-helper"].loaded_from == "project"
    assert by_name["frontend:add-component"].loaded_from == "project"
    assert by_name["simplify"].loaded_from == "bundled"

"""Groups D & E — Symlink dedup, bare/policy modes, conditional paths,
and gitignore-aware dynamic discovery (covers DEV-4).
"""

from __future__ import annotations

import os
import subprocess
import unittest.mock as mock
from pathlib import Path
from typing import Iterator

import pytest

from src.skills.bundled_skills import clear_bundled_skills
from src.skills.loader import (
    activate_conditional_skills_for_paths,
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    discover_skill_dirs_for_paths,
    get_all_skills,
    get_conditional_skill_count,
    get_dynamic_skills,
    get_skill_dir_commands,
)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
# D — Dedup + bare/policy modes
# ======================================================================


def test_symlinked_skill_collapses_to_single_entry(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Two paths pointing at the same SKILL.md (via symlink) must
    collapse to one entry after realpath dedup."""
    project = tmp_path / "proj"
    real_dir = project / ".claude" / "skills" / "real"
    _write_skill(real_dir / "SKILL.md", "---\ndescription: real\n---\nbody")

    # Create a sibling symlinked dir that points at the same
    # SKILL.md-containing folder. After realpath dedup they should
    # collapse to one entry.
    link_dir = project / ".claude" / "skills" / "linked"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    skills = get_skill_dir_commands(str(project))
    # Both names land in the walker, but the realpath dedup keeps the
    # first-wins entry only. Total count of unique skills must be 1.
    seen_files = {
        os.path.realpath(str(Path(s.base_dir) / "SKILL.md"))
        for s in skills
        if s.base_dir
    }
    assert len(seen_files) == 1, (
        f"realpath dedup should collapse symlinked skills, got: "
        f"{[s.name for s in skills]}"
    )


def test_disable_policy_skills_excludes_managed_dir(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Plant a "policy" skill in the managed dir we set via the fixture.
    managed_root = Path(os.environ["CLAUDE_MANAGED_CONFIG_DIR"])
    _write_skill(
        managed_root / ".claude" / "skills" / "policyskill" / "SKILL.md",
        "---\ndescription: policy\n---\nbody",
    )

    project = tmp_path / "proj"
    project.mkdir()

    # Without the disable, the policy skill should appear.
    skills = get_skill_dir_commands(str(project))
    assert any(s.name == "policyskill" for s in skills), (
        f"managed/policy skill should load by default; got: "
        f"{[s.name for s in skills]}"
    )

    # With CLAUDE_CODE_DISABLE_POLICY_SKILLS=1, it must NOT.
    clear_skill_caches()
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_POLICY_SKILLS", "1")
    skills2 = get_skill_dir_commands(str(project))
    assert all(s.name != "policyskill" for s in skills2), (
        f"CLAUDE_CODE_DISABLE_POLICY_SKILLS=1 must skip the managed "
        f"dir; got: {[s.name for s in skills2]}"
    )


def test_bare_mode_skips_autodiscovery(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "projskill" / "SKILL.md",
        "---\ndescription: proj\n---\nbody",
    )

    # Sanity: without bare mode the project skill loads.
    assert any(s.name == "projskill" for s in get_skill_dir_commands(str(project)))

    # With bare mode and no --add-dir paths, discovery returns empty.
    clear_skill_caches()
    monkeypatch.setenv("CLAUDE_CODE_BARE_MODE", "1")
    assert get_skill_dir_commands(str(project)) == []


def test_bare_mode_with_add_dir_only_loads_those(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    # Plant a project skill that should be IGNORED in bare mode.
    _write_skill(
        project / ".claude" / "skills" / "projskill" / "SKILL.md",
        "---\ndescription: proj\n---\nbody",
    )

    # Plant an additional-dir skill that SHOULD load.
    extra_dir = tmp_path / "extra"
    _write_skill(
        extra_dir / ".claude" / "skills" / "extraskill" / "SKILL.md",
        "---\ndescription: extra\n---\nbody",
    )

    monkeypatch.setenv("CLAUDE_CODE_BARE_MODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ADDITIONAL_DIRECTORIES", str(extra_dir))

    skills = get_skill_dir_commands(str(project))
    names = {s.name for s in skills}
    assert "extraskill" in names, (
        f"bare mode should still load --add-dir skills; got: {names}"
    )
    assert "projskill" not in names, (
        f"bare mode must skip auto-discovery; got: {names}"
    )


def test_managed_user_project_precedence(
    tmp_path: Path, isolated_home: Path
) -> None:
    """When the same skill name appears in managed + user + project
    dirs, the unified ``get_all_skills`` merge keeps the highest-
    priority occurrence (TS order: managed → user → project → bundled).
    """
    managed_root = Path(os.environ["CLAUDE_MANAGED_CONFIG_DIR"])
    user_root = isolated_home

    _write_skill(
        managed_root / ".claude" / "skills" / "shared" / "SKILL.md",
        "---\ndescription: from-managed\n---\nM",
    )
    _write_skill(
        user_root / ".claude" / "skills" / "shared" / "SKILL.md",
        "---\ndescription: from-user\n---\nU",
    )
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "shared" / "SKILL.md",
        "---\ndescription: from-project\n---\nP",
    )

    skills = get_all_skills(project_root=project)
    by_name = {s.name: s for s in skills}
    # Managed wins per `get_skill_dir_commands` ordering (managed loads
    # first, dedup is first-wins).
    assert by_name["shared"].description == "from-managed", (
        f"precedence regression — expected managed to win, got: "
        f"{by_name['shared'].description!r}"
    )


# ======================================================================
# E — Conditional paths + dynamic discovery
# ======================================================================


def test_conditional_skill_held_until_path_matches(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "lintpy" / "SKILL.md",
        "---\n"
        "description: lint python\n"
        "paths:\n"
        '  - "src/**/*.py"\n'
        "---\n"
        "Run ruff",
    )

    # Initial load: the conditional skill is held back.
    skills = get_skill_dir_commands(str(project))
    assert all(s.name != "lintpy" for s in skills), (
        "conditional skill must NOT appear in unconditional results"
    )
    assert get_conditional_skill_count() >= 1

    # A non-matching activation must not flip it on.
    activated = activate_conditional_skills_for_paths(
        [str(project / "docs" / "x.md")], str(project)
    )
    assert activated == []
    assert "lintpy" not in {s.name for s in get_dynamic_skills()}

    # A matching activation flips it on.
    activated = activate_conditional_skills_for_paths(
        [str(project / "src" / "foo" / "bar.py")], str(project)
    )
    assert activated == ["lintpy"]
    assert "lintpy" in {s.name for s in get_dynamic_skills()}


def test_paths_double_glob_treated_as_unconditional(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "always" / "SKILL.md",
        "---\ndescription: always\npaths:\n  - \"**\"\n---\nbody",
    )
    skills = get_skill_dir_commands(str(project))
    names = {s.name for s in skills}
    assert "always" in names, (
        "`paths: ['**']` cleans to no-filter (None) and must appear "
        "in the unconditional list, not the conditional bucket"
    )


def test_path_validity_guards_reject_dotdot_and_absolute(
    tmp_path: Path, isolated_home: Path
) -> None:
    project = tmp_path / "proj"
    _write_skill(
        project / ".claude" / "skills" / "guarded" / "SKILL.md",
        "---\n"
        "description: guarded\n"
        "paths:\n"
        '  - "src/**/*.py"\n'
        "---\n"
        "body",
    )

    # Prime the conditional bucket.
    get_skill_dir_commands(str(project))
    assert get_conditional_skill_count() >= 1

    # `..`-escaping path: should be filtered out by the validity guard.
    above = tmp_path.parent / "outside.py"
    activated = activate_conditional_skills_for_paths(
        [str(above)], str(project)
    )
    assert activated == [], (
        "files outside the cwd (relpath starts with '..') must be "
        "ignored by the activation guard"
    )

    # Absolute path that ISN'T under cwd similarly drops.
    activated2 = activate_conditional_skills_for_paths(
        ["/etc/passwd"], str(project)
    )
    assert activated2 == []

    # The skill stays in the conditional bucket.
    assert "guarded" not in {s.name for s in get_dynamic_skills()}


def test_dynamic_discovery_skips_gitignored_dirs(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`discover_skill_dirs_for_paths` consults `git check-ignore` to
    avoid loading skills out of gitignored trees (e.g. node_modules).
    """
    project = tmp_path / "proj"
    project.mkdir()

    # Plant a `.claude/skills` dir under a gitignored path.
    ignored_skills_dir = project / "node_modules" / "pkg" / ".claude" / "skills"
    ignored_skills_dir.mkdir(parents=True)
    (ignored_skills_dir / "noisy" / "SKILL.md").parent.mkdir(parents=True)
    (ignored_skills_dir / "noisy" / "SKILL.md").write_text(
        "---\ndescription: noisy\n---\nbody"
    )

    # Mock `git check-ignore` to say "yes, this is ignored". We mock
    # at the `subprocess.run` boundary used by `_is_path_gitignored`.
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        # cmd looks like ["git", "check-ignore", "<path>"]
        # Anything under node_modules/ is ignored.
        path = cmd[-1] if cmd else ""
        rc = 0 if "node_modules" in path else 1
        return subprocess.CompletedProcess(cmd, rc, "", "")

    file_under_ignored = ignored_skills_dir.parent / "lib.js"
    file_under_ignored.write_text("//")

    # Walk from a touched file inside the ignored dir.
    with mock.patch("subprocess.run", side_effect=fake_run):
        new_dirs = discover_skill_dirs_for_paths(
            [str(file_under_ignored)], str(project)
        )

    # The gitignored skills dir must NOT be returned.
    assert all(
        "node_modules" not in d for d in new_dirs
    ), f"gitignored skills dir leaked into discovery: {new_dirs}"


def test_dynamic_discovery_includes_non_ignored_dirs(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Sanity counterpart: a non-ignored `.claude/skills` dir is
    returned by the discovery walk."""
    project = tmp_path / "proj"
    project.mkdir()
    nested_skills_dir = project / "pkg" / ".claude" / "skills"
    nested_skills_dir.mkdir(parents=True)
    file_path = project / "pkg" / "lib.js"
    file_path.write_text("//")

    # `git check-ignore` outside a git repo returns 128 — treated as
    # not-ignored by `_is_path_gitignored`'s fail-open branch.
    new_dirs = discover_skill_dirs_for_paths([str(file_path)], str(project))
    # The nested skills dir is included.
    assert any(str(nested_skills_dir) == d for d in new_dirs), (
        f"non-ignored nested .claude/skills dir should appear in "
        f"discovery; got: {new_dirs}"
    )

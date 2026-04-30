"""Tests for DEV-4: realpath dedup, bare/policy gates, conditional paths.

Acceptance criteria:
1. Two same-file skills accessed via different sources collapse to one.
2. Symlink → SKILL.md does not produce a duplicate (same realpath).
3. ``CLAUDE_CODE_DISABLE_POLICY_SKILLS`` skips the managed dir.
4. ``CLAUDE_CODE_BARE_MODE`` skips autodiscovery; only ``--add-dir``.
5. Conditional ``paths: ["src/**/*.py"]`` activates for `src/foo/bar.py`.
6. ``discover_skill_dirs_for_paths`` skips a `.claude/skills` dir whose
   parent is gitignored.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.skills.bundled_skills import clear_bundled_skills
from src.skills.create import create_skill
from src.skills.loader import (
    _compile_path_spec,
    _dedup_by_realpath,
    _get_additional_skill_dirs,
    _get_file_identity,
    _is_bare_mode,
    _is_path_gitignored,
    _is_restricted_to_plugin_only,
    _is_skills_policy_disabled,
    _path_matches_pattern,
    activate_conditional_skills_for_paths,
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    discover_skill_dirs_for_paths,
    get_skill_dir_commands,
    load_skills_from_skills_dir,
)
from src.skills.model import Skill


class _IsolatedHomeMixin:
    """Provide an isolated $HOME / managed-dir per test so user/managed
    skill discovery doesn't leak in real disk content."""

    def _isolate_env(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {
                "HOME": str(self._home),
                "CLAUDE_CONFIG_DIR": str(self._home / ".claude"),
                "CLAUDE_MANAGED_CONFIG_DIR": str(self._managed),
            },
            clear=False,
        )
        self._env_patch.start()
        # Drop any contaminating envs:
        for k in (
            "CLAWCODEX_SKILLS_DIR",
            "CLAUDE_SKILLS_DIR",
            "CLAWCODEX_MANAGED_SKILLS_DIR",
            "CLAUDE_CODE_BARE_MODE",
            "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
            "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        ):
            os.environ.pop(k, None)


class TestFileIdentity(unittest.TestCase):
    def test_returns_realpath(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "f.txt"
            p.write_text("x")
            link = Path(t) / "link.txt"
            link.symlink_to(p)
            self.assertEqual(_get_file_identity(str(link)), str(p.resolve()))

    def test_broken_symlink_returns_path_or_none(self) -> None:
        # `os.path.realpath` on a broken symlink returns the target path
        # without resolving it; either result is acceptable as long as
        # we never raise.
        with tempfile.TemporaryDirectory() as t:
            broken = Path(t) / "broken"
            broken.symlink_to(Path(t) / "missing")
            out = _get_file_identity(str(broken))
            # Should not raise; may return the unresolved path string.
            self.assertIsInstance(out, str)


class TestDedupByRealpath(unittest.TestCase):
    def test_same_realpath_drops_later_entries(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sk = Path(t) / "s"
            sk.mkdir()
            (sk / "SKILL.md").write_text("---\ndescription: x\n---\nbody")
            link = Path(t) / "link"
            link.symlink_to(sk)

            a = Skill(
                name="x", description="x", source="userSettings",
                base_dir=str(sk),
            )
            b = Skill(
                name="x", description="x", source="projectSettings",
                base_dir=str(link),
            )
            out = _dedup_by_realpath([a, b])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0].source, "userSettings")  # first wins

    def test_different_files_both_kept(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d1, d2 = Path(t) / "a", Path(t) / "b"
            d1.mkdir(); d2.mkdir()
            (d1 / "SKILL.md").write_text("body1")
            (d2 / "SKILL.md").write_text("body2")
            a = Skill(name="x", description="x", base_dir=str(d1))
            b = Skill(name="x", description="x", base_dir=str(d2))
            out = _dedup_by_realpath([a, b])
            self.assertEqual(len(out), 2)

    def test_no_base_dir_fails_open(self) -> None:
        a = Skill(name="x", description="x", base_dir=None)
        b = Skill(name="y", description="y", base_dir=None)
        out = _dedup_by_realpath([a, b])
        self.assertEqual(len(out), 2)


class TestPolicyAndBareGates(unittest.TestCase):
    def test_bare_mode_env_signal(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_BARE_MODE": "1"}):
            self.assertTrue(_is_bare_mode())
        with patch.dict(os.environ, {"CLAUDE_CODE_BARE_MODE": "0"}):
            self.assertFalse(_is_bare_mode())
        os.environ.pop("CLAUDE_CODE_BARE_MODE", None)
        self.assertFalse(_is_bare_mode())

    def test_policy_disabled_env_signal(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_POLICY_SKILLS": "true"}):
            self.assertTrue(_is_skills_policy_disabled())
        os.environ.pop("CLAUDE_CODE_DISABLE_POLICY_SKILLS", None)
        self.assertFalse(_is_skills_policy_disabled())

    def test_plugin_only_stub_returns_false(self) -> None:
        self.assertFalse(_is_restricted_to_plugin_only("skills"))

    def test_additional_dirs_pathsep_split(self) -> None:
        with patch.dict(
            os.environ,
            {"CLAUDE_CODE_ADDITIONAL_DIRECTORIES": f"/a{os.pathsep}/b{os.pathsep}"},
        ):
            self.assertEqual(_get_additional_skill_dirs(), ["/a", "/b"])
        os.environ.pop("CLAUDE_CODE_ADDITIONAL_DIRECTORIES", None)
        self.assertEqual(_get_additional_skill_dirs(), [])


class TestGetSkillDirCommandsBareAndPolicy(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name).resolve()
        self._home = self._root / "_home"; self._home.mkdir()
        self._managed = self._root / "_etc"; self._managed.mkdir()
        self._project = self._root / "proj"; self._project.mkdir()
        self._isolate_env()
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()

    def _make_skill(self, parent: Path, name: str, body: str = "body") -> Path:
        skill_dir = parent / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"---\ndescription: {name}\n---\n{body}")
        return skill_dir

    def test_policy_disabled_skips_managed_dir(self) -> None:
        # AC#3
        self._make_skill(self._managed / ".claude", "managed-only")
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1"}):
            clear_skill_caches()
            skills = get_skill_dir_commands(str(self._project))
        names = {s.name for s in skills}
        self.assertNotIn("managed-only", names)

    def test_managed_loaded_when_policy_enabled(self) -> None:
        self._make_skill(self._managed / ".claude", "managed-only")
        clear_skill_caches()
        skills = get_skill_dir_commands(str(self._project))
        names = {s.name for s in skills}
        self.assertIn("managed-only", names)

    def test_bare_mode_skips_managed_user_project(self) -> None:
        # AC#4
        self._make_skill(self._managed / ".claude", "m1")
        self._make_skill(self._home / ".claude", "u1")
        self._make_skill(self._project / ".claude", "p1")
        with patch.dict(os.environ, {"CLAUDE_CODE_BARE_MODE": "1"}):
            clear_skill_caches()
            skills = get_skill_dir_commands(str(self._project))
        # No additional dirs → bare mode returns empty.
        self.assertEqual(skills, [])

    def test_bare_mode_with_add_dir_loads_additional_only(self) -> None:
        # AC#4 — bare + --add-dir loads only the explicit dir.
        self._make_skill(self._managed / ".claude", "m1")
        self._make_skill(self._home / ".claude", "u1")
        self._make_skill(self._project / ".claude", "p1")
        extra = self._root / "extra"
        self._make_skill(extra / ".claude", "e1")
        with patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_BARE_MODE": "1",
                "CLAUDE_CODE_ADDITIONAL_DIRECTORIES": str(extra),
            },
        ):
            clear_skill_caches()
            skills = get_skill_dir_commands(str(self._project))
        names = {s.name for s in skills}
        self.assertEqual(names, {"e1"})


class TestSymlinkDedup(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name).resolve()
        self._home = self._root / "_home"; self._home.mkdir()
        self._managed = self._root / "_etc"; self._managed.mkdir()
        self._project = self._root / "proj"; self._project.mkdir()
        self._isolate_env()
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()

    def test_symlink_to_project_skill_collapses_to_one(self) -> None:
        # AC#2: ~/.claude/skills/foo → <proj>/.claude/skills/foo
        proj_skill = self._project / ".claude" / "skills" / "foo"
        proj_skill.mkdir(parents=True)
        (proj_skill / "SKILL.md").write_text("---\ndescription: foo\n---\nbody")

        user_skills_dir = self._home / ".claude" / "skills"
        user_skills_dir.mkdir(parents=True)
        (user_skills_dir / "foo").symlink_to(proj_skill)

        skills = get_skill_dir_commands(str(self._project))
        names = [s.name for s in skills]
        self.assertEqual(names.count("foo"), 1, f"got {names}")


class TestConditionalPathsGitignoreSemantics(unittest.TestCase):
    def setUp(self) -> None:
        clear_skill_caches(); clear_dynamic_skills()

    def tearDown(self) -> None:
        clear_skill_caches(); clear_dynamic_skills()

    def test_compile_pathspec_handles_doublestar(self) -> None:
        spec = _compile_path_spec(["src/**/*.py"])
        self.assertIsNotNone(spec)
        self.assertTrue(spec.match_file("src/foo/bar.py"))
        self.assertTrue(spec.match_file("src/x.py"))
        self.assertFalse(spec.match_file("docs/x.md"))

    def test_path_matches_pattern_recursive(self) -> None:
        # Regression for the prior fnmatch-based matcher that missed nested files.
        self.assertTrue(_path_matches_pattern("src/foo/bar.py", "src/**/*.py"))
        self.assertFalse(_path_matches_pattern("docs/x.md", "src/**/*.py"))

    def test_activate_conditional_via_pathspec(self) -> None:
        # AC#5: paths: ["src/**/*.py"] activates for src/foo/bar.py
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            sk = base / "skills" / "py-helper"
            sk.mkdir(parents=True)
            (sk / "SKILL.md").write_text(
                "---\n"
                "description: py helper\n"
                "paths:\n"
                "  - src/**/*.py\n"
                "---\n"
                "body\n"
            )
            # Loading puts the skill into _conditional_skills (because
            # paths is set), not into the returned list — that's the
            # contract callers rely on.
            loaded = load_skills_from_skills_dir(
                str(sk.parent), "projectSettings"
            )
            from src.skills.loader import _conditional_skills, _dynamic_skills
            # Manually move into conditional pool (simulates the
            # get_skill_dir_commands path):
            for s in loaded:
                _conditional_skills[s.name] = s

            cwd = str(base)
            (base / "src" / "foo").mkdir(parents=True)
            (base / "src" / "foo" / "bar.py").write_text("x = 1")

            activated = activate_conditional_skills_for_paths(
                [str(base / "src" / "foo" / "bar.py")], cwd
            )
            self.assertIn("py-helper", activated)
            self.assertIn("py-helper", _dynamic_skills)

            # Same skill should NOT activate for unrelated docs/x.md:
            _dynamic_skills.clear()
            _conditional_skills["py-helper"] = loaded[0]
            (base / "docs").mkdir()
            (base / "docs" / "x.md").write_text("doc")
            activated = activate_conditional_skills_for_paths(
                [str(base / "docs" / "x.md")], cwd
            )
            self.assertNotIn("py-helper", activated)


class TestActivatedConditionalIsInvokableViaSkillTool(unittest.TestCase):
    """Regression for QA bug #14.

    Before the fix, ``get_all_skills`` did not merge ``_dynamic_skills``
    into ``_skill_registry``, so a conditional skill that
    ``activate_conditional_skills_for_paths`` had just promoted was
    invisible to ``SkillTool`` (`Unknown skill: ...`, error_code=2).
    """

    def setUp(self) -> None:
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()
        self._tmp = tempfile.TemporaryDirectory()
        self._project = Path(self._tmp.name).resolve()
        # Isolate $HOME and managed-dir so user/managed discovery doesn't
        # contaminate the project under test.
        self._home = self._project / "_home"; self._home.mkdir()
        self._managed = self._project / "_etc"; self._managed.mkdir()
        self._env_patch = patch.dict(
            os.environ,
            {
                "HOME": str(self._home),
                "CLAUDE_CONFIG_DIR": str(self._home / ".claude"),
                "CLAUDE_MANAGED_CONFIG_DIR": str(self._managed),
            },
            clear=False,
        )
        self._env_patch.start()
        for k in (
            "CLAWCODEX_SKILLS_DIR",
            "CLAUDE_SKILLS_DIR",
            "CLAWCODEX_MANAGED_SKILLS_DIR",
            "CLAUDE_CODE_BARE_MODE",
            "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
            "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        ):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()
        clear_skill_caches(); clear_dynamic_skills(); clear_skill_registry()

    def test_skilltool_can_invoke_activated_conditional(self) -> None:
        from src.skills.loader import get_all_skills, get_registered_skill
        from src.tool_system.context import ToolContext
        from src.tool_system.tools import SkillTool

        # Place a paths-gated SKILL.md under .claude/skills/lint-py/
        skill_dir = self._project / ".claude" / "skills" / "lint-py"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "description: Lint Python files\n"
            "paths:\n"
            '  - "**/*.py"\n'
            "---\n"
            "Lint that file.\n"
        )

        # Pre-activation: the skill is held back (conditional).
        names = {s.name for s in get_all_skills(project_root=self._project)}
        self.assertNotIn("lint-py", names)
        self.assertIsNone(get_registered_skill("lint-py"))

        # Touching a .py file activates it.
        (self._project / "src").mkdir()
        target = self._project / "src" / "foo.py"
        target.write_text("x = 1")
        activated = activate_conditional_skills_for_paths(
            [str(target)], str(self._project)
        )
        self.assertIn("lint-py", activated)

        # The SkillTool path must now succeed for /lint-py — that is,
        # `get_all_skills` must splice `_dynamic_skills` into the
        # registry so the lookup hits.
        ctx = ToolContext(workspace_root=self._project)
        result = SkillTool.call({"skill": "lint-py"}, ctx)
        self.assertFalse(
            result.is_error,
            f"SkillTool returned error after activation: {result.output}",
        )
        self.assertTrue(result.output["success"])
        self.assertIn("Lint that file.", result.output["prompt"])


class TestDiscoverSkillDirsGitignore(unittest.TestCase):
    def setUp(self) -> None:
        clear_skill_caches(); clear_dynamic_skills()
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = Path(self._tmp.name).resolve()
        # Initialize a real git repo so `git check-ignore` works.
        try:
            subprocess.run(
                ["git", "init", "-q"], cwd=self._cwd, check=True, timeout=10,
            )
            subprocess.run(
                ["git", "config", "user.email", "t@t.test"],
                cwd=self._cwd, check=True, timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=self._cwd, check=True, timeout=5,
            )
            self._git_ok = True
        except (FileNotFoundError, subprocess.SubprocessError):
            self._git_ok = False

    def tearDown(self) -> None:
        self._tmp.cleanup()
        clear_skill_caches(); clear_dynamic_skills()

    def test_skips_gitignored_skills_dir(self) -> None:
        if not self._git_ok:
            self.skipTest("git unavailable")
        # AC#6: node_modules-style gitignored dir's .claude/skills isn't loaded.
        (self._cwd / ".gitignore").write_text("node_modules/\n")
        ignored_pkg = self._cwd / "node_modules" / "pkg"
        ignored_pkg.mkdir(parents=True)
        skills_dir = ignored_pkg / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "evil").mkdir()
        (skills_dir / "evil" / "SKILL.md").write_text(
            "---\ndescription: evil\n---\n"
        )
        (ignored_pkg / "x.js").write_text("nop")

        new_dirs = discover_skill_dirs_for_paths(
            [str(ignored_pkg / "x.js")], str(self._cwd)
        )
        self.assertNotIn(str(skills_dir), new_dirs)

    def test_loads_non_gitignored_skills_dir(self) -> None:
        if not self._git_ok:
            self.skipTest("git unavailable")
        # Sanity: a non-ignored .claude/skills dir IS picked up.
        nested = self._cwd / "feature"
        nested.mkdir()
        skills_dir = nested / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "good").mkdir()
        (skills_dir / "good" / "SKILL.md").write_text(
            "---\ndescription: good\n---\n"
        )
        (nested / "f.txt").write_text("x")
        new_dirs = discover_skill_dirs_for_paths(
            [str(nested / "f.txt")], str(self._cwd)
        )
        self.assertIn(str(skills_dir), new_dirs)


class TestGitignoreCheckHelper(unittest.TestCase):
    def test_returns_false_outside_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "f.txt"
            p.write_text("x")
            # No git init → fail open.
            self.assertFalse(_is_path_gitignored(str(p), t))

    def test_returns_true_for_ignored_path(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as t:
            try:
                subprocess.run(
                    ["git", "init", "-q"], cwd=t, check=True, timeout=10,
                )
            except subprocess.SubprocessError:
                self.skipTest("git init failed")
            (Path(t) / ".gitignore").write_text("ignored_file\n")
            (Path(t) / "ignored_file").write_text("x")
            self.assertTrue(_is_path_gitignored("ignored_file", t))
            (Path(t) / "kept").write_text("x")
            self.assertFalse(_is_path_gitignored("kept", t))


if __name__ == "__main__":
    unittest.main()

"""Tests for src/memdir/team_mem_paths.py — defense-in-depth path validation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.memdir.team_mem_paths import (
    PathTraversalError,
    _realpath_deepest_existing,
    _sanitize_path_key,
    get_team_mem_entrypoint,
    get_team_mem_path,
    is_team_mem_file,
    is_team_mem_path,
    is_team_memory_enabled,
    validate_team_mem_key,
    validate_team_mem_write_path,
)

_TRACKED_ENV = (
    "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE",
    "CLAUDE_CODE_TEAM_MEMORY",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_REMOTE",
    "CLAUDE_CODE_REMOTE_MEMORY_DIR",
)


class _EnvFixture(unittest.TestCase):
    """Mixin that snapshots/restores the tracked memory env vars."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _TRACKED_ENV}
        for k in _TRACKED_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class SanitizePathKeyTest(unittest.TestCase):
    def test_plain_key_accepted(self):
        self.assertEqual(
            _sanitize_path_key("feedback_testing.md"), "feedback_testing.md"
        )

    def test_nested_subdir_accepted(self):
        self.assertEqual(_sanitize_path_key("subdir/file.md"), "subdir/file.md")

    def test_null_byte_rejected(self):
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("foo\0bar")

    def test_url_encoded_traversal_rejected(self):
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("%2e%2e%2fetc")

    def test_url_encoded_slash_rejected(self):
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("foo%2fbar")

    def test_malformed_percent_does_not_raise(self):
        # %ZZ is not valid URL-encoding, so it cannot encode a traversal.
        self.assertEqual(_sanitize_path_key("foo%ZZbar"), "foo%ZZbar")

    def test_nfkc_fullwidth_dotdot_rejected(self):
        # Fullwidth ．．／ (U+FF0E U+FF0F) → ASCII ../ under NFKC.
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("．．／etc")

    def test_backslash_rejected(self):
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("foo\\bar")

    def test_absolute_path_rejected(self):
        with self.assertRaises(PathTraversalError):
            _sanitize_path_key("/etc/passwd")


class IsTeamMemoryEnabledTest(_EnvFixture):
    def test_default_off(self):
        self.assertFalse(is_team_memory_enabled())

    def test_flag_on_auto_on(self):
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "1"
        self.assertTrue(is_team_memory_enabled())

    def test_flag_on_auto_off(self):
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "1"
        # Disabling auto-memory transitively disables team memory.
        os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        self.assertFalse(is_team_memory_enabled())

    def test_flag_falsy_off(self):
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "0"
        self.assertFalse(is_team_memory_enabled())


class GetTeamMemPathTest(_EnvFixture):
    def test_trailing_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = tmp
            self.assertTrue(get_team_mem_path().endswith(os.sep))

    def test_under_auto_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = tmp
            from src.memdir.paths import get_auto_mem_path

            self.assertTrue(get_team_mem_path().startswith(get_auto_mem_path()))

    def test_entrypoint_is_memory_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = tmp
            self.assertTrue(get_team_mem_entrypoint().endswith("MEMORY.md"))


class IsTeamMemPathTest(_EnvFixture):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_path_inside_accepted(self):
        path = os.path.join(get_team_mem_path(), "feedback_testing.md")
        self.assertTrue(is_team_mem_path(path))

    def test_prefix_attack_rejected(self):
        # team-evil/ must not match team/ — trailing sep prevents this.
        sibling = get_team_mem_path().rstrip(os.sep) + "-evil/foo.md"
        self.assertFalse(is_team_mem_path(sibling))

    def test_traversal_rejected(self):
        # The .. should be normalized away, escaping the team dir.
        escaping = os.path.join(
            get_team_mem_path(), "..", "..", "etc", "passwd"
        )
        self.assertFalse(is_team_mem_path(escaping))

    def test_empty_returns_false(self):
        self.assertFalse(is_team_mem_path(""))

    def test_is_team_mem_file_requires_enabled(self):
        path = os.path.join(get_team_mem_path(), "feedback_testing.md")
        # Flag off by default — even an inside path is not a "team file".
        self.assertFalse(is_team_mem_file(path))
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "1"
        self.assertTrue(is_team_mem_file(path))


class ValidateTeamMemWritePathTest(_EnvFixture):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name
        Path(get_team_mem_path()).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_happy_path(self):
        path = os.path.join(get_team_mem_path(), "feedback_testing.md")
        resolved = validate_team_mem_write_path(path)
        self.assertTrue(resolved.startswith(get_team_mem_path()))

    def test_null_byte_rejected(self):
        path = os.path.join(get_team_mem_path(), "bad\0file.md")
        with self.assertRaises(PathTraversalError):
            validate_team_mem_write_path(path)

    def test_string_level_escape_rejected(self):
        path = os.path.join(
            get_team_mem_path(), "..", "..", "etc", "passwd"
        )
        with self.assertRaises(PathTraversalError):
            validate_team_mem_write_path(path)

    def test_symlink_escape_rejected(self):
        # Create a symlink inside teamDir pointing to a sibling outside.
        with tempfile.TemporaryDirectory() as outside:
            link = os.path.join(get_team_mem_path(), "evil")
            os.symlink(outside, link)
            target = os.path.join(link, "stolen.md")
            with self.assertRaises(PathTraversalError):
                validate_team_mem_write_path(target)

    def test_existing_inside_file_passes(self):
        inside = os.path.join(get_team_mem_path(), "existing.md")
        Path(inside).write_text("content", encoding="utf-8")
        resolved = validate_team_mem_write_path(inside)
        # On macOS /var resolves to /private/var. Compare realpaths.
        self.assertEqual(
            os.path.realpath(resolved), os.path.realpath(inside)
        )


class ValidateTeamMemKeyTest(_EnvFixture):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name
        Path(get_team_mem_path()).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_happy_path(self):
        resolved = validate_team_mem_key("project_freeze.md")
        self.assertTrue(resolved.startswith(get_team_mem_path()))

    def test_url_encoded_rejected(self):
        with self.assertRaises(PathTraversalError):
            validate_team_mem_key("%2e%2e%2fetc%2fpasswd")

    def test_absolute_key_rejected(self):
        with self.assertRaises(PathTraversalError):
            validate_team_mem_key("/etc/passwd")

    def test_traversal_key_rejected(self):
        # _sanitize_path_key allows literal "..", but the second-pass
        # containment check catches the join-and-resolve escape.
        with self.assertRaises(PathTraversalError):
            validate_team_mem_key("../../etc/passwd")

    def test_backslash_key_rejected(self):
        with self.assertRaises(PathTraversalError):
            validate_team_mem_key("foo\\bar")

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            link = os.path.join(get_team_mem_path(), "evil-link")
            os.symlink(outside, link)
            with self.assertRaises(PathTraversalError):
                validate_team_mem_key("evil-link/stolen.md")


class RealpathDeepestExistingTest(_EnvFixture):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name
        Path(get_team_mem_path()).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_tail_rejoin_for_nonexistent_target(self):
        tail_path = os.path.join(
            get_team_mem_path(), "new_subdir", "file.md"
        )
        result = _realpath_deepest_existing(tail_path)
        team_real = os.path.realpath(get_team_mem_path().rstrip(os.sep))
        self.assertTrue(result.startswith(team_real))
        self.assertTrue(
            result.endswith(os.path.join("new_subdir", "file.md"))
        )

    def test_existing_path_resolves_to_realpath(self):
        f = Path(get_team_mem_path()) / "real.md"
        f.write_text("hi", encoding="utf-8")
        result = _realpath_deepest_existing(str(f))
        self.assertEqual(result, os.path.realpath(str(f)))

    def test_dangling_symlink_detected(self):
        # Symlink pointing to a non-existent target — writing through
        # this would still follow the link and create the target outside
        # teamDir.
        link = os.path.join(get_team_mem_path(), "dangling")
        os.symlink(os.path.join(self._tmp.name, "nowhere"), link)
        with self.assertRaises(PathTraversalError):
            _realpath_deepest_existing(link)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from src.permissions.bash_security import (
    BashAnalysisResult,
    analyze_bash_command,
    check_bash_command_safety,
    classify_sed_pattern,
    get_bash_command_description,
    is_sed_in_place,
    should_sandbox_command,
)
from src.permissions.bash_parser.commands import CommandSafety


class TestAnalyzeBashCommand(unittest.TestCase):
    def test_empty_command(self) -> None:
        result = analyze_bash_command("")
        self.assertEqual(result.safety, "safe")
        self.assertEqual(len(result.commands), 0)

    def test_safe_command(self) -> None:
        result = analyze_bash_command("echo hello")
        self.assertEqual(result.safety, "safe")

    def test_read_only_command(self) -> None:
        result = analyze_bash_command("ls -la")
        self.assertEqual(result.safety, "read_only")

    def test_write_command(self) -> None:
        result = analyze_bash_command("cp a b")
        self.assertEqual(result.safety, "write")

    def test_destructive_command(self) -> None:
        result = analyze_bash_command("rm -rf /tmp/foo")
        self.assertEqual(result.safety, "destructive")

    def test_dangerous_command(self) -> None:
        result = analyze_bash_command("curl http://example.com")
        self.assertEqual(result.safety, "dangerous")

    def test_pipeline_takes_max_safety(self) -> None:
        result = analyze_bash_command("echo hello | rm file")
        self.assertEqual(result.safety, "destructive")

    def test_complex_command_is_unknown(self) -> None:
        result = analyze_bash_command("echo 'unterminated")
        self.assertTrue(result.is_complex)
        self.assertEqual(result.safety, "unknown")

    def test_extracts_file_paths(self) -> None:
        result = analyze_bash_command("cat /etc/hosts ./local.txt")
        self.assertIn("/etc/hosts", result.paths)
        self.assertIn("./local.txt", result.paths)

    def test_redirect_path_extracted(self) -> None:
        result = analyze_bash_command("echo hello > output.txt")
        self.assertIn("output.txt", result.paths)


class TestCheckBashCommandSafety(unittest.TestCase):
    def test_safe_command_returns_none(self) -> None:
        self.assertIsNone(check_bash_command_safety("echo hello"))

    def test_read_only_returns_none(self) -> None:
        self.assertIsNone(check_bash_command_safety("ls -la"))

    def test_write_returns_none(self) -> None:
        self.assertIsNone(check_bash_command_safety("cp a b"))

    def test_dangerous_returns_ask(self) -> None:
        result = check_bash_command_safety("curl http://example.com")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_destructive_returns_ask(self) -> None:
        result = check_bash_command_safety("rm -rf /tmp/foo")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_unknown_command_returns_ask(self) -> None:
        result = check_bash_command_safety("frobnicator --do-stuff")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_complex_command_returns_ask(self) -> None:
        result = check_bash_command_safety("echo 'unclosed")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")


class TestIsSedInPlace(unittest.TestCase):
    def test_sed_i_flag(self) -> None:
        self.assertTrue(is_sed_in_place(["sed", "-i", "s/a/b/", "file"]))

    def test_sed_in_place_long(self) -> None:
        self.assertTrue(is_sed_in_place(["sed", "--in-place", "s/a/b/", "file"]))

    def test_sed_without_i(self) -> None:
        self.assertFalse(is_sed_in_place(["sed", "s/a/b/", "file"]))

    def test_not_sed(self) -> None:
        self.assertFalse(is_sed_in_place(["grep", "-i", "pattern"]))


class TestClassifySedPattern(unittest.TestCase):
    def test_sed_in_place_is_write(self) -> None:
        self.assertEqual(classify_sed_pattern(["sed", "-i", "s/a/b/"]), CommandSafety.WRITE)

    def test_sed_no_inplace_is_read_only(self) -> None:
        self.assertEqual(classify_sed_pattern(["sed", "s/a/b/"]), CommandSafety.READ_ONLY)


class TestShouldSandboxCommand(unittest.TestCase):
    def test_safe_not_sandboxed(self) -> None:
        self.assertFalse(should_sandbox_command("echo hello"))

    def test_read_only_not_sandboxed(self) -> None:
        self.assertFalse(should_sandbox_command("ls -la"))

    def test_destructive_sandboxed(self) -> None:
        self.assertTrue(should_sandbox_command("rm -rf /tmp/foo"))

    def test_dangerous_sandboxed(self) -> None:
        self.assertTrue(should_sandbox_command("curl http://example.com"))

    def test_complex_sandboxed(self) -> None:
        self.assertTrue(should_sandbox_command("echo 'unclosed"))


class TestGetBashCommandDescription(unittest.TestCase):
    def test_simple_command(self) -> None:
        desc = get_bash_command_description("ls -la")
        self.assertIn("ls", desc)

    def test_pipeline(self) -> None:
        desc = get_bash_command_description("cat foo | grep bar")
        self.assertIn("cat", desc)
        self.assertIn("grep", desc)

    def test_empty(self) -> None:
        desc = get_bash_command_description("")
        self.assertEqual(desc, "empty command")


if __name__ == "__main__":
    unittest.main()

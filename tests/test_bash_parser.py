from __future__ import annotations

import unittest

from src.permissions.bash_parser.ast_nodes import SimpleCommand, Redirect
from src.permissions.bash_parser.commands import (
    CommandSafety,
    classify_command,
    get_command_safety,
    is_read_only_command,
    is_safe_command,
)
from src.permissions.bash_parser.parser import ParseResult, extract_all_commands, parse_command
from src.permissions.bash_parser.shell_quote import (
    expand_home,
    is_glob_pattern,
    quote,
    split_command,
)


class TestParseCommandSimple(unittest.TestCase):
    def test_empty_string(self) -> None:
        r = parse_command("")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 0)

    def test_single_command(self) -> None:
        r = parse_command("ls -la")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 1)
        self.assertEqual(r.commands[0].argv, ["ls", "-la"])

    def test_command_with_pipe(self) -> None:
        r = parse_command("cat file.txt | grep foo")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 2)
        self.assertEqual(r.commands[0].argv, ["cat", "file.txt"])
        self.assertEqual(r.commands[1].argv, ["grep", "foo"])

    def test_command_with_and(self) -> None:
        r = parse_command("mkdir foo && cd foo")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 2)
        self.assertEqual(r.commands[0].argv, ["mkdir", "foo"])
        self.assertEqual(r.commands[1].argv, ["cd", "foo"])

    def test_command_with_or(self) -> None:
        r = parse_command("test -f foo || echo missing")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 2)

    def test_command_with_semicolon(self) -> None:
        r = parse_command("echo a; echo b")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 2)

    def test_single_quoted_string(self) -> None:
        r = parse_command("echo 'hello world'")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(r.commands[0].argv, ["echo", "hello world"])

    def test_double_quoted_string(self) -> None:
        r = parse_command('echo "hello world"')
        self.assertEqual(r.kind, "simple")
        self.assertEqual(r.commands[0].argv, ["echo", "hello world"])

    def test_env_var_prefix(self) -> None:
        r = parse_command("FOO=bar echo test")
        self.assertEqual(r.kind, "simple")
        cmd = r.commands[0]
        self.assertEqual(cmd.env_vars, {"FOO": "bar"})
        self.assertEqual(cmd.argv, ["echo", "test"])

    def test_redirect_output(self) -> None:
        r = parse_command("echo hello > file.txt")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(len(r.commands), 1)
        self.assertEqual(len(r.commands[0].redirects), 1)
        self.assertEqual(r.commands[0].redirects[0].target, "file.txt")

    def test_redirect_append(self) -> None:
        r = parse_command("echo hello >> file.txt")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(r.commands[0].redirects[0].op, ">>")

    def test_comment_ignored(self) -> None:
        r = parse_command("echo hello # this is a comment")
        self.assertEqual(r.kind, "simple")
        self.assertEqual(r.commands[0].argv, ["echo", "hello"])

    def test_command_substitution_dollar(self) -> None:
        r = parse_command("echo $(whoami)")
        self.assertEqual(r.kind, "simple")
        self.assertIn("__CMDSUB__", r.commands[0].argv[1])

    def test_command_substitution_backtick(self) -> None:
        r = parse_command("echo `whoami`")
        self.assertEqual(r.kind, "simple")

    def test_continuation_line(self) -> None:
        r = parse_command("echo \\\nhello")
        self.assertEqual(r.kind, "simple")

    def test_unterminated_single_quote(self) -> None:
        r = parse_command("echo 'hello")
        self.assertEqual(r.kind, "too-complex")


class TestExtractAllCommands(unittest.TestCase):
    def test_simple_command(self) -> None:
        cmds = extract_all_commands("ls -la")
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0].argv, ["ls", "-la"])

    def test_pipeline(self) -> None:
        cmds = extract_all_commands("cat foo | grep bar | wc -l")
        self.assertEqual(len(cmds), 3)

    def test_complex_fallback(self) -> None:
        cmds = extract_all_commands("echo 'unclosed")
        self.assertEqual(len(cmds), 1)


class TestClassifyCommand(unittest.TestCase):
    def test_empty_is_safe(self) -> None:
        self.assertEqual(classify_command([]), CommandSafety.SAFE)

    def test_echo_is_safe(self) -> None:
        self.assertEqual(classify_command(["echo", "hello"]), CommandSafety.SAFE)

    def test_ls_is_read_only(self) -> None:
        self.assertEqual(classify_command(["ls", "-la"]), CommandSafety.READ_ONLY)

    def test_cat_is_read_only(self) -> None:
        self.assertEqual(classify_command(["cat", "file.txt"]), CommandSafety.READ_ONLY)

    def test_grep_is_read_only(self) -> None:
        self.assertEqual(classify_command(["grep", "foo", "bar.txt"]), CommandSafety.READ_ONLY)

    def test_cp_is_write(self) -> None:
        self.assertEqual(classify_command(["cp", "a", "b"]), CommandSafety.WRITE)

    def test_mv_is_write(self) -> None:
        self.assertEqual(classify_command(["mv", "a", "b"]), CommandSafety.WRITE)

    def test_rm_is_destructive(self) -> None:
        self.assertEqual(classify_command(["rm", "file"]), CommandSafety.DESTRUCTIVE)

    def test_sudo_is_dangerous(self) -> None:
        self.assertEqual(classify_command(["sudo", "ls"]), CommandSafety.DANGEROUS)

    def test_python_is_dangerous(self) -> None:
        self.assertEqual(classify_command(["python", "script.py"]), CommandSafety.DANGEROUS)

    def test_curl_is_dangerous(self) -> None:
        self.assertEqual(classify_command(["curl", "http://example.com"]), CommandSafety.DANGEROUS)

    def test_unknown_command(self) -> None:
        self.assertEqual(classify_command(["frobnicator"]), CommandSafety.UNKNOWN)

    def test_git_status_is_read_only(self) -> None:
        self.assertEqual(classify_command(["git", "status"]), CommandSafety.READ_ONLY)

    def test_git_log_is_read_only(self) -> None:
        self.assertEqual(classify_command(["git", "log"]), CommandSafety.READ_ONLY)

    def test_git_add_is_write(self) -> None:
        self.assertEqual(classify_command(["git", "add", "."]), CommandSafety.WRITE)

    def test_git_commit_is_write(self) -> None:
        self.assertEqual(classify_command(["git", "commit", "-m", "msg"]), CommandSafety.WRITE)

    def test_git_push_is_dangerous(self) -> None:
        self.assertEqual(classify_command(["git", "push"]), CommandSafety.DANGEROUS)

    def test_git_clean_is_destructive(self) -> None:
        self.assertEqual(classify_command(["git", "clean", "-fd"]), CommandSafety.DESTRUCTIVE)

    def test_npm_list_is_read_only(self) -> None:
        self.assertEqual(classify_command(["npm", "list"]), CommandSafety.READ_ONLY)

    def test_npm_install_is_write(self) -> None:
        self.assertEqual(classify_command(["npm", "install"]), CommandSafety.WRITE)

    def test_npm_run_is_dangerous(self) -> None:
        self.assertEqual(classify_command(["npm", "run", "build"]), CommandSafety.DANGEROUS)

    def test_sed_without_inplace_is_read_only(self) -> None:
        self.assertEqual(classify_command(["sed", "s/a/b/", "file"]), CommandSafety.READ_ONLY)

    def test_sed_with_inplace_is_write(self) -> None:
        self.assertEqual(classify_command(["sed", "-i", "s/a/b/", "file"]), CommandSafety.WRITE)

    def test_full_path_command(self) -> None:
        self.assertEqual(classify_command(["/usr/bin/ls"]), CommandSafety.READ_ONLY)

    def test_full_path_python(self) -> None:
        self.assertEqual(classify_command(["/usr/bin/python3", "script.py"]), CommandSafety.DANGEROUS)


class TestGetCommandSafety(unittest.TestCase):
    def test_ls(self) -> None:
        self.assertEqual(get_command_safety("ls"), CommandSafety.READ_ONLY)

    def test_echo(self) -> None:
        self.assertEqual(get_command_safety("echo"), CommandSafety.SAFE)


class TestIsReadOnlyCommand(unittest.TestCase):
    def test_ls_is_read_only(self) -> None:
        self.assertTrue(is_read_only_command(["ls"]))

    def test_echo_is_read_only(self) -> None:
        self.assertTrue(is_read_only_command(["echo"]))

    def test_rm_is_not_read_only(self) -> None:
        self.assertFalse(is_read_only_command(["rm", "file"]))


class TestIsSafeCommand(unittest.TestCase):
    def test_echo_is_safe(self) -> None:
        self.assertTrue(is_safe_command(["echo"]))

    def test_ls_is_not_safe(self) -> None:
        self.assertFalse(is_safe_command(["ls"]))


class TestShellQuote(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(quote(""), "''")

    def test_simple_word(self) -> None:
        self.assertEqual(quote("hello"), "hello")

    def test_word_with_spaces(self) -> None:
        result = quote("hello world")
        self.assertIn("hello world", result)

    def test_path_no_quoting(self) -> None:
        self.assertEqual(quote("/usr/bin/ls"), "/usr/bin/ls")


class TestSplitCommand(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(split_command("ls -la"), ["ls", "-la"])

    def test_quoted(self) -> None:
        self.assertEqual(split_command('echo "hello world"'), ["echo", "hello world"])

    def test_invalid_returns_whole(self) -> None:
        result = split_command("echo 'unclosed")
        self.assertEqual(len(result), 1)


class TestIsGlobPattern(unittest.TestCase):
    def test_star(self) -> None:
        self.assertTrue(is_glob_pattern("*.py"))

    def test_question_mark(self) -> None:
        self.assertTrue(is_glob_pattern("file?.txt"))

    def test_no_glob(self) -> None:
        self.assertFalse(is_glob_pattern("file.txt"))


class TestExpandHome(unittest.TestCase):
    def test_tilde_expanded(self) -> None:
        result = expand_home("~/Documents")
        self.assertNotIn("~", result)
        self.assertTrue(result.startswith("/"))

    def test_no_tilde(self) -> None:
        self.assertEqual(expand_home("/usr/bin"), "/usr/bin")


if __name__ == "__main__":
    unittest.main()

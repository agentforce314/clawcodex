import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.hooks import hol_guard_pretool as guard
from src.services.tool_execution.tool_hooks import _prepare_pre_tool_hook_input


@pytest.fixture(autouse=True)
def _trusted_guard_lookup_for_focused_tests(monkeypatch):
    # Production looks up the real Guard binary. Synthetic output tests use a
    # fixed executable path outside any project worktree.
    monkeypatch.setattr(guard.shutil, "which", lambda _: "/__hol_guard_test__/bin/hol-guard")


def _input(
    command: object = "git status",
    *,
    cwd: str | None = None,
) -> dict[str, object]:
    tool_input: dict[str, object] = {"command": command}
    if cwd is not None:
        tool_input["cwd"] = cwd
    return {
        "hook_event": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": tool_input,
    }


def _result(payload: object, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr="")


def test_explicit_benign_allow(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: _result(
            {"classification": {"explicitly_benign": True}, "minimum_action": "allow"}
        ),
    )
    assert guard.evaluate(_input()) == (True, "guard_allow")


def test_implicit_allow_blocks(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: _result(
            {"classification": {"explicitly_benign": False}, "minimum_action": "allow"}
        ),
    )
    assert guard.evaluate(_input()) == (False, "guard_block")


@pytest.mark.parametrize(
    "hook_output",
    [
        None,
        {"permissionDecision": "allow"},
        {"hookEventName": "PostToolUse", "permissionDecision": "allow"},
        {"hookEventName": "PreToolUse", "permissionDecision": "deny"},
    ],
)
def test_invalid_explicit_hook_envelopes_block(monkeypatch, hook_output):
    payload = {
        "classification": {"explicitly_benign": True},
        "minimum_action": "allow",
        "hookSpecificOutput": hook_output,
    }
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: _result(payload),
    )
    assert guard.evaluate(_input()) == (False, "guard_block")


@pytest.mark.parametrize("stop", [{"decision": None}, {"decision": []}, {"decision": "deny"}])
def test_explicit_invalid_decision_fails_closed(monkeypatch, stop):
    verdict = {
        "classification": {"explicitly_benign": True},
        "minimum_action": "allow",
        **stop,
    }
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: _result(verdict),
    )
    assert guard.evaluate(_input()) == (False, "guard_block")


def test_invalid_utf8_stdin_exits_with_blocking_code():
    """Even malformed hook input must not exit with the nonblocking error code 1."""
    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=b"\xff",
        capture_output=True,
        check=False,
    )
    assert result.returncode == guard.BLOCK_EXIT
    assert b"guard_invalid_input" in result.stderr


@pytest.mark.parametrize(
    "raw_input",
    [
        '{"hook_event":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git status","command":"rm -rf build"}}',
        '{"hook_event":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git status"},"tool_input":{"command":"rm -rf build"}}',
        '{"hook_event":"PostToolUse","hook_event":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git status"}}',
    ],
)
def test_duplicate_hook_input_fields_block_before_guard(monkeypatch, raw_input, capsys):
    """Ambiguous hook input must not be inspected as a different command."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw_input))
    monkeypatch.setattr(
        guard,
        "_guard_allows",
        lambda *_args, **_kwargs: pytest.fail("Guard must not inspect ambiguous input"),
    )

    assert guard.main() == guard.BLOCK_EXIT
    assert "guard_invalid_input" in capsys.readouterr().err


def test_review_blocks(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: _result(
            {"classification": {"explicitly_benign": False}, "minimum_action": "review"}
        ),
    )
    assert guard.evaluate(_input()) == (False, "guard_block")


def test_malformed_output_blocks(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="{", stderr=""),
    )
    assert guard.evaluate(_input()) == (False, "guard_invalid_output")


@pytest.mark.parametrize(
    "stdout",
    [
        '{"classification":{"explicitly_benign":true},"minimum_action":"deny","minimum_action":"allow"}',
        '{"classification":{"explicitly_benign":false,"explicitly_benign":true},"minimum_action":"allow"}',
        '{"classification":{"explicitly_benign":true},"continue":false,"continue":true,"minimum_action":"allow"}',
    ],
)
def test_duplicate_guard_verdict_fields_fail_closed(monkeypatch, stdout):
    """JSON duplicate keys must not override an explicit stop signal."""
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    assert guard.evaluate(_input()) == (False, "guard_invalid_output")


def test_overdeep_guard_json_blocks(monkeypatch):
    """RecursionError from JSON parsing must not turn into a nonblocking hook exit."""
    nested = "[" * 20000 + "]" * 20000
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=nested, stderr=""),
    )
    assert guard.evaluate(_input()) == (False, "guard_invalid_output")


def test_overdeep_hook_input_exits_with_blocking_code():
    nested = "[" * 20000 + "]" * 20000
    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=nested,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == guard.BLOCK_EXIT
    assert "guard_invalid_input" in result.stderr


def test_nonzero_guard_exit_blocks(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert guard.evaluate(_input()) == (False, "guard_error")


def test_timeout_blocks(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="hol-guard", timeout=guard.TIMEOUT_SECONDS)

    monkeypatch.setattr(guard.subprocess, "run", raise_timeout)
    assert guard.evaluate(_input()) == (False, "guard_timeout")


def test_missing_guard_blocks(monkeypatch):
    def raise_missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(guard.subprocess, "run", raise_missing)
    assert guard.evaluate(_input()) == (False, "guard_unavailable")


def test_permission_error_blocks(monkeypatch):
    def raise_permission(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(guard.subprocess, "run", raise_permission)
    assert guard.evaluate(_input()) == (False, "guard_error")


def test_decode_error_blocks(monkeypatch):
    def raise_decode(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(guard.subprocess, "run", raise_decode)
    assert guard.evaluate(_input()) == (False, "guard_invalid_output")


def test_missing_command_blocks():
    assert guard.evaluate(_input(None)) == (False, "guard_invalid_input")


@pytest.mark.parametrize("cwd", [".", "./project", "../other", "src/tools", ""])
def test_relative_bash_cwd_blocks_before_inspection(monkeypatch, cwd):
    """A relative cwd must not classify a different checkout than Bash executes."""
    monkeypatch.setattr(
        guard,
        "_guard_allows",
        lambda *_args, **_kwargs: pytest.fail(
            "Guard must not inspect a command with ambiguous relative cwd"
        ),
    )
    payload = _input()
    payload["tool_input"]["cwd"] = cwd

    assert guard.evaluate(payload) == (False, "guard_invalid_input")


def test_command_is_passed_as_one_argv_item(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _result(
            {"classification": {"explicitly_benign": True}, "minimum_action": "allow"}
        )

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    command = "echo $(touch /tmp/guard-argv-test)"
    assert guard.evaluate(_input(command)) == (True, "guard_allow")
    assert seen["argv"] == ["/__hol_guard_test__/bin/hol-guard", "command", "test", "--json", "--", command]


@pytest.mark.parametrize(
    "command",
    ["-n", "--help", "printf 'first\\nsecond'", "echo 'quoted argument'"],
)
def test_option_like_and_multiline_commands_remain_positional(monkeypatch, command):
    captured = []

    def fake_run(argv, **kwargs):
        captured.append(argv)
        return _result({
            "classification": {"explicitly_benign": True},
            "minimum_action": "allow",
        })

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    assert guard.evaluate(_input(command)) == (True, "guard_allow")
    assert captured == [["/__hol_guard_test__/bin/hol-guard", "command", "test", "--json", "--", command]]


def test_guard_runs_in_explicit_bash_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return _result(
            {"classification": {"explicitly_benign": True}, "minimum_action": "allow"}
        )

    monkeypatch.setattr(guard.shutil, "which", lambda _: "/__hol_guard_test__/bin/hol-guard")
    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    assert guard.evaluate(_input(cwd=str(tmp_path))) == (True, "guard_allow")
    assert seen["cwd"] == str(tmp_path)


def test_persisted_bash_cwd_is_added_to_hook_input(tmp_path):
    workspace = tmp_path / "workspace"
    prior_cwd = workspace / "nested"
    workspace.mkdir()
    prior_cwd.mkdir()
    context = SimpleNamespace(cwd=prior_cwd, workspace_root=workspace)
    tool = SimpleNamespace(name="Bash")

    prepared = _prepare_pre_tool_hook_input(context, tool, {"command": "git status"})

    assert prepared == {"command": "git status", "cwd": str(prior_cwd)}


def test_null_bash_cwd_uses_persisted_context(tmp_path):
    """Bash treats cwd=None as unspecified, so Guard must use the same cwd."""
    workspace = tmp_path / "workspace"
    persisted = workspace / "nested"
    persisted.mkdir(parents=True)
    context = SimpleNamespace(cwd=persisted, workspace_root=workspace)
    tool = SimpleNamespace(name="Bash")
    original = {"command": "git status", "cwd": None}

    prepared = _prepare_pre_tool_hook_input(context, tool, original)

    assert prepared == {"command": "git status", "cwd": str(persisted)}
    assert original["cwd"] is None


def test_null_bash_cwd_uses_workspace_root_without_persisted_cwd(tmp_path):
    """The hook and Bash must agree when only workspace_root is available."""
    context = SimpleNamespace(cwd=None, workspace_root=tmp_path)
    tool = SimpleNamespace(name="Bash")
    original = {"command": "git status", "cwd": None}

    prepared = _prepare_pre_tool_hook_input(context, tool, original)

    assert prepared == {"command": "git status", "cwd": str(tmp_path)}


def test_explicit_bash_cwd_wins_over_persisted_context(tmp_path):
    workspace = tmp_path / "workspace"
    persisted = workspace / "persisted"
    explicit = workspace / "explicit"
    workspace.mkdir()
    persisted.mkdir()
    explicit.mkdir()
    context = SimpleNamespace(cwd=persisted, workspace_root=workspace)
    tool = SimpleNamespace(name="Bash")
    original = {"command": "git status", "cwd": str(explicit)}

    prepared = _prepare_pre_tool_hook_input(context, tool, original)

    assert prepared is original
    assert prepared["cwd"] == str(explicit)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permission semantics")
def test_non_executable_guard_exits_with_blocking_code(tmp_path):
    fake_guard = tmp_path / "hol-guard"
    fake_guard.write_text("#!/bin/sh\nexit 0\n")
    fake_guard.chmod(0o644)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=json.dumps(_input()),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == guard.BLOCK_EXIT
    assert "guard_unavailable" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable script fixture")
def test_invalid_utf8_guard_output_exits_with_blocking_code(tmp_path):
    fake_guard = tmp_path / "hol-guard"
    fake_guard.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "os.write(1, b'\\xff')\n"
    )
    fake_guard.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=json.dumps(_input()),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == guard.BLOCK_EXIT
    assert "guard_invalid_output" in result.stderr


def test_invalid_subprocess_arguments_block(monkeypatch):
    """A subprocess argument error must never allow the Bash tool to run."""
    def raise_invalid_argv(*args, **kwargs):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(guard.subprocess, "run", raise_invalid_argv)
    assert guard.evaluate(_input("echo \\x00bad")) == (False, "guard_error")


@pytest.mark.skipif(os.name == "nt", reason="POSIX argv NUL validation")
def test_invalid_argv_nul_exits_with_blocking_code(tmp_path):
    """Actual hook process must exit with 2, not the nonblocking code 1."""
    guard_bin = tmp_path / "hol-guard"
    guard_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    guard_bin.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=json.dumps(_input("echo \x00bad")),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == guard.BLOCK_EXIT
    assert "guard_error" in result.stderr

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture semantics")
@pytest.mark.parametrize("failure", ["not_executable", "invalid_utf8_output"])
async def test_hook_executor_blocks_guard_launch_and_decode_failures(
    tmp_path, monkeypatch, failure
):
    """The actual hook runner must receive a blocking result, not exit 1."""
    import shlex

    from src.hooks.hook_executor import _execute_command_hook
    from src.hooks.hook_types import HookConfig

    fake_guard = tmp_path / "hol-guard"
    if failure == "not_executable":
        fake_guard.write_text("#!/bin/sh\nexit 0\n")
        fake_guard.chmod(0o644)
    else:
        fake_guard.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "os.write(1, b'\\xff')\n"
        )
        fake_guard.chmod(0o755)

    monkeypatch.setenv(
        "PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"
    )
    hook = HookConfig(
        type="command",
        command=(
            f"{shlex.quote(sys.executable)} "
            f"{shlex.quote(str(Path(guard.__file__).resolve()))}"
        ),
    )
    result = await _execute_command_hook(hook, _input())

    assert result.exit_code == guard.BLOCK_EXIT
    assert result.blocking_error is not None
    assert (
        "guard_unavailable" if failure == "not_executable" else "guard_invalid_output"
    ) in result.blocking_error

def test_overlong_json_integer_exits_with_blocking_code():
    """JSON integer conversion errors must deny, not exit with code 1."""
    get_limit = getattr(sys, "get_int_max_str_digits", None)
    if get_limit is None or get_limit() == 0:
        pytest.skip("Python JSON integer digit limit is unavailable or disabled")

    oversized_int = "9" * (get_limit() + 1)
    payload = (
        '{"hook_event":"PreToolUse","tool_name":"Bash",'
        '"tool_input":{"command":"git status"},"oversized":'
        + oversized_int + "}"
    )
    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__))],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == guard.BLOCK_EXIT
    assert "guard_invalid_input" in result.stderr


@pytest.mark.asyncio
async def test_pre_tool_hook_executor_receives_actual_bash_working_directory(monkeypatch, tmp_path):
    """PreToolUse must inspect the directory that Bash will actually run in."""
    from src.hooks import hook_executor
    from src.services.tool_execution.tool_hooks import run_pre_tool_use_hooks

    workspace = tmp_path / "workspace"
    persisted = workspace / "persisted"
    explicit = workspace / "explicit"
    persisted.mkdir(parents=True)
    explicit.mkdir()
    context = SimpleNamespace(
        cwd=persisted,
        workspace_root=workspace,
        abort_controller=SimpleNamespace(signal=SimpleNamespace(aborted=False)),
    )
    tool = SimpleNamespace(name="Bash")
    monkeypatch.setattr(hook_executor, "has_hook_for_event", lambda *_args: True)
    observed = []

    async def capture_pretool(tool_name, call_id, hook_input, hook_context):
        assert tool_name == "Bash"
        assert hook_context is context
        observed.append(hook_input)
        yield {}

    monkeypatch.setattr(hook_executor, "execute_pre_tool_hooks", capture_pretool)

    implicit_input = {"command": "git status"}
    events = [
        result async for result in run_pre_tool_use_hooks(
            context, tool, implicit_input, "implicit-cwd",
        )
    ]
    assert events == []
    assert observed[-1]["cwd"] == str(persisted)
    assert "cwd" not in implicit_input

    explicit_input = {"command": "git status", "cwd": str(explicit)}
    events = [
        result async for result in run_pre_tool_use_hooks(
            context, tool, explicit_input, "explicit-cwd",
        )
    ]
    assert events == []
    assert observed[-1]["cwd"] == str(explicit)
    assert observed[-1] is explicit_input


def test_relative_guard_path_cannot_change_binary_with_bash_cwd(monkeypatch, tmp_path):
    """A relative PATH shim under the launch directory cannot forge a Guard allow."""
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    bash_cwd = tmp_path / "bash"
    bash_cwd.mkdir()
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(guard.shutil, "which", lambda _: "./bin/hol-guard")
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("untrusted local Guard shim must not run"),
    )
    assert guard.evaluate(_input(cwd=str(bash_cwd))) == (
        False, "guard_untrusted_executable"
    )


@pytest.mark.parametrize("path_entry", [".", "", "bin", "../shims"])
def test_implicit_bash_cwd_rejects_relative_path_entries(monkeypatch, path_entry):
    """A bare Guard executable must not be selected from a local PATH shim."""
    monkeypatch.setenv("PATH", f"{path_entry}{os.pathsep}/usr/bin")
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Relative PATH must not execute Guard"),
    )
    assert guard.evaluate(_input()) == (False, "guard_untrusted_executable")


@pytest.mark.parametrize("link_to_outside", [False, True])
def test_implicit_bash_cwd_rejects_absolute_worktree_guard(monkeypatch, tmp_path, link_to_outside):
    """No implicit-cwd Guard evaluation may launch a repo-controlled shim."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    local_bin = project / "bin"
    local_bin.mkdir()
    selected = local_bin / "hol-guard"
    if link_to_outside:
        outside = tmp_path / "trusted-guard"
        outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        selected.symlink_to(outside)
    else:
        selected.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", str(local_bin))
    monkeypatch.setattr(guard.shutil, "which", lambda _: str(selected))
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("untrusted Guard binary must not run"),
    )
    assert guard.evaluate(_input()) == (False, "guard_untrusted_executable")


def test_missing_guard_lookup_blocks_explicit_bash_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(guard.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("Missing Guard binary must not launch"),
    )
    assert guard.evaluate(_input(cwd=str(tmp_path))) == (False, "guard_unavailable")

@pytest.mark.parametrize("git_marker", ["directory", "file"])
@pytest.mark.parametrize("spoofed_nested_git", [False, True])
def test_nested_bash_cwd_rejects_project_guard_binary(monkeypatch, tmp_path, git_marker, spoofed_nested_git):
    """The Guard shim cannot hide at the Git root when Bash runs in a child."""
    workspace = tmp_path / "workspace"
    nested = workspace / "src" / "nested"
    project_guard = workspace / "node_modules" / ".bin" / "hol-guard"
    nested.mkdir(parents=True)
    project_guard.parent.mkdir(parents=True)
    project_guard.write_text("#!/bin/sh\nexit 0\n")
    if spoofed_nested_git:
        (nested / ".git").write_text("fake nested marker")
    marker = workspace / ".git"
    if git_marker == "directory":
        marker.mkdir()
    else:
        # Linked Git worktrees identify their root with a .git file.
        marker.write_text("gitdir: /does/not/need/to/exist\n")

    monkeypatch.setattr(guard.shutil, "which", lambda _name: str(project_guard))
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Never execute a project Guard shim"),
    )
    assert guard.evaluate(_input(cwd=str(nested))) == (
        False,
        "guard_untrusted_executable",
    )

def test_guard_project_owned_symlink_cannot_launder_external_target(monkeypatch, tmp_path):
    """A project-owned PATH link remains untrusted when it points outside Git."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    outside = tmp_path / "trusted-bin" / "hol-guard"
    outside.parent.mkdir()
    outside.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    project_bin = project / ".bin"
    project_bin.mkdir()
    project_link = project_bin / "hol-guard"
    try:
        project_link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(guard.shutil, "which", lambda _: str(project_link))
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("untrusted Guard symlink must never launch"),
    )
    assert guard.evaluate(_input(cwd=str(project))) == (
        False, "guard_untrusted_executable"
    )


@pytest.mark.parametrize("symlinked", [False, True])
def test_explicit_bash_cwd_rejects_guard_from_launcher_repo(monkeypatch, tmp_path, symlinked):
    """The hook launcher repo must not supply a fake Guard for another Bash cwd."""
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    (launcher / ".git").mkdir()
    selected = launcher / ".bin" / "hol-guard"
    selected.parent.mkdir()
    if symlinked:
        target = tmp_path / "trusted" / "hol-guard"
        target.parent.mkdir()
        target.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
        try:
            selected.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is unavailable")
    else:
        selected.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    bash_cwd = tmp_path / "other-checkout"
    bash_cwd.mkdir()
    (bash_cwd / ".git").mkdir()
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(guard.shutil, "which", lambda _: str(selected))
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("launch-checkout shim must not execute"),
    )
    assert guard.evaluate(_input(cwd=str(bash_cwd))) == (
        False, "guard_untrusted_executable"
    )


def test_explicit_bash_cwd_allows_trusted_external_guard(monkeypatch, tmp_path):
    """An external Guard install stays usable across independent checkouts."""
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    (launcher / ".git").mkdir()
    bash_cwd = tmp_path / "bash"
    bash_cwd.mkdir()
    (bash_cwd / ".git").mkdir()
    trusted_bin = tmp_path / "trusted" / "hol-guard"
    trusted_bin.parent.mkdir()
    trusted_bin.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(guard.shutil, "which", lambda _: str(trusted_bin))
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs["cwd"]
        return _result(
            {"classification": {"explicitly_benign": True}, "minimum_action": "allow"}
        )

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    assert guard.evaluate(_input(cwd=str(bash_cwd))) == (True, "guard_allow")
    assert seen["argv"][0] == str(trusted_bin)
    assert seen["cwd"] == str(bash_cwd)

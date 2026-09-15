import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.hooks import hol_guard_pretool as guard
from src.services.tool_execution.tool_hooks import _prepare_pre_tool_hook_input


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
    assert seen["argv"] == ["hol-guard", "command", "test", command, "--json"]


def test_guard_runs_in_explicit_bash_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return _result(
            {"classification": {"explicitly_benign": True}, "minimum_action": "allow"}
        )

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
    assert "guard_error" in result.stderr


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

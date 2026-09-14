import json
import subprocess
from types import SimpleNamespace

from src.hooks import hol_guard_pretool as guard


def _input(command: object = "git status") -> dict[str, object]:
    return {
        "hook_event": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
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

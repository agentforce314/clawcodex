"""Regression coverage for fail-closed HOL Guard hook evaluation."""

import io
import json
import sys

import pytest

from src.hooks import hol_guard_pretool as guard


def test_unexpected_guard_evaluation_error_exits_with_blocking_code(monkeypatch, capsys):
    """Unknown internal failures must not become hook runner's nonblocking status 1."""
    valid_request = {
        "hook_event": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(valid_request)))

    def fail_unexpectedly(_payload):
        raise RuntimeError("simulated unexpected Guard evaluation failure")

    monkeypatch.setattr(guard, "evaluate", fail_unexpectedly)
    assert guard.main() == guard.BLOCK_EXIT
    assert capsys.readouterr().err.strip() == "guard_error"


@pytest.mark.parametrize("parser_error", [RuntimeError("parser failure"), MemoryError("allocation failure")])
def test_unexpected_guard_input_parse_error_exits_with_blocking_code(
    monkeypatch, capsys, parser_error
):
    """Parser errors cannot become the hook runner's nonblocking exit status 1."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    def fail_parser(*_args, **_kwargs):
        raise parser_error

    monkeypatch.setattr(guard.json, "load", fail_parser)
    assert guard.main() == guard.BLOCK_EXIT
    assert capsys.readouterr().err.strip() == "guard_invalid_input"

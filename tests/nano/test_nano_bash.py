"""Nano bash: no timeout ceiling (pi parity for long-running work).

TB 2.1 evidence (tb21-nano-flash-max-2): the 2-minute default / 10-minute
cap forced nohup-and-poll loops — 225 poll-pattern commands across the 25
failed tasks; compile-compcert burned 111 polls and hit the 300-turn
ceiling mid-build. Under nano a long build blocks in ONE call; the abort
signal and the task wall-clock remain the backstops. Stock behavior is
byte-identical.
"""

from __future__ import annotations

import pytest

from src.nano.state import set_nano_mode
from src.tool_system.errors import ToolInputError
from src.tool_system.tools import BashTool


@pytest.fixture
def ctx(tmp_path):
    from src.tool_system.context import ToolContext

    return ToolContext(cwd=tmp_path, workspace_root=tmp_path)


def _run(ctx, **extra):
    return BashTool.call({"command": "echo long-ok", **extra}, ctx)


def test_nano_accepts_timeouts_beyond_the_stock_cap(ctx):
    set_nano_mode(True)
    result = _run(ctx, timeout=1_200_000)  # 20 minutes
    assert "long-ok" in str(result.output)


def test_stock_still_rejects_beyond_cap(ctx):
    with pytest.raises(ToolInputError, match="must not exceed"):
        _run(ctx, timeout=1_200_000)


def test_nano_default_runs_without_explicit_timeout(ctx):
    set_nano_mode(True)
    result = _run(ctx)
    assert "long-ok" in str(result.output)


def test_stock_default_unchanged(ctx):
    result = _run(ctx)
    assert "long-ok" in str(result.output)


def test_minimum_floor_still_enforced_in_nano(ctx):
    set_nano_mode(True)
    with pytest.raises(ToolInputError, match="at least 1000 ms"):
        _run(ctx, timeout=10)


def test_idle_watchdog_kills_silent_stuck_command(ctx, monkeypatch):
    import time

    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    set_nano_mode(True)
    t0 = time.monotonic()
    result = BashTool.call({"command": "sleep 60"}, ctx)
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"watchdog too slow: {elapsed:.0f}s"
    assert result.is_error
    assert "no output" in str(result.output)
    assert "re-run" in str(result.output)


def test_idle_watchdog_spares_chatty_long_command(ctx, monkeypatch):
    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    set_nano_mode(True)
    result = BashTool.call(
        {"command": "for i in 1 2 3 4; do echo tick-$i; sleep 2; done; echo chatty-done"},
        ctx,
    )
    assert not result.is_error
    assert "chatty-done" in str(result.output)


def test_pipe_drain_handles_output_beyond_pipe_buffer(ctx):
    # >64KB written before exit used to fill the un-drained pipe and block
    # the child forever (masked by the stock hard timeout as a bogus
    # "timed out"). The concurrent readers drain it. Marker LAST: nano
    # keeps the tail of long output (pi-style), not the head.
    set_nano_mode(True)
    result = BashTool.call(
        {"command": "python3 -c \"print('x'*300000)\"; echo drained-ok"}, ctx
    )
    assert not result.is_error
    assert "drained-ok" in str(result.output)
    assert result.output.get("exit_code") == 0


def test_pipe_drain_fixes_stock_mode_too(ctx):
    result = BashTool.call(
        {"command": "echo stock-ok; python3 -c \"print('y'*300000)\""}, ctx
    )
    assert not result.is_error
    assert "stock-ok" in str(result.output)
    assert result.output.get("exit_code") == 0


def test_stock_has_no_idle_watchdog(ctx, monkeypatch):
    # Stock keeps its hard-timeout model: a 7s-silent command with a 15s
    # timeout completes even with the env set (the watchdog is nano-only).
    monkeypatch.setenv("NANO_BASH_IDLE_TIMEOUT_S", "5")
    result = BashTool.call({"command": "sleep 7 && echo stock-quiet-ok", "timeout": 15000}, ctx)
    assert not result.is_error
    assert "stock-quiet-ok" in str(result.output)


def _spill_path_from(text: str) -> str:
    import re

    m = re.search(r"Full output: (\S+)\]", text)
    assert m, f"no spill footer in: {text[-400:]}"
    return m.group(1)


def test_nano_tail_keep_with_full_output_spill(ctx, monkeypatch):
    # pi parity: long output keeps the TAIL (where a build's error and
    # final metrics live) and the complete stream spills to a temp file
    # whose path the model gets — instead of stock's head-keep that
    # drops exactly the interesting part and discards the rest forever.
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2000")
    set_nano_mode(True)
    cmd = (
        "echo HEAD-MARKER; "
        "python3 -c \"print('filler-line\\n'*800, end='')\"; "
        "echo TAIL-MARKER"
    )
    result = BashTool.call({"command": cmd}, ctx)
    assert not result.is_error
    assert result.output.get("exit_code") == 0
    out = result.output["stdout"]
    assert "TAIL-MARKER" in out
    assert "HEAD-MARKER" not in out  # tail-keep, not head-keep
    assert "of 802 lines" in out  # HEAD + 800 fillers + TAIL
    assert len(out) < 2000 + 400  # bounded reply: limit + footer slack

    from pathlib import Path

    full = Path(_spill_path_from(out)).read_text()
    assert full.startswith("HEAD-MARKER")  # complete from byte 0
    assert full.endswith("TAIL-MARKER\n")
    assert full.count("\n") == 802


def test_nano_small_output_passes_through_unchanged(ctx, monkeypatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2000")
    set_nano_mode(True)
    result = BashTool.call({"command": "echo small-ok"}, ctx)
    out = result.output["stdout"]
    assert "small-ok" in out
    assert "[Showing" not in out and "Full output" not in out


def test_nano_stderr_spills_separately_from_stdout(ctx, monkeypatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2000")
    set_nano_mode(True)
    cmd = (
        "python3 -c \"import sys; sys.stderr.write('err-line\\n'*600); "
        "sys.stderr.write('ERR-TAIL\\n')\"; echo out-ok"
    )
    result = BashTool.call({"command": cmd}, ctx)
    assert "out-ok" in result.output["stdout"]
    assert "Full output" not in result.output["stdout"]  # stdout was small
    err = result.output["stderr"]
    assert "ERR-TAIL" in err
    from pathlib import Path

    full = Path(_spill_path_from(err)).read_text()
    assert full.startswith("err-line") and full.endswith("ERR-TAIL\n")


def test_nano_tail_decode_never_splits_multibyte(ctx, monkeypatch):
    # The rolling-tail trim can cut mid-UTF-8-sequence; the render must
    # re-align (pi's trimToLastUtf8Bytes) so no replacement chars leak.
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "1000")
    set_nano_mode(True)
    # Emit UTF-8 bytes explicitly: Windows stdout defaults to a legacy code
    # page, which would test a different encoding instead of a split sequence.
    result = BashTool.call(
        {
            "command": "python3 -c \"import sys; sys.stdout.buffer.write(('\\u00e9'*5000+'\\n').encode('utf-8'))\""
        },
        ctx,
    )
    out = result.output["stdout"]
    assert "Full output:" in out
    assert "�" not in out


def test_stock_truncation_unchanged_head_keep(ctx, monkeypatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2000")
    result = BashTool.call(
        {"command": "echo STOCK-HEAD; python3 -c \"print('line\\n'*1000, end='')\""},
        ctx,
    )
    out = result.output["stdout"]
    assert "STOCK-HEAD" in out  # head kept
    assert "lines truncated] ..." in out  # stock marker format
    assert "Full output:" not in out  # no spill file in stock


def test_nano_bash_schema_drops_background_trap():
    # run_in_background needs TaskOutput (absent in nano) — a backgrounded
    # command strands its output; the nano schema must not advertise it.
    from src.nano.registry import build_nano_registry

    bash = next(t for t in build_nano_registry().list_tools() if t.name == "Bash")
    assert "run_in_background" not in bash.input_schema["properties"]
    assert "1-600" not in str(bash.input_schema["properties"].get("timeout_s", {}))
    # Stock schema untouched.
    assert "run_in_background" in BashTool.input_schema["properties"]

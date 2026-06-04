"""Regression tests for bash timeout vs ESC-cancel distinction.

Before this fix, the Python bash supervisor (``_run_bash_with_abort``)
set ``interrupted=True`` on BOTH ESC-abort and timeout. The two paths
then collapsed into one tool_result shape:

* content ends with ``<error>Command was aborted before completion</error>``
* ``is_error: True``

The TS reference at ``typescript/src/utils/ShellCommand.ts:135-141, 302,
323-328`` and ``typescript/src/tools/BashTool/BashTool.tsx:610-630``
deliberately keeps the two paths distinct:

* **ESC abort**: SIGKILL → exit code 137 → ``interrupted=true`` →
  ``is_error=true`` → ``<error>Command was aborted before completion</error>``
* **Timeout**: SIGTERM → exit code 143 → ``interrupted=false`` →
  ``is_error=false`` → stderr is prepended with
  ``"Command timed out after <duration>"`` and the ``<error>`` tag is NOT
  added.

Why the asymmetry: ``is_error=true`` is a Claude API signal that the
tool failed. ESC is a user-initiated rejection so the model should not
retry. Timeout is a non-fatal runtime condition; the model reads the
duration marker in stderr and decides whether to retry with a longer
timeout, change the approach, or give up — the ``<error>`` tag would
falsely promote a timeout to a tool failure.

These tests pin the parity at three layers:

1. ``_run_bash_with_abort`` returns ``interrupted=False, timed_out=True``
   on timeout (and the converse on ESC).
2. ``_bash_call`` produces distinct ToolResult shapes for the two cases.
3. ``_bash_map_result_to_api`` only emits the ``<error>`` tag and
   ``is_error=True`` for the ESC path.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.tools.bash.bash_tool import (
    BASH_TOOL_NAME,
    _bash_map_result_to_api,
    _run_bash_with_abort,
)
from src.utils.abort_controller import AbortController


def test_run_bash_with_abort_sets_only_timed_out_on_timeout(tmp_path: Path) -> None:
    """Pure supervisor test: a command that exceeds ``timeout_s`` returns
    ``interrupted=False`` and ``timed_out=True``. Mirrors TS where the
    timeout path is labelled with the SIGTERM exit code (143) while
    ``interrupted`` is reserved for the SIGKILL/ESC path.
    """
    start = time.monotonic()
    result = _run_bash_with_abort(
        ["bash", "-lc", "sleep 3; echo done"],
        cwd=str(tmp_path),
        timeout_s=1,
        abort_signal=None,
    )
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert result.interrupted is False, (
        "timeout must NOT set interrupted=True — TS distinguishes the two via "
        "the result-label exit code (SIGKILL=137 for ESC, SIGTERM=143 for "
        "timeout). Conflating them makes the model treat timed-out commands "
        "as user-cancelled and retry them on resume."
    )
    # Sanity: SIGKILL takes effect near-instantly; the timeout deadline
    # is 1s + at most one ``_ABORT_POLL_INTERVAL_S`` (50ms) jitter.
    assert elapsed < 3.0, (
        f"timeout supervisor should have killed the process well under "
        f"the 3s sleep, took {elapsed:.2f}s"
    )


def test_run_bash_with_abort_sets_only_interrupted_on_esc(tmp_path: Path) -> None:
    """Pure supervisor test: when the abort signal fires, the result must
    be ``interrupted=True`` and ``timed_out=False`` regardless of how long
    the command would have taken. Mirrors TS where the abort path is
    labelled with SIGKILL=137.
    """
    ctrl = AbortController()

    def _trip_abort() -> None:
        # Give the subprocess time to actually start before we trip the
        # abort. 100ms is well within the supervisor's poll cadence and
        # the OS process-launch window on any reasonable host.
        time.sleep(0.1)
        ctrl.abort("user_interrupt")

    threading.Thread(target=_trip_abort, daemon=True).start()

    result = _run_bash_with_abort(
        ["bash", "-lc", "sleep 3; echo done"],
        cwd=str(tmp_path),
        timeout_s=30,  # well above the abort time
        abort_signal=ctrl.signal,
    )

    assert result.interrupted is True
    assert result.timed_out is False


def test_bash_map_result_to_api_timeout_emits_no_error_tag(tmp_path: Path) -> None:
    """Tool-result mapping: a timeout payload (``timed_out=True``, no
    ``interrupted`` key) must produce ``is_error=False`` and MUST NOT
    append ``<error>Command was aborted before completion</error>`` to
    the content. The stderr-embedded duration marker is the only
    model-facing signal — mirrors TS at
    ``BashTool.tsx:610-630`` where ``is_error: interrupted`` is the
    sole gate.
    """
    # The stderr string here uses ``format_duration``-style markup
    # (``30s``, not ``30 seconds``) — matches what ``_bash_call`` actually
    # writes when it composes the timeout payload via
    # ``src/utils/format.py:format_duration``.
    output = {
        "cwd": str(tmp_path),
        "exit_code": 143,
        "stdout": "partial\n",
        "stderr": "Command timed out after 30s",
        "timed_out": True,
    }
    block = _bash_map_result_to_api(output, "call_t")

    assert block["is_error"] is False, (
        "timeout must NOT set is_error=True — the Claude API treats "
        "is_error=true as a hard tool failure signal that encourages the "
        "model to retry; for a timeout the duration marker is the signal."
    )
    assert "Command timed out after 30s" in block["content"]
    assert (
        "<error>Command was aborted before completion</error>"
        not in block["content"]
    ), (
        "the ``<error>`` tag is reserved for user-initiated abort (ESC). "
        "Emitting it on timeout collapses the two cases together and makes "
        "the model misread timeouts as user cancellations."
    )


def test_bash_map_result_to_api_esc_emits_error_tag(tmp_path: Path) -> None:
    """Tool-result mapping: an ESC payload (``interrupted=True``) must
    produce ``is_error=True`` AND append the ``<error>`` tag — preserves
    the existing ESC behavior the REJECT_MESSAGE override in
    ``_dispatch_single_tool`` relies on as a fallback.
    """
    output = {
        "cwd": str(tmp_path),
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "interrupted": True,
    }
    block = _bash_map_result_to_api(output, "call_e")

    assert block["is_error"] is True
    assert (
        "<error>Command was aborted before completion</error>" in block["content"]
    )


def test_bash_call_timeout_payload_shape(tmp_path: Path) -> None:
    """End-to-end ``_bash_call`` test: a command that times out produces
    a ToolResult with ``is_error=False``, the stderr prepended with the
    timeout marker, and the output dict carries ``timed_out=True`` and
    NO ``interrupted`` key. This is the contract the model-facing
    mapping relies on. Duration marker uses ``format_duration`` so the
    1-second timeout renders as ``1s`` (TS parity).
    """
    from src.tool_system.tools.bash.bash_tool import _bash_call

    ctx = ToolContext(workspace_root=tmp_path)
    ctx.cwd = tmp_path

    result = _bash_call(
        {"command": "sleep 3; echo done", "timeout_s": 1},
        ctx,
    )

    assert isinstance(result.output, dict)
    assert result.output.get("timed_out") is True
    assert "interrupted" not in result.output, (
        "timeout must NOT also write the interrupted key — the two fields "
        "are the TS-parity discriminator between ESC-abort and timeout. "
        "Writing both (even as False) would let downstream readers that "
        "do truthy checks misroute."
    )
    assert "Command timed out after 1s" in result.output.get("stderr", "")
    assert result.is_error is False, (
        "_bash_call must mirror TS by setting is_error=False on timeout; "
        "the duration marker in stderr is the signal."
    )


def test_bash_call_esc_payload_shape(tmp_path: Path) -> None:
    """End-to-end ``_bash_call`` test: ESC-abort produces a ToolResult
    with ``is_error=True``, ``interrupted=True``, and NO ``timed_out``
    key. The mapping layer will then emit the ``<error>`` tag.
    """
    from src.tool_system.tools.bash.bash_tool import _bash_call

    ctx = ToolContext(workspace_root=tmp_path)
    ctx.cwd = tmp_path
    ctrl = AbortController()
    ctx.abort_controller = ctrl

    def _trip_abort() -> None:
        time.sleep(0.1)
        ctrl.abort("user_interrupt")

    threading.Thread(target=_trip_abort, daemon=True).start()

    result = _bash_call(
        {"command": "sleep 3; echo done", "timeout_s": 30},
        ctx,
    )

    assert isinstance(result.output, dict)
    assert result.output.get("interrupted") is True
    assert "timed_out" not in result.output, (
        "ESC-abort must NOT also write the timed_out key — the two fields "
        "are the TS-parity discriminator between ESC-abort and timeout."
    )
    assert result.is_error is True


def test_run_bash_with_abort_natural_exit_sets_neither_flag(tmp_path: Path) -> None:
    """Sanity check: a command that exits normally must have BOTH
    ``interrupted`` and ``timed_out`` False. Guards against a regression
    where a code change accidentally sets one of them on the happy path.
    """
    result = _run_bash_with_abort(
        ["bash", "-lc", "echo hello"],
        cwd=str(tmp_path),
        timeout_s=10,
        abort_signal=None,
    )

    assert result.interrupted is False
    assert result.timed_out is False
    assert result.returncode == 0
    assert "hello" in result.stdout

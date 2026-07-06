"""The ``Monitor`` tool (C5 Part 2) — stream a shell command's stdout to the
model as ``<monitor-output>`` notifications (~1s polling), rather than the
single completion notification the Bash ``run_in_background`` path delivers.

Port of ``typescript/src/tools/MonitorTool/MonitorTool.ts`` (MONITOR_TOOL flag
= true, build.ts:43). Reuses ``spawn_background_bash`` (the same detached shell
+ reaper) + ``enqueue_pending_notification`` (the shared queue the model drains
each turn). The novel pieces are (1) the per-monitor polling thread that tails
the output file and (2) BACKPRESSURE: a monitor that produces too many
notifications is AUTO-STOPPED — the exact guard the tools-round critic required
(an unbounded streamer floods the conversation). TS: "Monitors that produce too
many events are automatically stopped."
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from .bash.bash_tool import _bash_check_permissions

logger = logging.getLogger(__name__)

MONITOR_TOOL_NAME = "Monitor"
_MONITOR_TIMEOUT_S = 30 * 60  # 30 minutes (TS MONITOR_TIMEOUT_MS)
_POLL_INTERVAL_S = 1.0
#: BACKPRESSURE cap — after this many streamed notifications the monitor
#: auto-stops (kills the shell + sends a final notice). Bounds a chatty
#: monitor's conversation footprint (tools-critic: an unbounded poller floods).
_MONITOR_MAX_NOTIFICATIONS = 100


def _kill_monitor_task(task_id: str, context: ToolContext) -> None:
    """Best-effort kill of the underlying shell (auto-stop / timeout)."""
    try:
        import asyncio

        from src.tasks.local_shell import LocalShellTask

        asyncio.run(LocalShellTask().kill(task_id, context.runtime_tasks))
    except Exception:  # noqa: BLE001
        logger.debug("monitor kill failed for %s", task_id, exc_info=True)


def _stream_output(
    *, task_id: str, output_path: str, context: ToolContext, description: str
) -> None:
    """Tail ``output_path`` and enqueue each new batch of whole lines as a
    ``<monitor-output>`` notification. Stops when the task leaves ``running``
    (after a final drain), the 30-minute deadline passes, OR the notification
    cap is hit (auto-stop backpressure). Never raises."""
    try:
        from src.utils.message_queue_manager import enqueue_pending_notification

        deadline = time.monotonic() + _MONITOR_TIMEOUT_S
        pos = 0
        sent = 0
        path = Path(output_path)

        def _drain() -> int:
            """Enqueue the new whole lines (if any); return 1 if it enqueued."""
            nonlocal pos
            try:
                data = path.read_bytes()
            except OSError:
                return 0
            if len(data) <= pos:
                return 0
            chunk = data[pos:].decode("utf-8", "replace")
            pos = len(data)
            if "\n" not in chunk:
                # No complete line yet — rewind so the partial is re-read next poll.
                pos -= len(chunk.encode("utf-8"))
                return 0
            body, _, tail = chunk.rpartition("\n")
            pos -= len(tail.encode("utf-8"))
            lines = body.strip("\n")
            if not lines:
                return 0
            from xml.sax.saxutils import escape as _esc

            enqueue_pending_notification(
                value=(
                    f'<monitor-output task="{_esc(task_id)}" '
                    f'description="{_esc(description)}">\n{_esc(lines)}\n</monitor-output>'
                ),
                mode="task-notification",
            )
            return 1

        while time.monotonic() < deadline:
            sent += _drain()
            if sent >= _MONITOR_MAX_NOTIFICATIONS:
                # BACKPRESSURE: too many events → auto-stop the monitor.
                _drain()
                _kill_monitor_task(task_id, context)
                enqueue_pending_notification(
                    value=(
                        f'<monitor-output task="{task_id}" description="{description}">\n'
                        f"Monitor auto-stopped after {sent} notifications "
                        f"(too many events). Re-run with a tighter filter "
                        f"(e.g. grep) if you still need to watch this.\n"
                        f"</monitor-output>"
                    ),
                    mode="task-notification",
                )
                return
            state = context.runtime_tasks.get(task_id)
            status = getattr(state, "status", None)
            if state is None:
                return
            if status is not None and status != "running":
                _drain()  # final drain so the last lines aren't lost
                return
            time.sleep(_POLL_INTERVAL_S)
        # Deadline — stop the shell so it doesn't linger past the monitor.
        _drain()
        _kill_monitor_task(task_id, context)
    except Exception:  # noqa: BLE001 — a monitor poller must NEVER crash the app
        logger.debug("monitor poller failed for %s", task_id, exc_info=True)


def _monitor_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    command = tool_input.get("command")
    description = tool_input.get("description") or "Monitor shell command"
    if not isinstance(command, str) or not command.strip():
        raise ToolInputError("command must be a non-empty string")

    from .bash.background import spawn_background_bash

    cwd = Path(getattr(context, "cwd", None) or ".")
    spawned = spawn_background_bash(
        command=command, cwd=cwd, description=description, context=context
    )
    task_id = spawned.get("backgroundTaskId") or ""
    # The output path lives on the registered task state (spawn writes to
    # <tmp>/clawcodex-bg/<task_id>.log).
    state = context.runtime_tasks.get(task_id)
    output_path = getattr(state, "output_path", None) or getattr(state, "output_file", "")

    # Start the per-monitor streaming poller (daemon so it never blocks exit).
    threading.Thread(
        target=_stream_output,
        kwargs={
            "task_id": task_id,
            "output_path": output_path,
            "context": context,
            "description": description,
        },
        name=f"monitor-{task_id}",
        daemon=True,
    ).start()

    return ToolResult(
        name=MONITOR_TOOL_NAME,
        output={
            "taskId": task_id,
            "outputFile": output_path,
            "message": (
                f"Monitor task started with ID: {task_id}. Output is being "
                f"streamed to: {output_path}. You will receive notifications "
                f"as new output lines appear (~1s polling). Use TaskStop to "
                f"end monitoring when done."
            ),
        },
    )


MonitorTool: Tool = build_tool(
    name=MONITOR_TOOL_NAME,
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run and monitor",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in "
                    "active voice."
                ),
            },
        },
        "required": ["command", "description"],
    },
    call=_monitor_call,
    prompt=(
        "Execute a shell command in the background and stream its stdout "
        "line-by-line as notifications. Each polling interval (~1s), new "
        "output lines are delivered to you. Use this for monitoring logs, "
        "watching build output, or observing long-running processes. For "
        "one-shot 'wait until done' commands, prefer Bash with "
        "run_in_background instead. Monitors that produce too many events are "
        "automatically stopped — restart with a tighter filter if that happens."
    ),
    description="Stream a shell command's output as notifications.",
    max_result_size_chars=2000,
    is_read_only=lambda _input: False,
    is_concurrency_safe=lambda _input: False,
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("command", ""),
    # Monitor runs an arbitrary shell command — gate it exactly like Bash
    # (TS MonitorTool.checkPermissions delegates to bashToolHasPermission).
    check_permissions=_bash_check_permissions,
)

__all__ = ["MonitorTool", "MONITOR_TOOL_NAME"]

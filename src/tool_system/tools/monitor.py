"""The ``Monitor`` tool (C5 Part 2) — stream a shell command's stdout to the
model as render-path <task-notification> envelopes (~1s polling), rather than the
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
#: Per-notification SIZE cap (bytes of streamed body). The count cap alone
#: doesn't bound a FIREHOSE — one poll batches all new lines into one
#: notification, so a command dumping megabytes/second sends few-but-huge
#: notifications. Truncate each to the tail so a single notification can't
#: blow the conversation (critic C5-P2 #1: bound bytes, not just count).
_MONITOR_MAX_NOTIFICATION_BYTES = 8 * 1024
#: Max bytes read from the output file PER poll (TS diskOutput.ts:23
#: DEFAULT_MAX_READ_BYTES = 8 MiB). ``read_bytes()`` on the whole file is an
#: OOM vector for a firehose command; seek to the offset and read a bounded
#: chunk instead (critic C5-P2 #1).
_MONITOR_MAX_READ_BYTES = 8 * 1024 * 1024


def _monitor_notification_xml(
    task_id: str, output_file: str, lines: str, status: str = "running"
) -> str:
    """A ``<task-notification>`` envelope carrying a streamed batch (critic
    C5-P2 major: the invented ``<monitor-output>`` tag was incompatible with the
    render path — parse_task_id found no ``<task-id>`` child and the banner
    mis-rendered). Uses the real envelope structure the drain path parses
    (``<task-id>/<output-file>/<status>/<summary>``). ``status="running"`` (the
    streaming default) makes ``build_notification_turn`` frame it as a live
    update; the terminal auto-stop notice passes ``status="killed"`` so it gets
    the completion framing instead (critic C5-P2 minor #3)."""
    from xml.sax.saxutils import escape as _esc

    from src.constants.xml import (
        OUTPUT_FILE_TAG,
        STATUS_TAG,
        SUMMARY_TAG,
        TASK_ID_TAG,
        TASK_NOTIFICATION_TAG,
    )

    return (
        f"<{TASK_NOTIFICATION_TAG}>\n"
        f"<{TASK_ID_TAG}>{_esc(task_id)}</{TASK_ID_TAG}>\n"
        f"<{OUTPUT_FILE_TAG}>{_esc(output_file)}</{OUTPUT_FILE_TAG}>\n"
        f"<{STATUS_TAG}>{_esc(status)}</{STATUS_TAG}>\n"
        f"<{SUMMARY_TAG}>{_esc(lines)}</{SUMMARY_TAG}>\n"
        f"</{TASK_NOTIFICATION_TAG}>"
    )


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
    <task-notification> envelope (status=running). Stops when the task leaves ``running``
    (after a final drain), the 30-minute deadline passes, OR the notification
    cap is hit (auto-stop backpressure). Never raises."""
    # DELIBERATE DIVERGENCE from TS (accepted as port scope, c5p2-critic APPROVE):
    # each drain enqueues a <task-notification> that the drain loop turns into an
    # internal model turn, whereas TS injects monitor deltas as passive
    # task_status/deltaSummary ATTACHMENTS that ride the next natural turn
    # (attachments.ts / framework.ts). Building that attachment pipeline for one
    # tool is out of scope; the cost here is bounded (≤ _MONITOR_MAX_NOTIFICATIONS
    # turns then auto-stop, and ZERO for a quiet monitor since a drain only fires
    # on new output). If turn-per-drain ever bites, rate-limit monitor turns or
    # lower the cap rather than "fixing" this by accident.
    try:
        from src.utils.message_queue_manager import enqueue_pending_notification

        deadline = time.monotonic() + _MONITOR_TIMEOUT_S
        pos = 0
        sent = 0
        path = Path(output_path)

        def _drain(final: bool = False) -> int:
            """Enqueue the new whole lines (if any); return 1 if it enqueued.

            Reads at most ``_MONITOR_MAX_READ_BYTES`` from the current offset —
            never the whole file — so a firehose command can't OOM the agent
            (TS getTaskOutputDelta bounds each read the same way).

            ``final=True`` (the terminal drain) emits any trailing partial line
            too, so a command whose last output has no newline (``printf done``)
            isn't lost (critic C5-P2 #3.ii)."""
            nonlocal pos
            try:
                with open(path, "rb") as fh:
                    fh.seek(pos)
                    raw = fh.read(_MONITOR_MAX_READ_BYTES)  # bounded read
            except OSError:
                return 0
            if not raw:
                return 0
            read_n = len(raw)  # advance the offset by BYTES read (offset arithmetic
            pos += read_n      # stays in bytes; decode replacement can't drift it)
            chunk = raw.decode("utf-8", "replace")
            if "\n" in chunk:
                if final:
                    lines = chunk  # include the trailing partial on the last drain
                else:
                    body, _, tail = chunk.rpartition("\n")
                    pos -= len(tail.encode("utf-8"))  # hold the partial for next poll
                    lines = body
            else:
                # No newline in this window. Normally hold the partial for the
                # next poll — UNLESS this is the final drain, or the window is
                # FULL (a single line longer than the read cap would otherwise
                # stall forever with no progress; critic C5-P2 #3.i). In those
                # cases emit what we have so the stream keeps moving.
                if not final and read_n < _MONITOR_MAX_READ_BYTES:
                    pos -= read_n
                    return 0
                lines = chunk
            lines = lines.strip("\n")
            if not lines:
                return 0
            # Per-notification size bound (critic C5-P2 #1): keep the TAIL (the
            # newest output is what a watcher cares about) + a truncation
            # marker, so a firehose can't emit one giant notification.
            encoded = lines.encode("utf-8")
            if len(encoded) > _MONITOR_MAX_NOTIFICATION_BYTES:
                kept = encoded[-_MONITOR_MAX_NOTIFICATION_BYTES:].decode("utf-8", "replace")
                dropped = len(encoded) - _MONITOR_MAX_NOTIFICATION_BYTES
                lines = f"…[{dropped} earlier bytes truncated]…\n{kept}"
            enqueue_pending_notification(
                value=_monitor_notification_xml(task_id, output_path, lines),
                mode="task-notification",
            )
            return 1

        while time.monotonic() < deadline:
            sent += _drain()
            if sent >= _MONITOR_MAX_NOTIFICATIONS:
                # BACKPRESSURE: too many events → auto-stop the monitor.
                _drain(final=True)
                _kill_monitor_task(task_id, context)
                enqueue_pending_notification(
                    # status="killed": this notice is TERMINAL (the monitor just
                    # stopped), so it takes the completion framing, not the
                    # "STILL RUNNING" streaming preamble (critic C5-P2 minor #3).
                    value=_monitor_notification_xml(
                        task_id, output_path,
                        f"Monitor auto-stopped after {sent} notifications "
                        f"(too many events). Re-run with a tighter filter "
                        f"(e.g. grep) if you still need to watch this.",
                        status="killed"),
                    mode="task-notification",
                )
                return
            state = context.runtime_tasks.get(task_id)
            status = getattr(state, "status", None)
            if state is None:
                return
            if status is not None and status != "running":
                _drain(final=True)  # final drain: emit the last (maybe partial) lines
                return
            time.sleep(_POLL_INTERVAL_S)
        # Deadline — stop the shell so it doesn't linger past the monitor.
        _drain(final=True)
        _kill_monitor_task(task_id, context)
    except Exception:  # noqa: BLE001 — a monitor poller must NEVER crash the app
        logger.debug("monitor poller failed for %s", task_id, exc_info=True)


def _monitor_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    command = tool_input.get("command")
    description = tool_input.get("description") or "Monitor shell command"
    if not isinstance(command, str) or not command.strip():
        raise ToolInputError("command must be a non-empty string")

    # Monitor spawns via spawn_background_bash DIRECTLY (not _bash_call), so it
    # must apply the same pre-spawn safety — the hardcoded-dangerous-pattern
    # block + the C8 sandbox hard-gate — or it's a way around them (critic
    # C5-P2). check_permissions covers the permission RULES; this covers the
    # hard safety gates that live below the permission layer.
    from .bash.bash_tool import bash_command_safety_guard

    bash_command_safety_guard(command)

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
    max_result_size_chars=10_000,  # TS MonitorTool.ts:51
    is_read_only=lambda _input: False,
    is_concurrency_safe=lambda _input: True,  # TS MonitorTool.ts:54 (fire-and-forget spawn)
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("command", ""),
    # Monitor runs an arbitrary shell command — gate it exactly like Bash
    # (TS MonitorTool.checkPermissions delegates to bashToolHasPermission).
    check_permissions=_bash_check_permissions,
)

__all__ = ["MonitorTool", "MONITOR_TOOL_NAME"]

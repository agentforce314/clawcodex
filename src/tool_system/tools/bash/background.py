"""Background execution helpers for the Bash tool.

Mirrors the subset of ``typescript/src/tools/BashTool/BashTool.tsx`` that
handles ``run_in_background: true`` -- the command is spawned detached from
the foreground request, its combined stdout/stderr streams are captured to a
temp file, and a small metadata record is kept on ``ToolContext`` so
``TaskOutput`` can poll it later.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ...context import ToolContext


def _bg_output_dir() -> Path:
    """Return the directory where background-task stdout/stderr files live.

    Follows the convention used by
    ``typescript/src/utils/task/diskOutput.ts``: ``<tmp>/clawcodex-bg/``.
    """
    root = Path(tempfile.gettempdir()) / "clawcodex-bg"
    root.mkdir(parents=True, exist_ok=True)
    return root


def spawn_background_bash(
    *,
    command: str,
    cwd: Path,
    description: str | None,
    context: ToolContext,
) -> dict[str, Any]:
    """Spawn *command* in the background and register it on *context*.

    Returns a dict that mirrors the shape consumed by
    ``_bash_map_result_to_api``: it includes the background task id plus a
    human-readable message instructing the model how to poll the output.
    """
    task_id = uuid.uuid4().hex[:8]
    output_path = _bg_output_dir() / f"{task_id}.log"
    output_path.touch(exist_ok=True)

    output_handle = open(output_path, "wb", buffering=0)

    # Same wrapper the foreground path uses, so a trailing ``cd`` still writes
    # the final PWD for inspection. Exit code is appended to the log after the
    # process exits so ``TaskOutput`` can report it even if Popen.wait() races
    # with the reader.
    wrapped = (
        f"{{ {command}\n}}; __rc=$?; "
        f"echo \"__CLAWCODEX_EXIT__=$__rc\" >&2; "
        f"exit $__rc"
    )

    proc = subprocess.Popen(
        ["bash", "-lc", wrapped],
        cwd=str(cwd),
        stdout=output_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    entry: dict[str, Any] = {
        "task_id": task_id,
        "command": command,
        "description": description or command,
        "cwd": str(cwd),
        "started_at": time.time(),
        "output_path": str(output_path),
        "pid": proc.pid,
        "_proc": proc,
        "_handle": output_handle,
        "exit_code": None,
        "finished_at": None,
    }
    context.background_bash_tasks[task_id] = entry

    def _reap() -> None:
        try:
            rc = proc.wait()
        finally:
            try:
                output_handle.flush()
            except OSError:
                pass
            try:
                output_handle.close()
            except OSError:
                pass
            entry["exit_code"] = rc
            entry["finished_at"] = time.time()

    threading.Thread(
        target=_reap,
        name=f"bash-bg:{task_id}",
        daemon=True,
    ).start()

    message = (
        f"Command running in background with ID: {task_id}. "
        f"Output is being streamed to: {output_path}. "
        f"Use TaskOutput with task_id={task_id!r} to read the latest output "
        f"and check completion status."
    )
    return {
        "cwd": str(cwd),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "backgroundTaskId": task_id,
        "outputFilePath": str(output_path),
        "pid": proc.pid,
        "message": message,
    }


def read_background_output(
    context: ToolContext,
    task_id: str,
    *,
    max_bytes: int = 200_000,
) -> dict[str, Any] | None:
    """Return the current snapshot of a background Bash task, or ``None``.

    Result shape mirrors what ``TaskOutput`` exposes to the model:
        {
            "task_id": ...,
            "status": "running" | "completed" | "failed",
            "exit_code": int | None,
            "command": str,
            "output": str,       # combined stdout+stderr, possibly truncated
            "truncated": bool,   # True if the log was bigger than ``max_bytes``
            "pid": int,
            "started_at": float,
            "finished_at": float | None,
        }
    """
    entry = context.background_bash_tasks.get(task_id)
    if entry is None:
        return None

    output_path = Path(entry["output_path"])
    try:
        total_size = output_path.stat().st_size
    except OSError:
        total_size = 0

    output_bytes = b""
    truncated = False
    try:
        with open(output_path, "rb") as fh:
            if total_size > max_bytes:
                fh.seek(total_size - max_bytes)
                truncated = True
            output_bytes = fh.read()
    except OSError:
        output_bytes = b""

    try:
        output_text = output_bytes.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode("replace") shouldn't raise
        output_text = ""

    exit_code = entry.get("exit_code")
    # Strip the trailing __CLAWCODEX_EXIT__ marker we emit from the wrapper so
    # it never leaks into the model's transcript.
    marker = "__CLAWCODEX_EXIT__="
    if marker in output_text:
        idx = output_text.rfind(marker)
        # Trim everything from the last newline before the marker onward.
        nl = output_text.rfind("\n", 0, idx)
        if nl != -1:
            output_text = output_text[:nl]
        else:
            output_text = output_text[:idx]

    if exit_code is None:
        # Process may have died between ``_reap``'s wait() returning and our
        # read; double-check with Popen.poll().
        proc: subprocess.Popen | None = entry.get("_proc")
        if proc is not None:
            exit_code = proc.poll()
            if exit_code is not None:
                entry["exit_code"] = exit_code
                entry["finished_at"] = entry.get("finished_at") or time.time()

    if exit_code is None:
        status = "running"
    elif exit_code == 0:
        status = "completed"
    else:
        status = "failed"

    return {
        "task_id": task_id,
        "status": status,
        "exit_code": exit_code,
        "command": entry.get("command", ""),
        "description": entry.get("description", ""),
        "output": output_text,
        "truncated": truncated,
        "pid": entry.get("pid"),
        "started_at": entry.get("started_at"),
        "finished_at": entry.get("finished_at"),
    }


def stop_background_bash(context: ToolContext, task_id: str) -> bool:
    """Send SIGTERM to a running background task. Returns True on success."""
    entry = context.background_bash_tasks.get(task_id)
    if entry is None:
        return False
    proc: subprocess.Popen | None = entry.get("_proc")
    if proc is None or proc.poll() is not None:
        return False
    try:
        # Kill the whole process group started with ``start_new_session=True``
        # so that ``bash -lc "cmd"`` and any children terminate together.
        os.killpg(os.getpgid(proc.pid), 15)
    except (ProcessLookupError, PermissionError):
        return False
    return True

"""Background execution helpers for the Bash tool.

Mirrors the subset of ``typescript/src/tools/BashTool/BashTool.tsx`` that
handles ``run_in_background: true`` -- the command is spawned detached from
the foreground request, its combined stdout/stderr streams are captured to a
temp file, and a typed ``LocalShellTaskState`` is registered on the
``ToolContext.runtime_tasks`` registry so ``TaskOutput`` and ``TaskStop``
can dispatch on it.

Chapter-10 / Chunk B / WI-1.4: this module previously stored
dict-of-dicts entries on ``context.background_bash_tasks``. Writers now
populate ``context.runtime_tasks`` as the source of truth (typed
``LocalShellTaskState``); the legacy dict is kept in lockstep as a
compatibility view so external test fixtures or readers that haven't
migrated yet continue to work. The dict goes away in a follow-up phase.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ...context import ToolContext
from src.tasks.local_shell import LocalShellTaskState
from src.tasks_core import generate_task_id


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
    # Chapter-10 / WI-1.4: prefixed task id (``b<8 base36 chars>``) instead
    # of the legacy ``uuid4().hex[:8]``. Mirrors TS Task.ts:79-105 — the
    # ``b`` prefix is what TaskStop / TaskOutput dispatch on.
    task_id = generate_task_id("local_bash")
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

    # ``stdin=DEVNULL`` mirrors the foreground bash path: prevents background
    # commands that read fd 0 from blocking on a TTY inherited from clawcodex's
    # REPL (see bash_tool.py:_run_bash_with_abort for the same reasoning).
    from src.utils.subprocess_env import subprocess_env

    proc = subprocess.Popen(
        ["bash", "-lc", wrapped],
        cwd=str(cwd),
        # Scrub secret env vars when CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set,
        # so a prompt-injected background command can't exfiltrate a credential
        # via ${ANTHROPIC_API_KEY} (parity with TS subprocessEnv).
        env=subprocess_env(),
        stdin=subprocess.DEVNULL,
        stdout=output_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    started_at = time.time()
    state = LocalShellTaskState(
        id=task_id,
        type="local_bash",
        status="running",
        description=description or command,
        start_time=started_at,
        # ``output_file`` (chapter-10 base field) carries the same string
        # as ``output_path`` (bash-specific name kept for legacy readers).
        output_file=str(output_path),
        command=command,
        cwd=str(cwd),
        pid=proc.pid,
        output_path=str(output_path),
        proc=proc,
        handle=output_handle,
        # Owner = the spawning (sub-)agent, so a completing sub-agent reaps
        # the shells it started (None for the main session — never reaped by
        # an agent exit).
        agent_id=getattr(context, "agent_id", None),
    )
    context.runtime_tasks.upsert(state)
    # Chunk-B compat view: keep the legacy dict-of-dicts alive in lockstep
    # so readers that haven't migrated yet still work. The dict shares the
    # task id with runtime_tasks; the reaper updates both.
    context.background_bash_tasks[task_id] = state.to_legacy_dict()

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

            finished_at = time.time()

            def _patch(prev: Any) -> Any:
                if not isinstance(prev, LocalShellTaskState):
                    return prev
                # ``replace`` keeps every other field (incl. proc/handle)
                # so a still-pending TaskStop call has the Popen reference.
                from dataclasses import replace

                from src.tasks.eviction import schedule_eviction
                # Preserve a 'killed' status set by stop_background_bash (a
                # user kill) — don't reclassify it as 'failed' from the SIGTERM
                # exit code (critic C5-P1 #3). Its notified=True (set at kill)
                # also makes the enqueue below a no-op.
                new_status = (
                    "killed" if getattr(prev, "status", None) == "killed"
                    else ("completed" if rc == 0 else "failed")
                )
                # C5 Part 1: terminal state is left ``notified=False`` — the
                # forward-trap ``notified=True`` (which pre-suppressed any
                # completion notification, per the ch10 WI-2 comment) is
                # REMOVED. enqueue_shell_notification below atomically sets
                # notified=True AND sends (TS framework.ts:289 +
                # BashTool/prompt.ts:285-287 "you will be notified when it
                # completes"). The eviction sweeper's notify-before-evict guard
                # (eviction.py:97) now correctly keeps the entry until the
                # notification is delivered, then reclaims it.
                terminal = replace(
                    prev,
                    exit_code=rc,
                    finished_at=finished_at,
                    end_time=finished_at,
                    status=new_status,
                )
                return schedule_eviction(terminal)

            context.runtime_tasks.update(task_id, _patch)
            # Deliver the completion notification (C5 Part 1): atomically
            # check-and-set notified + enqueue. A no-op if already notified
            # (e.g. a TaskStop already delivered), so it's duplicate-safe.
            try:
                from src.utils.task_notification import enqueue_shell_notification

                enqueue_shell_notification(
                    task_id=task_id,
                    description=description or command,
                    status="completed" if rc == 0 else "failed",
                    output_file=str(output_path),
                    registry=context.runtime_tasks,
                    exit_code=rc,
                    tool_use_id=getattr(context, "tool_use_id", None),
                )
            except Exception:  # noqa: BLE001 — notification must not break reaping
                # FALLBACK: if the enqueue raised, the terminal state is still
                # notified=False and the eviction sweeper (which keeps
                # un-notified terminal tasks) would leak it forever. Mark it
                # notified so it stays evictable — degrading to the OLD
                # behavior (evictable, unsent) rather than a leak. This is
                # strictly better than the pre-C5 silent-drop.
                import logging as _logging
                _logging.getLogger(__name__).debug(
                    "[bg] shell completion notification failed", exc_info=True
                )
                try:
                    from dataclasses import replace as _replace

                    def _force_notified(prev: Any) -> Any:
                        if isinstance(prev, LocalShellTaskState) and not prev.notified:
                            return _replace(prev, notified=True)
                        return prev

                    context.runtime_tasks.update(task_id, _force_notified)
                except Exception:  # noqa: BLE001
                    pass
            # Mirror to the legacy dict in lockstep so old readers see the
            # exit code without round-tripping through runtime_tasks. The
            # legacy dict carries the chapter-10 status string too — older
            # callers that grew up reading ``entry["status"]`` get the same
            # vocabulary as the typed registry. The whole legacy dict goes
            # away when bg_tasks is removed in a follow-up.
            entry = context.background_bash_tasks.get(task_id)
            if entry is not None:
                entry["exit_code"] = rc
                entry["finished_at"] = finished_at
                entry["status"] = "completed" if rc == 0 else "failed"
                entry["end_time"] = finished_at

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
    # Atomically mark status='killed' + notified=True (TS killTask,
    # killShellTasks.ts:38-44). Setting notified=True SUPPRESSES the reaper's
    # later enqueue_shell_notification (it no-ops on notified=True), so a
    # user-killed bg bash produces NO completion notification — instead of the
    # spurious "failed with exit code N" the reaper would otherwise send
    # (critic C5-P1 #3). _patch preserves an existing 'killed' status.
    try:
        from dataclasses import replace as _replace

        def _mark_killed(prev: Any) -> Any:
            if isinstance(prev, LocalShellTaskState):
                return _replace(prev, status="killed", notified=True)
            return prev

        context.runtime_tasks.update(task_id, _mark_killed)
    except Exception:  # noqa: BLE001 — the SIGTERM already succeeded
        pass
    return True

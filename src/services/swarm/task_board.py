"""Serialized team task-board mutations and durable snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator


def write_json_atomic(path: Path, value: Any) -> None:
    """Replace a JSON snapshot without exposing a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def task_board(
    context: Any, *, write: bool = False
) -> Iterator[dict[str, dict[str, Any]]]:
    """Hold the shared board lock while reading or changing a team snapshot."""
    with context.task_board_lock:
        path = context.task_board_path
        if path is not None and path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or any(
                not isinstance(value, dict) for value in loaded.values()
            ):
                raise ValueError(f"Invalid task board: {path}")
            context.tasks.clear()
            context.tasks.update(loaded)
        before = deepcopy(context.tasks) if write else None
        try:
            yield context.tasks
            if write and path is not None:
                write_json_atomic(path, context.tasks)
        except BaseException:
            if before is not None:
                context.tasks.clear()
                context.tasks.update(before)
            raise


def with_task_board(*, write: bool = False) -> Callable:
    """Apply the board transaction to a synchronous task tool."""

    def decorate(call: Callable) -> Callable:
        @wraps(call)
        def locked(tool_input: dict, context: Any) -> Any:
            with task_board(context, write=write):
                return call(tool_input, context)

        return locked

    return decorate


def claim_next_task(context: Any, owner: str) -> dict[str, Any] | None:
    """Atomically claim a pending task whose dependencies have completed."""
    with task_board(context) as board:
        candidates = sorted(
            board.values(), key=lambda task: (task.get("owner") != owner, task["id"])
        )
        for task in candidates:
            if task.get("status") != "pending" or task.get("owner") not in (
                None,
                "",
                owner,
            ):
                continue
            if any(
                board.get(dep, {}).get("status") != "completed"
                for dep in task.get("blockedBy", [])
            ):
                continue
            before = dict(task)
            try:
                task.update(owner=owner, status="in_progress")
                if context.task_board_path is not None:
                    write_json_atomic(context.task_board_path, board)
            except BaseException:
                task.clear()
                task.update(before)
                raise
            return dict(task)
    return None


def release_tasks(context: Any, owner: str) -> None:
    """Return unfinished work to the board when its teammate exits."""
    with task_board(context, write=True) as board:
        for task in board.values():
            if task.get("owner") == owner and task.get("status") != "completed":
                task.update(owner=None, status="pending")

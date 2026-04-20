from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ManagedTask:
    task_id: str
    name: str
    started_at: float
    stop_event: threading.Event
    thread: threading.Thread


class TaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, ManagedTask] = {}

    def start(self, *, name: str, target: Callable[[threading.Event], None]) -> ManagedTask:
        task_id = str(uuid.uuid4())
        stop_event = threading.Event()

        def runner() -> None:
            try:
                target(stop_event)
            finally:
                with self._lock:
                    self._tasks.pop(task_id, None)

        thread = threading.Thread(target=runner, name=f"tool-task:{name}:{task_id}", daemon=True)
        task = ManagedTask(
            task_id=task_id,
            name=name,
            started_at=time.time(),
            stop_event=stop_event,
            thread=thread,
        )
        with self._lock:
            self._tasks[task_id] = task
        thread.start()
        return task

    def stop(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return False
        task.stop_event.set()
        return True

    def get(self, task_id: str) -> Optional[ManagedTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[ManagedTask]:
        with self._lock:
            return list(self._tasks.values())


"""Background tasks (the original's §9 backgroundable runs / Ctrl+B). A registry
of detached shell commands running concurrently in subprocesses — no conversation
race (each task is its own process). Surfaced via /bg and /tasks.
"""

from .tasks import BackgroundTasks, BgTask

__all__ = ["BackgroundTasks", "BgTask"]

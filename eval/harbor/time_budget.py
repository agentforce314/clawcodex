"""Deadline helpers shared by Harbor adapters.

Harbor owns the agent-phase timeout but does not pass it to ``Agent.run``.
The resolved inputs are nevertheless available beside the environment and
trial, so adapters can make the deadline visible to an otherwise unaware
agent without encoding dataset or task names.
"""

from __future__ import annotations

import json
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path


def resolve_agent_timeout_seconds(task_toml: Path, lock_json: Path) -> float | None:
    """Return Harbor's effective agent timeout from resolved trial inputs."""
    try:
        task = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        lock = json.loads(lock_json.read_text(encoding="utf-8"))
        base = float(task["agent"]["timeout_sec"])
        multiplier = lock.get("agent_timeout_multiplier")
        if multiplier is None:
            multiplier = lock.get("timeout_multiplier", 1.0)
        effective = base * float(multiplier)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return effective if effective > 0 else None


def build_deadline_prompt(
    timeout_seconds: float,
    *,
    started_at: float | None = None,
) -> str:
    """Build model-neutral guidance for a real external execution deadline."""
    start = time.time() if started_at is None else started_at
    deadline = start + timeout_seconds
    # Reserve 15%, bounded so short tasks still get two minutes and very long
    # tasks do not abandon productive work excessively early.
    reserve = min(10 * 60, max(2 * 60, timeout_seconds * 0.15))
    finalize_at = deadline - reserve

    def stamp(value: float) -> str:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(
            timespec="seconds"
        )

    return (
        "This run has a hard external execution deadline at "
        f"{stamp(deadline)} ({timeout_seconds / 60:.1f} minutes from start). "
        f"By {stamp(finalize_at)}, preserve the best valid deliverable, stop "
        "broad exploration, and switch to the narrowest checks needed for the "
        "explicit requirements. Do not start optional audits, repeated passing "
        "checks, or long refinements that cannot finish before the deadline. "
        "If the core result already works, finish and return control rather "
        "than consuming the remaining budget. You can use `date -u` to compare "
        "the current time with these timestamps."
    )


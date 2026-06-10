"""Tests for ``src.task_registry`` — Chunk B / WI-1.2.

Covers the typed ``RuntimeTaskRegistry`` plus the ``Task`` Protocol
dispatch (``register_task`` / ``get_all_tasks`` / ``get_task_by_type``).
The contract that the ``update`` mutator must be a sync function
(assumption A6 / concern C5) is exercised explicitly.
"""
from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from src.task_registry import (
    RuntimeTaskRegistry,
    Task,
    get_all_tasks,
    get_task_by_type,
)
from src.tasks_core import TaskStateBase


def _make_state(task_id: str = "b1", status: str = "running") -> TaskStateBase:
    return TaskStateBase(
        id=task_id,
        type="local_bash",
        status=status,  # type: ignore[arg-type]
        description="t",
        start_time=0.0,
        output_file="/tmp/x",
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_upsert_get_round_trip() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_state()
    reg.upsert(state)
    assert reg.get("b1") is state


def test_get_missing_returns_none() -> None:
    reg = RuntimeTaskRegistry()
    assert reg.get("nope") is None


def test_remove_returns_true_on_hit_false_on_miss() -> None:
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    assert reg.remove("b1") is True
    assert reg.remove("b1") is False
    assert reg.get("b1") is None


def test_all_returns_snapshot_not_view() -> None:
    """``all()`` must return a list snapshot — concurrent writers must not
    corrupt iteration."""
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    snap = reg.all()
    reg.upsert(_make_state("b2"))
    assert len(snap) == 1
    assert len(reg.all()) == 2


def test_by_type_filters_correctly() -> None:
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    reg.upsert(_make_state("b2"))
    other = TaskStateBase(
        id="a1", type="local_agent", status="running",
        description="t", start_time=0.0, output_file="",
    )
    reg.upsert(other)
    assert {t.id for t in reg.by_type("local_bash")} == {"b1", "b2"}
    assert {t.id for t in reg.by_type("local_agent")} == {"a1"}
    assert reg.by_type("dream") == []


def test_contains_and_len() -> None:
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    assert "b1" in reg
    assert "missing" not in reg
    assert len(reg) == 1


def test_iter_yields_snapshot() -> None:
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    reg.upsert(_make_state("b2"))
    ids = {t.id for t in reg}
    assert ids == {"b1", "b2"}


# ---------------------------------------------------------------------------
# update — atomic mutator semantics
# ---------------------------------------------------------------------------


def test_update_applies_mutator_atomically() -> None:
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1", status="running"))
    applied = reg.update("b1", lambda prev: replace(prev, status="completed"))
    assert applied is True
    assert reg.get("b1").status == "completed"


def test_update_returns_false_on_missing_id() -> None:
    reg = RuntimeTaskRegistry()
    applied = reg.update("missing", lambda prev: prev)
    assert applied is False


def test_update_rejects_async_def_mutator() -> None:
    """Hard contract per A6 / concern C5: mutator must be sync — never
    ``async def``. Yielding under the RLock would deadlock asyncio."""
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))

    async def bad_mutator(prev):  # noqa: ANN001  (test stub)
        return prev

    with pytest.raises(TypeError, match="must be a sync function"):
        reg.update("b1", bad_mutator)  # type: ignore[arg-type]


def test_update_rejects_mutator_that_returns_coroutine() -> None:
    """Belt-and-braces — a sync function that accidentally returns a
    coroutine (forgot to ``await``) is also rejected."""
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))

    async def helper():  # noqa: ANN001
        return 1

    def sync_but_returns_coroutine(prev):  # noqa: ANN001
        return helper()  # forgot to await

    with pytest.raises(TypeError, match="returned a coroutine"):
        reg.update("b1", sync_but_returns_coroutine)


def test_update_concurrent_writers_no_lost_writes() -> None:
    """Two threads each apply 100 increments to ``output_offset``; no
    writes are lost under the RLock."""
    reg = RuntimeTaskRegistry()
    reg.upsert(_make_state("b1"))
    increments_per_thread = 100

    def worker() -> None:
        for _ in range(increments_per_thread):
            reg.update("b1", lambda prev: replace(prev, output_offset=prev.output_offset + 1))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert reg.get("b1").output_offset == 2 * increments_per_thread


# ---------------------------------------------------------------------------
# Per-type task registration
# ---------------------------------------------------------------------------


def test_get_all_tasks_includes_local_bash_and_local_agent() -> None:
    """LocalShellTask and LocalAgentTask self-register at import time."""
    # Ensure the modules have been imported (idempotent).
    from src.tasks import local_agent, local_shell  # noqa: F401

    names = {t.name for t in get_all_tasks()}
    assert "LocalShellTask" in names
    assert "LocalAgentTask" in names


def test_get_task_by_type_dispatches_correctly() -> None:
    from src.tasks import local_agent, local_shell  # noqa: F401

    bash_task = get_task_by_type("local_bash")
    assert bash_task is not None
    assert bash_task.type == "local_bash"

    agent_task = get_task_by_type("local_agent")
    assert agent_task is not None
    assert agent_task.type == "local_agent"


def test_get_task_by_type_unknown_returns_none() -> None:
    """Out-of-scope chapter task types stay unregistered (per plan §3:
    RemoteAgent / Monitor / Dream are deferred). Chunk-F registered
    ``in_process_teammate``; the workflow-engine integration registered
    ``local_workflow`` — both are now implemented and no longer in this list.
    """
    # ``dream`` is out-of-scope (chapter §3 / plan §3 — deferred).
    assert get_task_by_type("dream") is None
    # And so are remote_agent / monitor_mcp.
    assert get_task_by_type("remote_agent") is None
    assert get_task_by_type("monitor_mcp") is None
    # ``local_workflow`` is now registered by the workflow-engine integration.
    assert get_task_by_type("local_workflow") is not None


def test_in_process_teammate_registered_post_chunk_f() -> None:
    """Chunk F / WI-6.2 + N1 fold-in: ``InProcessTeammateTask`` self-
    registers via ``tasks/__init__.py``."""
    impl = get_task_by_type("in_process_teammate")
    assert impl is not None
    assert impl.name == "InProcessTeammateTask"
    assert impl.type == "in_process_teammate"


def test_register_task_is_idempotent() -> None:
    """Re-registering the same Task implementation is a no-op."""
    from src.task_registry import register_task
    from src.tasks.local_shell import LocalShellTask

    before = len(get_all_tasks())
    register_task(LocalShellTask())
    register_task(LocalShellTask())
    after = len(get_all_tasks())
    assert after == before, "duplicate registrations should be ignored"

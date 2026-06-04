"""Tests for ``src.tasks_core`` — Chunk B / WI-1.1.

Covers TaskType / TaskStatus shape, ``is_terminal_task_status``, prefixed
ID generation, and ``TaskStateBase`` defaults.
"""
from __future__ import annotations

import re

import pytest

from src.tasks_core import (
    TaskStateBase,
    create_task_state_base,
    generate_task_id,
    is_terminal_task_status,
)


# ---------------------------------------------------------------------------
# is_terminal_task_status — covers all 5 chapter statuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "failed", "killed"])
def test_terminal_statuses(status: str) -> None:
    assert is_terminal_task_status(status) is True


@pytest.mark.parametrize("status", ["pending", "running"])
def test_non_terminal_statuses(status: str) -> None:
    assert is_terminal_task_status(status) is False


# ---------------------------------------------------------------------------
# generate_task_id — prefix, length, alphabet, randomness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_type,prefix",
    [
        ("local_bash", "b"),
        ("local_agent", "a"),
        ("remote_agent", "r"),
        ("in_process_teammate", "t"),
        ("local_workflow", "w"),
        ("monitor_mcp", "m"),
        ("dream", "d"),
    ],
)
def test_generate_task_id_prefix_per_type(task_type: str, prefix: str) -> None:
    """Each TaskType maps to its chapter-listed single-character prefix."""
    tid = generate_task_id(task_type)  # type: ignore[arg-type]
    assert tid.startswith(prefix)


def test_generate_task_id_length_is_nine() -> None:
    """``<prefix><8 body>`` = 9 chars total. Mirrors TS Task.ts."""
    for _ in range(50):
        tid = generate_task_id("local_bash")
        assert len(tid) == 9, f"got {tid!r}"


def test_generate_task_id_alphabet_is_lowercase_base36() -> None:
    """Body uses ``[0-9a-z]`` per TS Task.ts:96 — case-insensitive-safe
    against symlink path collisions on case-insensitive filesystems."""
    body_re = re.compile(r"^[a-z][0-9a-z]{8}$")
    for _ in range(50):
        tid = generate_task_id("local_agent")
        assert body_re.match(tid), f"unexpected shape: {tid!r}"


def test_generate_task_id_uses_secrets_choice() -> None:
    """Belt-and-braces: walk the AST and verify ``generate_task_id``
    calls ``secrets.choice`` (NOT ``random.choice``). Prevents accidental
    regressions to non-CSPRNG randomness — see WI-1.1's note that the
    chapter's symlink-attack rationale depends on a CSPRNG.

    AST walk rather than substring grep because the docstrings here
    legitimately mention ``random.choice`` as the wrong-thing-to-do.
    """
    import ast
    import inspect

    from src import tasks_core

    tree = ast.parse(inspect.getsource(tasks_core))
    found_secrets_choice = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # ``random.choice(...)`` — banned in executable code.
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "random"
            and func.attr == "choice"
        ):
            pytest.fail(
                "tasks_core.generate_task_id calls random.choice in "
                "executable code — must be secrets.choice (CSPRNG) per "
                "WI-1.1 rationale."
            )
        # ``secrets.choice(...)`` — required.
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "secrets"
            and func.attr == "choice"
        ):
            found_secrets_choice = True

    assert found_secrets_choice, "expected at least one secrets.choice() call"


def test_generate_task_id_yields_unique_ids() -> None:
    """36^8 ≈ 2.8T combinations — collisions across 1000 calls should
    be vanishingly unlikely. Sanity check, not a statistical proof."""
    ids = {generate_task_id("local_bash") for _ in range(1000)}
    assert len(ids) == 1000


# ---------------------------------------------------------------------------
# TaskStateBase defaults + factory
# ---------------------------------------------------------------------------


def test_task_state_base_defaults() -> None:
    state = TaskStateBase(
        id="t1",
        type="in_process_teammate",
        status="pending",
        description="x",
        start_time=0.0,
        output_file="/tmp/x",
    )
    assert state.output_offset == 0
    assert state.notified is False
    assert state.tool_use_id is None
    assert state.end_time is None
    assert state.total_paused_seconds == 0.0


def test_create_task_state_base_factory_fills_start_time() -> None:
    state = create_task_state_base(
        id="b9",
        type="local_bash",
        description="hello",
        output_file="/tmp/y",
    )
    assert state.status == "pending"
    assert state.start_time > 0


def test_task_state_base_is_kw_only() -> None:
    """Subclasses override ``type`` with a Literal default; that requires
    the base to use ``kw_only=True`` to avoid Python's "non-default
    argument follows default argument" rule. Verify by attempting to
    construct positionally and expecting TypeError."""
    with pytest.raises(TypeError):
        TaskStateBase("t1", "in_process_teammate", "pending", "x", 0.0, "/tmp/x")  # type: ignore[misc]

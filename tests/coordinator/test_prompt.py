"""WI-8.4 tests — coordinator system prompt port + behavioral acceptance.

Snapshot tests pin the byte-level shape of each `worker_capabilities`
branch independently. Behavioral string-contains tests prove the
chapter pillars are present (3 tools / 4 phases / "never delegate
understanding" / continue-vs-spawn / example session).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.coordinator.prompt import get_coordinator_system_prompt


SNAPSHOTS = Path(__file__).parent / "__snapshots__"


@pytest.fixture(autouse=True)
def _clear_simple_env():
    """Each test starts with the SIMPLE flag unset; restore on
    teardown. Manual save/restore (matching the test_mode.py fixture
    rationale) so any direct ``os.environ`` mutations inside the
    test don't leak."""
    import os as _os
    saved = _os.environ.pop("CLAUDE_CODE_SIMPLE", None)
    try:
        yield
    finally:
        _os.environ.pop("CLAUDE_CODE_SIMPLE", None)
        if saved is not None:
            _os.environ["CLAUDE_CODE_SIMPLE"] = saved


# ---------------------------------------------------------------------------
# Behavioral string-contains — chapter pillars
# ---------------------------------------------------------------------------


def test_prompt_mentions_all_three_coordinator_tools() -> None:
    p = get_coordinator_system_prompt()
    assert "Agent" in p
    assert "SendMessage" in p
    assert "TaskStop" in p


def test_prompt_includes_four_phase_workflow() -> None:
    p = get_coordinator_system_prompt()
    for phase in ("Research", "Synthesis", "Implementation", "Verification"):
        assert phase in p, f"missing phase: {phase!r}"


def test_prompt_warns_against_delegating_understanding() -> None:
    p = get_coordinator_system_prompt()
    assert "Never delegate understanding" in p or "never hand off understanding" in p


def test_prompt_contains_continue_vs_spawn_guidance() -> None:
    p = get_coordinator_system_prompt()
    # Loose match — table headers + the spawn-fresh phrase.
    assert "Continue" in p
    assert "Spawn fresh" in p


def test_prompt_contains_example_session() -> None:
    p = get_coordinator_system_prompt()
    assert "There's a null pointer in the auth module" in p
    assert "agent-a1b" in p


def test_prompt_mentions_task_notification_envelope() -> None:
    """The chapter calls out that workers report via ``<task-notification>``
    XML — the prompt teaches the model to recognize the envelope."""
    p = get_coordinator_system_prompt()
    assert "<task-notification>" in p
    assert "<task-id>" in p
    assert "<status>" in p


# ---------------------------------------------------------------------------
# Worker-capabilities branch
# ---------------------------------------------------------------------------


def test_default_branch_mentions_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """env unset → default branch — mentions Skill tool + skill
    invocations."""
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    p = get_coordinator_system_prompt()
    assert "project skills via the Skill tool" in p


def test_simple_branch_mentions_only_three_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env truthy → simple branch — mentions Bash, Read, Edit only."""
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    p = get_coordinator_system_prompt()
    assert "Workers have access to Bash, Read, and Edit tools" in p
    # Default-branch phrase should NOT appear.
    assert "project skills via the Skill tool" not in p


# ---------------------------------------------------------------------------
# Snapshot tests — pin byte-level shape per branch
# ---------------------------------------------------------------------------


def test_default_branch_matches_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """env unset → default branch; rendered prompt is byte-equal to
    the pinned ``coordinator_prompt.default.snap.txt``. Updating the
    snapshot is a deliberate review step."""
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    rendered = get_coordinator_system_prompt()
    snap = (SNAPSHOTS / "coordinator_prompt.default.snap.txt").read_text(encoding="utf-8")
    if rendered != snap:
        # Show a hint of where the diff is so failures are diagnose-able.
        for i, (a, b) in enumerate(zip(rendered, snap)):
            if a != b:
                ctx = max(0, i - 50)
                pytest.fail(
                    f"snapshot drift at offset {i}:\n"
                    f"  rendered: ...{rendered[ctx:i+50]!r}...\n"
                    f"  snapshot: ...{snap[ctx:i+50]!r}..."
                )
                break
        # Same prefix, different length:
        pytest.fail(
            f"snapshot drift: lengths differ ({len(rendered)} vs {len(snap)})"
        )


def test_simple_branch_matches_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    rendered = get_coordinator_system_prompt()
    snap = (SNAPSHOTS / "coordinator_prompt.simple.snap.txt").read_text(encoding="utf-8")
    if rendered != snap:
        for i, (a, b) in enumerate(zip(rendered, snap)):
            if a != b:
                ctx = max(0, i - 50)
                pytest.fail(
                    f"snapshot drift at offset {i}:\n"
                    f"  rendered: ...{rendered[ctx:i+50]!r}...\n"
                    f"  snapshot: ...{snap[ctx:i+50]!r}..."
                )
                break
        pytest.fail(
            f"snapshot drift: lengths differ ({len(rendered)} vs {len(snap)})"
        )


# ---------------------------------------------------------------------------
# WI-8.5 — WORKER agent definition
# ---------------------------------------------------------------------------


def test_worker_agent_type_is_worker() -> None:
    from src.coordinator.worker_agent import WORKER_AGENT
    assert WORKER_AGENT.agent_type == "worker"


def test_worker_inherits_general_purpose_tools() -> None:
    """Spread from GENERAL_PURPOSE_AGENT — same tools list (the
    coordinator-mode INTERNAL_WORKER_TOOLS filter happens at the
    tool-set construction level, not the agent definition)."""
    from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT
    from src.coordinator.worker_agent import WORKER_AGENT
    assert WORKER_AGENT.tools == GENERAL_PURPOSE_AGENT.tools


def test_worker_has_distinct_when_to_use() -> None:
    """When-to-use rephrased for coordinator-mode role."""
    from src.coordinator.worker_agent import WORKER_AGENT
    assert "Worker agent for coordinator mode" in WORKER_AGENT.when_to_use


def test_get_coordinator_agents_lists_worker_first() -> None:
    """``workerAgent.ts:16-18`` order: WORKER first so
    ``subagent_type: "worker"`` resolves to the right definition."""
    from src.coordinator.worker_agent import get_coordinator_agents
    agents = get_coordinator_agents()
    assert agents[0].agent_type == "worker"
    types = [a.agent_type for a in agents]
    assert "general-purpose" in types
    assert "Explore" in types or "explore" in types
    assert "Plan" in types or "plan" in types

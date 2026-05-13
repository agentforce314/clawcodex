"""WI-8.1 / 8.2 / 8.3 / 8.6 tests — coordinator-mode gates and filters.

Covers:
* ``is_coordinator_mode`` env-var gating + ``match_session_mode`` flip.
* ``INTERNAL_WORKER_TOOLS`` set shape + filter behavior.
* ``filter_coordinator_tools`` returns exactly 3 tools.
* ``filter_worker_tools`` excludes ``INTERNAL_WORKER_TOOLS``, keeps
  everything else (incl. MCP).
* ``get_coordinator_user_context`` activation gate + content shape.
* ``is_fork_subagent_enabled`` is False under coordinator mode (WI-8.6).
* Round-2: ``get_coordinator_user_context`` honors ``CLAUDE_CODE_SIMPLE``
  branch and renders ``ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS``
  sorted (parity with ``coordinatorMode.ts:88-95``).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.coordinator.mode import (
    INTERNAL_WORKER_TOOLS,
    filter_coordinator_tools,
    filter_worker_tools,
    get_coordinator_user_context,
    is_coordinator_mode,
    match_session_mode,
)


@pytest.fixture(autouse=True)
def _clear_env_vars():
    """Each test starts with both env vars unset, AND any direct
    ``os.environ`` mutations inside the test (e.g.,
    ``match_session_mode`` writes ``CLAUDE_CODE_COORDINATOR_MODE``
    directly, outside monkeypatch's tracking) are reverted on
    teardown. Using manual save/restore rather than monkeypatch so
    direct env-var writes don't leak between tests."""
    saved = {}
    for var in ("CLAUDE_CODE_COORDINATOR_MODE", "CLAUDE_CODE_SIMPLE"):
        saved[var] = os.environ.pop(var, None)
    try:
        yield
    finally:
        for var, prev in saved.items():
            os.environ.pop(var, None)
            if prev is not None:
                os.environ[var] = prev


# ---------------------------------------------------------------------------
# WI-8.1 — is_coordinator_mode + match_session_mode
# ---------------------------------------------------------------------------


def test_is_coordinator_mode_false_when_env_unset() -> None:
    assert is_coordinator_mode() is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on"])
def test_is_coordinator_mode_true_for_truthy_env(
    truthy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", truthy)
    assert is_coordinator_mode() is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "  "])
def test_is_coordinator_mode_false_for_falsy_env(
    falsy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", falsy)
    assert is_coordinator_mode() is False


def test_match_session_mode_no_op_when_session_mode_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessions stored before mode tracking existed pass None."""
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    assert match_session_mode(None) is None


def test_match_session_mode_no_op_when_already_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    assert match_session_mode("coordinator") is None
    assert is_coordinator_mode() is True


def test_match_session_mode_enters_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    banner = match_session_mode("coordinator")
    assert banner is not None
    assert "Entered coordinator mode" in banner
    assert is_coordinator_mode() is True


def test_match_session_mode_exits_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    banner = match_session_mode("normal")
    assert banner is not None
    assert "Exited coordinator mode" in banner
    assert is_coordinator_mode() is False


# ---------------------------------------------------------------------------
# WI-8.2 — INTERNAL_WORKER_TOOLS + tool-set filters
# ---------------------------------------------------------------------------


def test_internal_worker_tools_contains_chapter_listed_names() -> None:
    """Per ``coordinatorMode.ts:29-34`` and the chapter §"Tool Restrictions"."""
    assert INTERNAL_WORKER_TOOLS == frozenset({
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "StructuredOutput",
    })


class _StubTool:
    """Minimal Tool-shaped stub for filter tests."""
    def __init__(self, name: str) -> None:
        self.name = name


def test_filter_coordinator_tools_returns_exactly_three() -> None:
    """The chapter calls this out as core: the coordinator gets
    EXACTLY {Agent, SendMessage, TaskStop} — no Read, no Edit, no
    Bash."""
    tools = [
        _StubTool("Agent"), _StubTool("SendMessage"), _StubTool("TaskStop"),
        _StubTool("Read"), _StubTool("Edit"), _StubTool("Bash"),
        _StubTool("WebSearch"), _StubTool("Grep"),
    ]
    coord = filter_coordinator_tools(tools)
    assert {t.name for t in coord} == {"Agent", "SendMessage", "TaskStop"}
    assert len(coord) == 3


def test_filter_worker_tools_excludes_internal_set() -> None:
    """Workers get standard tools (Read, Bash, etc.) but lose the four
    coordination tools that only the coordinator uses."""
    tools = [
        _StubTool("Read"), _StubTool("Bash"), _StubTool("Edit"),
        _StubTool("TeamCreate"), _StubTool("TeamDelete"),
        _StubTool("SendMessage"), _StubTool("StructuredOutput"),
        _StubTool("Agent"), _StubTool("TaskStop"),
        _StubTool("Grep"), _StubTool("WebSearch"), _StubTool("Skill"),
    ]
    worker = filter_worker_tools(tools)
    worker_names = {t.name for t in worker}
    assert worker_names.isdisjoint(INTERNAL_WORKER_TOOLS)
    # And Agent / TaskStop / standard tools survive (Agent is for
    # delegation; chapter notes workers don't spawn sub-teams via
    # TeamCreate but Agent itself isn't on the internal-worker list).
    assert {"Read", "Bash", "Edit", "Grep", "WebSearch", "Skill", "Agent"} <= worker_names


def test_filter_worker_tools_preserves_mcp_tools() -> None:
    """MCP-server tools aren't on ``INTERNAL_WORKER_TOOLS``; they
    pass through to workers (chapter §"Worker Context")."""
    tools = [
        _StubTool("Read"), _StubTool("mcp__github__create_pr"),
        _StubTool("mcp__sentry__search_issues"),
    ]
    worker = filter_worker_tools(tools)
    names = {t.name for t in worker}
    assert "mcp__github__create_pr" in names
    assert "mcp__sentry__search_issues" in names


# ---------------------------------------------------------------------------
# WI-8.3 — get_coordinator_user_context
# ---------------------------------------------------------------------------


def test_user_context_empty_when_not_in_coordinator_mode() -> None:
    """Non-coordinator agents shouldn't see the worker-tools block."""
    assert get_coordinator_user_context() == {}


def test_user_context_returns_worker_tools_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    ctx = get_coordinator_user_context()
    assert "workerToolsContext" in ctx
    assert "Workers spawned via Agent" in ctx["workerToolsContext"]


def test_user_context_includes_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")

    class _MCPClient:
        def __init__(self, name: str) -> None:
            self.name = name

    ctx = get_coordinator_user_context([_MCPClient("github"), _MCPClient("sentry")])
    assert "github" in ctx["workerToolsContext"]
    assert "sentry" in ctx["workerToolsContext"]


def test_user_context_omits_mcp_section_when_no_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    ctx = get_coordinator_user_context()
    assert "MCP servers" not in ctx["workerToolsContext"]


def test_user_context_includes_scratchpad_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    ctx = get_coordinator_user_context(scratchpad_dir="/tmp/scratch")
    assert "/tmp/scratch" in ctx["workerToolsContext"]
    assert "Scratchpad" in ctx["workerToolsContext"]


def test_user_context_worker_tools_matches_async_allowed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1 from Chunk-G critic — pin the hardcoded worker-tools string
    in ``get_coordinator_user_context`` against
    ``ASYNC_AGENT_ALLOWED_TOOLS`` so future drift surfaces at test
    time. Per Chunk-G deviation #1, the list is hardcoded (registry-
    construction order makes a live read awkward); this guard ensures
    it stays in sync."""
    from src.agent.constants import ASYNC_AGENT_ALLOWED_TOOLS

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    ctx = get_coordinator_user_context()
    body = ctx["workerToolsContext"]
    for tool_name in ASYNC_AGENT_ALLOWED_TOOLS:
        # Skip ``StructuredOutput`` — it's in ASYNC_AGENT_ALLOWED_TOOLS
        # for non-coordinator paths but coordinator mode adds it to
        # INTERNAL_WORKER_TOOLS so workers don't see it.
        if tool_name == "StructuredOutput":
            continue
        assert tool_name in body, (
            f"Coordinator user context lost {tool_name!r} from "
            f"ASYNC_AGENT_ALLOWED_TOOLS — update prompt.py / mode.py "
            f"to keep them in sync."
        )


# ---------------------------------------------------------------------------
# Round-2 — CLAUDE_CODE_SIMPLE branch + sort-order parity with TS
# (``coordinatorMode.ts:88-95``)
# ---------------------------------------------------------------------------


def test_user_context_simple_branch_three_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIMPLE branch returns exactly ``Bash, Edit, Read`` (sorted)
    — the literal three-tool list from TS ``coordinatorMode.ts:88-91``.
    Larger-list tools must NOT appear in SIMPLE mode."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    ctx = get_coordinator_user_context()
    body = ctx["workerToolsContext"]
    assert "Bash, Edit, Read" in body
    # Tools that exist in ASYNC_AGENT_ALLOWED_TOOLS but not SIMPLE must
    # NOT leak through.
    assert "WebSearch" not in body
    assert "TodoWrite" not in body
    assert "Glob" not in body


def test_user_context_default_branch_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default branch must render tools alphabetically sorted —
    matches TS ``.sort().join(', ')`` at ``coordinatorMode.ts:94``."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    ctx = get_coordinator_user_context()
    body = ctx["workerToolsContext"]
    line = next(l for l in body.splitlines() if "Workers spawned" in l)
    tools_part = line.split(":", 1)[1].strip()
    tools = [t.strip() for t in tools_part.split(",")]
    assert tools == sorted(tools), (
        f"Worker tools must be alphabetically sorted; got: {tools}"
    )


def test_user_context_default_branch_matches_async_allowed_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inverse of the existing N1 guard
    (``test_user_context_worker_tools_matches_async_allowed_tools``).

    N1 checks every ASYNC tool appears in the rendered context. This
    test checks the rendered context contains EXACTLY
    ``ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS`` — catching
    both drift directions:

    * Tool added to ASYNC_AGENT_ALLOWED_TOOLS but not coordinator → N1
      catches it.
    * Tool added to coordinator user context but not ASYNC list →
      this test catches it.
    """
    from src.agent.constants import ASYNC_AGENT_ALLOWED_TOOLS

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    ctx = get_coordinator_user_context()
    body = ctx["workerToolsContext"]
    line = next(l for l in body.splitlines() if "Workers spawned" in l)
    rendered = set(t.strip() for t in line.split(":", 1)[1].strip().split(","))
    expected = ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS
    assert rendered == expected, (
        f"Worker tools drift detected.\n"
        f"  Rendered: {sorted(rendered)}\n"
        f"  Expected: {sorted(expected)}\n"
        f"  Missing : {sorted(expected - rendered)}\n"
        f"  Extra   : {sorted(rendered - expected)}"
    )


def test_user_context_simple_off_returns_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: flipping ``CLAUDE_CODE_SIMPLE`` off reverts to the full
    async-allowed list — confirms the branch is live, not stuck."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    ctx_simple = get_coordinator_user_context()
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    ctx_default = get_coordinator_user_context()
    assert ctx_simple != ctx_default
    assert "WebSearch" not in ctx_simple["workerToolsContext"]
    assert "WebSearch" in ctx_default["workerToolsContext"]


def test_user_context_simple_snapshot(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    """Byte-exact snapshot pin for SIMPLE branch. Breaking this test
    is the deliberate review gate for any change to the SIMPLE-mode
    worker tools list — same pattern the prompt.py snapshots use."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    body = get_coordinator_user_context()["workerToolsContext"]
    snap_path = (
        Path(request.path).parent / "__snapshots__" / "user_context_simple.snap.txt"
    )
    expected = snap_path.read_text().rstrip("\n")
    assert body == expected


def test_user_context_default_snapshot(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    """Byte-exact snapshot pin for the default branch. Updates require
    a deliberate snapshot refresh (mirrors the prompt.py snapshot
    discipline at ``test_prompt.py``)."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    body = get_coordinator_user_context()["workerToolsContext"]
    snap_path = (
        Path(request.path).parent / "__snapshots__" / "user_context_default.snap.txt"
    )
    expected = snap_path.read_text().rstrip("\n")
    assert body == expected


def test_user_context_simple_with_mcp_and_scratchpad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIMPLE branch composes correctly with MCP servers + scratchpad —
    confirms the SIMPLE branch only swaps the tools list and leaves
    the rest of the structure intact."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")

    class _MCPClient:
        def __init__(self, name: str) -> None:
            self.name = name

    ctx = get_coordinator_user_context(
        [_MCPClient("github")], scratchpad_dir="/tmp/scratch"
    )
    body = ctx["workerToolsContext"]
    assert "Bash, Edit, Read" in body
    assert "WebSearch" not in body  # SIMPLE doesn't leak default tools
    assert "github" in body
    assert "/tmp/scratch" in body


# ---------------------------------------------------------------------------
# WI-8.6 — fork mutex
# ---------------------------------------------------------------------------


def test_fork_subagent_disabled_under_coordinator_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per chapter §"Mutual Exclusion with Fork": fork-subagent and
    coordinator-worker are mutually exclusive philosophies of
    delegation; enforced at the gate level."""
    from src.agent.fork_subagent import is_fork_subagent_enabled

    # Both flags would normally enable fork...
    monkeypatch.setenv("CLAUDE_FORK_SUBAGENT", "1")
    # ...but coordinator mode trumps.
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    assert is_fork_subagent_enabled() is False


def test_fork_subagent_enabled_when_coordinator_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: with CLAUDE_FORK_SUBAGENT=1 and coordinator mode OFF,
    fork is enabled (in interactive mode). Confirms the mutex is
    additive, not an unconditional disable."""
    from src.agent.fork_subagent import is_fork_subagent_enabled

    monkeypatch.setenv("CLAUDE_FORK_SUBAGENT", "1")
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    # Interactive flag — patch the helper.
    from unittest.mock import patch
    with patch(
        "src.agent.fork_subagent.get_is_non_interactive_session",
        return_value=False,
    ):
        assert is_fork_subagent_enabled() is True

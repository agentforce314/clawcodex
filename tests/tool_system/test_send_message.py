"""WI-7.1 + WI-7.2 + WI-7.3 tests — SendMessage tool routing + protocols.

Covers:
* Tool registration + schema shape.
* 4-branch routing dispatch order parity (WI-7.2).
* In-process queue (running) + auto-resume (terminal) — WI-7.4
  integration smoke.
* Mailbox routing (named recipient + ``*`` broadcast).
* Structured protocols + sender-side authorization.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.swarm.mailbox import get_inbox_path, read_mailbox
from src.services.swarm.team_file import TeamFile, TeamMember, write_team_file
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.tools.send_message import (
    SEND_MESSAGE_INPUT_SCHEMA,
    SEND_MESSAGE_TOOL_NAME,
    SendMessageTool,
)


def _call_send_message(input: dict, ctx: ToolContext):
    """Sync wrapper for the async tool — mirrors test_task_stop helper."""
    return asyncio.run(SendMessageTool.call(input, ctx))


# ---------------------------------------------------------------------------
# WI-7.1 — Tool registration + schema
# ---------------------------------------------------------------------------


def test_tool_registered_with_canonical_name() -> None:
    assert SendMessageTool.name == "SendMessage"
    assert SEND_MESSAGE_TOOL_NAME == "SendMessage"


def test_tool_in_default_static_tools() -> None:
    from src.tool_system.tools import ALL_STATIC_TOOLS
    assert SendMessageTool in ALL_STATIC_TOOLS


def test_input_schema_required_fields() -> None:
    assert "to" in SEND_MESSAGE_INPUT_SCHEMA["required"]
    assert "message" in SEND_MESSAGE_INPUT_SCHEMA["required"]
    # ``summary`` is optional (required for plain text via runtime
    # check; not at the schema level).
    assert "summary" not in SEND_MESSAGE_INPUT_SCHEMA["required"]


def test_input_schema_message_is_union() -> None:
    msg_schema = SEND_MESSAGE_INPUT_SCHEMA["properties"]["message"]
    assert "oneOf" in msg_schema
    type_options = {opt.get("type") for opt in msg_schema["oneOf"]}
    assert type_options == {"string", "object"}


# ---------------------------------------------------------------------------
# WI-7.2 — 4-branch routing dispatch order parity (the critical guard)
# ---------------------------------------------------------------------------


def test_dispatch_order_parity_with_ts(tmp_path: Path) -> None:
    """Pin the dispatch chain order so a future re-arrangement that
    silently breaks UDS_INBOX integration shows up at review time.

    TS dispatch order (mirrored): bridge → uds → in-process → mailbox
    → error. Per critic Chunk-F N2 — verify by walking the AST and
    matching ``If`` / ``Return`` nodes by line position rather than
    string searching. A comment containing the search text would have
    confused the substring approach; the AST walk is sturdier.
    """
    import ast

    from src.tool_system.tools import send_message as sm

    tree = ast.parse(inspect.getsource(sm._send_message_call))

    bridge_line = None
    uds_line = None
    in_process_line = None
    mailbox_line = None
    error_line = None

    for node in ast.walk(tree):
        # Bridge / uds branches: ``if addr.scheme == "bridge":`` /
        # ``"uds":``. We match an If whose test compares
        # ``addr.scheme`` to a constant.
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Attribute)
                and getattr(test.left.value, "id", None) == "addr"
                and test.left.attr == "scheme"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
            ):
                scheme = test.comparators[0].value
                if scheme == "bridge" and bridge_line is None:
                    bridge_line = node.lineno
                elif scheme == "uds" and uds_line is None:
                    uds_line = node.lineno
        # Calls to ``_route_in_process(...)`` / ``_route_team_mailbox(...)``
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_route_in_process" and in_process_line is None:
                in_process_line = node.lineno
            elif node.func.id == "_route_team_mailbox" and mailbox_line is None:
                mailbox_line = node.lineno
        # The error-branch return — match the literal "Recipient" text
        # but only on a Return node so a docstring / comment wouldn't
        # match.
        if isinstance(node, ast.Return) and node.value is not None:
            for child in ast.walk(node.value):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value.startswith("Recipient")
                ):
                    if error_line is None:
                        error_line = node.lineno
                    break

    assert bridge_line is not None, "bridge branch not found in AST"
    assert uds_line is not None, "uds branch not found in AST"
    assert in_process_line is not None, "in-process call not found in AST"
    assert mailbox_line is not None, "mailbox call not found in AST"
    assert error_line is not None, "error branch not found in AST"
    assert bridge_line < uds_line < in_process_line < mailbox_line < error_line, (
        f"dispatch order regression: expected bridge < uds < in-process "
        f"< mailbox < error; got bridge={bridge_line} uds={uds_line} "
        f"in_process={in_process_line} mailbox={mailbox_line} error={error_line}"
    )


def test_bridge_addressing_raises_not_implemented(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(NotImplementedError, match="bridge:"):
        _call_send_message(
            {"to": "bridge:my-session", "message": "hi", "summary": "x"},
            ctx,
        )


def test_uds_addressing_raises_not_implemented(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(NotImplementedError, match="uds:"):
        _call_send_message(
            {"to": "uds:/tmp/sock", "message": "hi", "summary": "x"},
            ctx,
        )


def test_uds_error_message_names_feature_gate(tmp_path: Path) -> None:
    """Error message names ``feature('UDS_INBOX')`` so the model and
    a future implementer know exactly what's gating the path."""
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(NotImplementedError) as excinfo:
        _call_send_message(
            {"to": "uds:/tmp/sock", "message": "hi", "summary": "x"}, ctx
        )
    assert "UDS_INBOX" in str(excinfo.value)


# ---------------------------------------------------------------------------
# In-process routing — running agent → queue
# ---------------------------------------------------------------------------


def test_message_to_running_agent_queued(tmp_path: Path) -> None:
    """Running ``local_agent`` → message goes onto pending_messages."""
    from src.tasks.local_agent import register_async_agent

    ctx = ToolContext(workspace_root=tmp_path)
    state = register_async_agent(
        agent_id="a-running", description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    ctx.agent_name_registry._mapping["researcher"] = state.id

    result = _call_send_message(
        {"to": "researcher", "message": "follow-up", "summary": "more"},
        ctx,
    )
    assert result.is_error is False
    assert "queued" in result.output["message"].lower()
    refreshed = ctx.runtime_tasks.get(state.id)
    assert "follow-up" in refreshed.pending_messages


def test_message_to_running_agent_by_raw_id(tmp_path: Path) -> None:
    """Raw agent_id fallback (model passes the id directly)."""
    from src.tasks.local_agent import register_async_agent

    ctx = ToolContext(workspace_root=tmp_path)
    state = register_async_agent(
        agent_id="a-raw", description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )

    result = _call_send_message(
        {"to": "a-raw", "message": "by-id", "summary": "x"},
        ctx,
    )
    assert result.is_error is False
    refreshed = ctx.runtime_tasks.get(state.id)
    assert "by-id" in refreshed.pending_messages


# ---------------------------------------------------------------------------
# WI-7.4 — auto-resume race guard
# ---------------------------------------------------------------------------


def test_message_to_terminal_agent_reports_honestly(tmp_path: Path) -> None:
    """ch10 round-4 (critic M1) — SendMessage to a TERMINAL agent no longer
    returns a false 'resumed it in the background' success (the live-resume
    lifecycle is a documented stub that never spawns the loop). It returns
    an error telling the model the message will NOT be processed and to
    spawn a fresh agent. The resume_agent_background call still re-registers
    the state (running + prompt), but the tool is honest about the outcome."""
    from src.tasks.local_agent import (
        complete_agent_task,
        register_async_agent,
    )

    ctx = ToolContext(workspace_root=tmp_path)
    state = register_async_agent(
        agent_id="a-dead", description="x", prompt="initial",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    complete_agent_task(state.id, result_text="done", registry=ctx.runtime_tasks)

    result = _call_send_message(
        {"to": "a-dead", "message": "wake up", "summary": "wakeup"},
        ctx,
    )
    assert result.is_error is True
    msg = result.output["message"].lower()
    assert "not yet supported" in msg or "fresh agent" in msg
    assert "resumed it in the background" not in msg


@pytest.mark.asyncio
async def test_concurrent_resume_race_only_one_winner(tmp_path: Path) -> None:
    """Two concurrent SendMessage calls to the same dead agent_id;
    only one resumes, the other queues."""
    from src.tasks.local_agent import (
        complete_agent_task,
        register_async_agent,
    )

    ctx = ToolContext(workspace_root=tmp_path)
    register_async_agent(
        agent_id="a-race", description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    complete_agent_task("a-race", result_text="done", registry=ctx.runtime_tasks)

    # Race two calls via asyncio.gather. The atomic claim mutator
    # ensures exactly one wins; the other should queue.
    results = await asyncio.gather(
        SendMessageTool.call(
            {"to": "a-race", "message": "msg-A", "summary": "x"}, ctx,
        ),
        SendMessageTool.call(
            {"to": "a-race", "message": "msg-B", "summary": "x"}, ctx,
        ),
    )

    # ch10 round-4 (critic M1) — the atomic claim still yields exactly one
    # winner + one loser, but the winner now reports the honest "not yet
    # supported" error (was a false "resumed" success) while the loser
    # queues onto the re-registered running state.
    error_count = sum(1 for r in results if r.is_error)
    queued_count = sum(
        1 for r in results if not r.is_error
        and "queued" in r.output["message"].lower()
    )
    assert error_count == 1, f"expected exactly 1 honest-error winner: {results}"
    assert queued_count == 1, f"expected exactly 1 queued loser: {results}"

    # The fresh state still has the resume prompt + the queued message in
    # pending_messages (resume_agent_background's re-registration is
    # unchanged; only the tool's message is honest).
    final = ctx.runtime_tasks.get("a-race")
    assert final.status == "running"
    assert final.prompt in {"msg-A", "msg-B"}
    other_msg = "msg-B" if final.prompt == "msg-A" else "msg-A"
    assert other_msg in final.pending_messages


# ---------------------------------------------------------------------------
# Mailbox routing — named recipient + broadcast
# ---------------------------------------------------------------------------


def _seed_team(tmp_path: Path, members: list[TeamMember] | None = None) -> ToolContext:
    """Helper: write a team file + populate ``ctx.team`` so mailbox
    routing has something to work with."""
    team = TeamFile(
        team_name="t",
        lead_agent_id="lead-1",
        description="test",
        members=tuple(members or []),
    )
    write_team_file(team, tmp_path)
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = {
        "team_name": "t", "lead_agent_id": "lead-1",
        "sender_name": "team-lead",
    }
    return ctx


def test_named_recipient_writes_to_mailbox(tmp_path: Path) -> None:
    ctx = _seed_team(
        tmp_path,
        members=[TeamMember(agent_id="r1", name="researcher")],
    )
    result = _call_send_message(
        {"to": "researcher", "message": "go investigate", "summary": "task"},
        ctx,
    )
    assert result.is_error is False
    msgs = read_mailbox(
        "researcher", team_name="t", workspace_root=tmp_path,
    )
    assert len(msgs) == 1
    assert msgs[0].text == "go investigate"
    assert msgs[0].from_ == "team-lead"


def test_broadcast_writes_to_every_member_except_sender(tmp_path: Path) -> None:
    ctx = _seed_team(
        tmp_path,
        members=[
            TeamMember(agent_id="r1", name="alice"),
            TeamMember(agent_id="r2", name="bob"),
            TeamMember(agent_id="r3", name="team-lead"),  # sender
        ],
    )
    result = _call_send_message(
        {"to": "*", "message": "all hands", "summary": "broadcast"},
        ctx,
    )
    assert result.is_error is False
    assert sorted(result.output["recipients"]) == ["alice", "bob"]

    # Each recipient's mailbox got the message; team-lead did not.
    assert len(read_mailbox("alice", team_name="t", workspace_root=tmp_path)) == 1
    assert len(read_mailbox("bob", team_name="t", workspace_root=tmp_path)) == 1
    assert read_mailbox(
        "team-lead", team_name="t", workspace_root=tmp_path,
    ) == []


def test_unknown_recipient_returns_error(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    # No team context, no in-process agent — branch 5 (error).
    result = _call_send_message(
        {"to": "ghost", "message": "knock knock", "summary": "x"},
        ctx,
    )
    assert result.is_error is True
    assert "not found" in result.output["message"].lower()


def test_recipient_not_on_roster_rejected(tmp_path: Path) -> None:
    """If the team has members but the recipient isn't on the roster,
    reject (defense-in-depth — model could pass an arbitrary string)."""
    ctx = _seed_team(
        tmp_path,
        members=[TeamMember(agent_id="r1", name="alice")],
    )
    result = _call_send_message(
        {"to": "stranger", "message": "x", "summary": "x"}, ctx
    )
    assert result.is_error is True
    assert "not on team" in result.output["message"]


# ---------------------------------------------------------------------------
# Validation — input shape rejection
# ---------------------------------------------------------------------------


def test_missing_to_raises(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(ToolInputError):
        _call_send_message({"message": "x", "summary": "x"}, ctx)


def test_empty_to_raises(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(ToolInputError):
        _call_send_message({"to": "   ", "message": "x", "summary": "x"}, ctx)


def test_at_in_to_raises(tmp_path: Path) -> None:
    """TS rejects '@' in the ``to:`` field (one team per session)."""
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(ToolInputError, match="@"):
        _call_send_message(
            {"to": "alice@team", "message": "x", "summary": "x"}, ctx
        )


def test_missing_message_raises(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(ToolInputError):
        _call_send_message({"to": "x", "summary": "x"}, ctx)


def test_plain_text_message_requires_summary(tmp_path: Path) -> None:
    """Per TS: plain-text messages need a summary for UI preview."""
    ctx = _seed_team(tmp_path, members=[TeamMember(agent_id="r1", name="alice")])
    with pytest.raises(ToolInputError, match="summary"):
        _call_send_message({"to": "alice", "message": "no summary"}, ctx)


# ---------------------------------------------------------------------------
# WI-7.3 — Structured protocols
# ---------------------------------------------------------------------------


def test_shutdown_request_writes_envelope_to_mailbox(tmp_path: Path) -> None:
    ctx = _seed_team(
        tmp_path,
        members=[TeamMember(agent_id="r1", name="researcher")],
    )
    result = _call_send_message(
        {
            "to": "researcher",
            "message": {
                "type": "shutdown_request",
                "request_id": "req-1",
                "reason": "end of session",
            },
        },
        ctx,
    )
    assert result.is_error is False

    msgs = read_mailbox(
        "researcher", team_name="t", workspace_root=tmp_path,
    )
    assert len(msgs) == 1
    envelope = json.loads(msgs[0].text)
    assert envelope["type"] == "shutdown_request"
    assert envelope["request_id"] == "req-1"
    assert envelope["reason"] == "end of session"
    assert envelope["from"] == "team-lead"


def test_shutdown_response_approve(tmp_path: Path) -> None:
    ctx = _seed_team(tmp_path, members=[TeamMember(agent_id="lead", name="team-lead")])
    result = _call_send_message(
        {
            "to": "team-lead",
            "message": {
                "type": "shutdown_response",
                "request_id": "req-1",
                "approve": True,
            },
        },
        ctx,
    )
    assert result.is_error is False
    msgs = read_mailbox("team-lead", team_name="t", workspace_root=tmp_path)
    envelope = json.loads(msgs[0].text)
    assert envelope["type"] == "shutdown_response"
    assert envelope["approved"] is True


def test_shutdown_response_reject_requires_reason(tmp_path: Path) -> None:
    ctx = _seed_team(tmp_path, members=[TeamMember(agent_id="lead", name="team-lead")])
    with pytest.raises(ToolInputError, match="reason"):
        _call_send_message(
            {
                "to": "team-lead",
                "message": {
                    "type": "shutdown_response",
                    "request_id": "req-1",
                    "approve": False,
                },
            },
            ctx,
        )


def test_plan_approval_only_team_lead_can_send(tmp_path: Path) -> None:
    """Sender-side authorization: ``is_team_lead`` gates plan approvals."""
    ctx = _seed_team(
        tmp_path, members=[TeamMember(agent_id="r1", name="researcher")],
    )
    # Active agent is NOT the team lead.
    ctx.agent_id = "r1"
    with pytest.raises(ToolInputError, match="team lead"):
        _call_send_message(
            {
                "to": "researcher",
                "message": {
                    "type": "plan_approval_response",
                    "request_id": "req-1",
                    "approve": True,
                    "permission_mode": "default",
                },
            },
            ctx,
        )


def test_plan_approval_succeeds_when_sender_is_lead(tmp_path: Path) -> None:
    ctx = _seed_team(
        tmp_path, members=[TeamMember(agent_id="r1", name="researcher")],
    )
    # Lead identifies as the team lead.
    ctx.agent_id = "lead-1"
    result = _call_send_message(
        {
            "to": "researcher",
            "message": {
                "type": "plan_approval_response",
                "request_id": "req-1",
                "approve": True,
                "permission_mode": "default",
            },
        },
        ctx,
    )
    assert result.is_error is False
    msgs = read_mailbox(
        "researcher", team_name="t", workspace_root=tmp_path,
    )
    envelope = json.loads(msgs[0].text)
    assert envelope["type"] == "plan_approval_response"
    assert envelope["approved"] is True
    assert envelope["from"] == "team-lead"


def test_unknown_structured_type_raises(tmp_path: Path) -> None:
    ctx = _seed_team(tmp_path, members=[TeamMember(agent_id="r1", name="alice")])
    with pytest.raises(ToolInputError, match="Unknown structured-protocol"):
        _call_send_message(
            {
                "to": "alice",
                "message": {"type": "this_is_not_a_protocol"},
            },
            ctx,
        )

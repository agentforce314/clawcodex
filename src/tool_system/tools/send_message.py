"""SendMessage tool — Chunk F / WI-7.1 + WI-7.2 + WI-7.3.

Mirrors ``typescript/src/tools/SendMessageTool/SendMessageTool.ts``.
The universal communication primitive for inter-agent messaging:
plain text from leader → teammate, structured protocol envelopes
(shutdown, plan-approval), and broadcasts.

Active teams resolve roster names in their own namespace. Other plain-text
recipients resolve through the session's local-worker registry, including real
continuations of completed workers. TeamRuntime validates and records control
messages before its single mailbox consumer applies them; arbitrary JSON in a
plain message cannot change runtime state. Legacy mailbox helpers remain for
callers that manage their own transport. Bridge and UDS addressing are explicit
unsupported operations in this build.
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.swarm.mailbox import (
    TeammateMessage,
    create_plan_approval_response_message,
    create_shutdown_approved_message,
    create_shutdown_rejected_message,
    create_shutdown_request_message,
    make_iso_timestamp,
    write_to_mailbox,
)
from src.services.swarm.team_file import TeamFile, find_member_by_name, read_team_file
from src.services.swarm.team_membership import is_team_lead
from src.utils.peer_address import parse_address

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

logger = logging.getLogger(__name__)


SEND_MESSAGE_TOOL_NAME = "SendMessage"


SEND_MESSAGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["to", "message"],
    "properties": {
        "to": {
            "type": "string",
            "description": (
                "Recipient: a teammate name, '*' for broadcast, "
                "'bridge:<session-id>' for a Remote Control peer (out of "
                "scope in this build), or 'uds:<socket-path>' for a "
                "local UDS peer (out of scope in this build)."
            ),
        },
        "summary": {
            "type": "string",
            "description": (
                "5-10 word UI preview shown in chat. Required for plain "
                "string messages; ignored for structured protocols."
            ),
        },
        "message": {
            "description": (
                "Plain text or one of the structured protocols: "
                "shutdown_request / shutdown_response / "
                "plan_approval_response. Structured protocols use a "
                "discriminated union via the 'type' field."
            ),
            # JSON-schema oneOf: a string, OR an object with a 'type'
            # discriminator. We don't enforce the union shape strictly
            # at the schema level (Python's json-schema validator
            # would complain about other fields); validation happens
            # in ``_send_message_call`` against the runtime shape.
            "oneOf": [
                {"type": "string"},
                {"type": "object", "required": ["type"]},
            ],
        },
    },
}


# Structured-protocol type discriminators we accept.
_VALID_STRUCTURED_TYPES = {
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}


def _ok(message: str, **extra: Any) -> ToolResult:
    out: dict[str, Any] = {"success": True, "message": message}
    out.update(extra)
    return ToolResult(name=SEND_MESSAGE_TOOL_NAME, output=out)


def _err(message: str, **extra: Any) -> ToolResult:
    out: dict[str, Any] = {"success": False, "message": message}
    out.update(extra)
    return ToolResult(name=SEND_MESSAGE_TOOL_NAME, output=out, is_error=True)


def _resolve_in_process(
    name_or_id: str, context: ToolContext
) -> tuple[str, Any] | None:
    """Resolve ``to:`` against the in-process registries.

    Returns ``(agent_id, state_or_None)`` if a candidate is found
    (registry hit OR raw agent_id matches a runtime_tasks entry),
    None if neither registry knows the target. The caller decides
    whether to queue (running) or resume (terminal).
    """
    # Step 1 — agent name registry first.
    by_name = context.agent_name_registry.get(name_or_id)
    if by_name is not None:
        return by_name, context.runtime_tasks.get(by_name)
    # Step 2 — raw agent_id fallback (model may pass the id directly).
    by_id = context.runtime_tasks.get(name_or_id)
    if by_id is not None or name_or_id in context.agent_continuations:
        return name_or_id, by_id
    return None


async def _route_in_process(
    *, to: str, message_text: str, context: ToolContext
) -> ToolResult | None:
    """Try to route as an in-process agent. Returns None if the target
    isn't an in-process agent (caller falls through to mailbox)."""
    from src.tasks.local_agent import (
        LocalAgentTaskState,
        queue_pending_message,
    )
    from src.tasks_core import is_terminal_task_status

    resolved = _resolve_in_process(to, context)
    if resolved is None:
        return None
    agent_id, state = resolved

    if state is None and agent_id not in context.agent_continuations:
        return None
    if state is not None and not isinstance(state, LocalAgentTaskState):
        return None
    if state is not None and not is_terminal_task_status(state.status):
        if queue_pending_message(agent_id, message_text, context.runtime_tasks):
            return _ok(
                f"Message queued for delivery to {to!r} at its next turn.",
                agent_id=agent_id,
            )
        # Completion raced the queue operation: fall through to real resume.

    # Terminal — attempt auto-resume. Race-guarded by
    # ``resume_agent_background``: only one concurrent caller wins.
    from src.agent.resume_agent import resume_agent_background

    result = await resume_agent_background(
        agent_id=agent_id, prompt=message_text, context=context,
    )
    if result.resumed:
        return _ok(
            f"Agent {to!r} resumed in the background with your message.",
            agent_id=agent_id,
            output_file=result.output_file,
        )
    # Lost the race or unable to resume — queue onto whatever the
    # winner registered (or report an error if the agent state moved
    # in an unexpected way).
    queued = queue_pending_message(agent_id, message_text, context.runtime_tasks)
    if queued:
        return _ok(
            f"Agent {to!r} is being resumed by another caller; your "
            f"message was queued for delivery at its next tool round.",
            agent_id=agent_id,
        )
    return _err(
        f"Agent {to!r} could not be resumed: {result.reason}",
        agent_id=agent_id,
    )


async def _route_team_mailbox(
    *, to: str, message_text: str, summary: str | None, context: ToolContext
) -> ToolResult | None:
    """Mailbox routing — write a JSONL line to the recipient's inbox.

    Returns None if no team context is active (caller falls through
    to the error branch). Also handles the ``"*"`` broadcast.
    """
    if context.team is None:
        return None
    team_file = read_team_file(context.workspace_root)
    if team_file is None:
        return None
    team_name = team_file.team_name
    sender_name = context.team.get("sender_name") or "team-lead"

    if to == "*":
        return _broadcast(
            team_file=team_file, sender_name=sender_name,
            message_text=message_text, summary=summary, context=context,
        )

    # Sanity: confirm the recipient is on the team roster (defense-in-
    # depth — the model could pass an arbitrary string). If the team
    # file has no members yet (Chunk-F TeamCreate writes []),
    # fall through to the path-sanitized write — the recipient's
    # inbox file is created on demand.
    if team_file.members and find_member_by_name(team_file, to) is None:
        return _err(
            f"Recipient {to!r} is not on team {team_name!r}.",
            recipient=to,
        )

    msg = TeammateMessage(
        from_=sender_name, text=message_text, timestamp=make_iso_timestamp(),
        summary=summary,
    )
    try:
        write_to_mailbox(
            to, msg, team_name=team_name, workspace_root=context.workspace_root,
        )
    except ValueError as exc:
        return _err(f"Invalid recipient: {exc}")
    return _ok(
        f"Message delivered to mailbox of {to!r}.",
        recipient=to,
    )


def _broadcast(
    *,
    team_file: TeamFile,
    sender_name: str,
    message_text: str,
    summary: str | None,
    context: ToolContext,
) -> ToolResult:
    """``to: "*"`` — fan out to every team member except the sender.

    Per chapter §"The Mailbox": no fan-out optimization, each
    recipient gets a separate ``write_to_mailbox`` call. At swarm
    scale (3-8 members) this is trivially cheap.
    """
    delivered: list[str] = []
    for member in team_file.members:
        if member.name.lower() == sender_name.lower():
            continue  # don't echo to self
        msg = TeammateMessage(
            from_=sender_name, text=message_text,
            timestamp=make_iso_timestamp(), summary=summary,
        )
        try:
            write_to_mailbox(
                member.name, msg,
                team_name=team_file.team_name,
                workspace_root=context.workspace_root,
            )
            delivered.append(member.name)
        except ValueError:
            # Skip malformed names — shouldn't happen with TeamCreate-
            # written rosters but defensive.
            continue
    return _ok(
        f"Broadcast delivered to {len(delivered)} teammate(s).",
        recipients=delivered,
    )


def _approve_flag(message_obj: dict[str, Any]) -> bool:
    """Read ``approve`` with the original's semanticBoolean→z.boolean strictness.

    The original wraps ``approve`` in ``semanticBoolean()`` inside the union
    branches (SendMessageTool.ts:55,61): the exact literals "true"/"false"
    coerce, and anything else — junk strings, numbers, a missing field —
    fails z.boolean(), rejecting the whole message. The port's loose oneOf
    (no ``properties``) never type-checks ``approve``, so the strictness
    must live here: shutdown/plan approvals are control-plane messages, and
    a truthy junk value ("no", "0", 1) must not read as an approval.
    """
    from ..schema_validation import semantic_coerce

    coerced = semantic_coerce(message_obj.get("approve"), {"type": "boolean"})
    if not isinstance(coerced, bool):
        raise ToolInputError(
            'approve must be a boolean (or the string literal "true"/"false").'
        )
    return coerced


def _structured_message_to_envelope(
    *,
    message_obj: dict[str, Any],
    sender_name: str,
    context: ToolContext,
) -> tuple[dict[str, Any], str]:
    """Build a structured-protocol envelope and identify the recipient
    (which may differ from ``to`` for shutdown_response — those
    always go to the team lead by name).

    Returns ``(envelope_dict, recipient_name)``. Raises
    ``ToolInputError`` for invalid / unauthorized payloads.
    """
    msg_type = message_obj.get("type")
    request_id = str(message_obj.get("request_id") or "")
    if msg_type == "shutdown_request":
        return (
            create_shutdown_request_message(
                request_id=request_id, from_=sender_name,
                reason=message_obj.get("reason"),
            ),
            "",  # caller passes the original ``to``
        )
    if msg_type == "shutdown_response":
        approve = _approve_flag(message_obj)
        if approve:
            return (
                create_shutdown_approved_message(
                    request_id=request_id, from_=sender_name,
                ),
                "",  # caller passes the original ``to`` (typically team-lead)
            )
        reason = message_obj.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolInputError(
                "shutdown_response with approve=False requires a reason."
            )
        return (
            create_shutdown_rejected_message(
                request_id=request_id, from_=sender_name, reason=reason,
            ),
            "",
        )
    if msg_type == "plan_approval_response":
        # Sender-side authorization gate — only team-lead may issue
        # plan approvals (chapter §"Plan-mode lifecycle"; refactoring-
        # plan critic concern C3 sender-side check).
        if not is_team_lead(context):
            raise ToolInputError(
                "plan_approval_response can only be sent by the team lead."
            )
        approve = _approve_flag(message_obj)
        permission_mode = str(
            message_obj.get("permission_mode") or "default"
        )
        feedback = message_obj.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise ToolInputError("plan_approval_response.feedback must be a string.")
        return (
            create_plan_approval_response_message(
                request_id=request_id, approved=approve,
                permission_mode=permission_mode, from_=sender_name,
                feedback=feedback,
            ),
            "",
        )
    raise ToolInputError(
        f"Unknown structured-protocol type: {msg_type!r}. "
        f"Expected one of: {sorted(_VALID_STRUCTURED_TYPES)}."
    )


async def _send_message_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    """Validate input and route to the active team or a local worker."""
    to = tool_input.get("to")
    if not isinstance(to, str) or not to.strip():
        raise ToolInputError("'to' is required and must be a non-empty string.")
    to = to.strip()

    raw_message = tool_input.get("message")
    if raw_message is None:
        raise ToolInputError("'message' is required.")

    summary = tool_input.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ToolInputError("'summary' must be a string when provided.")

    # ``to`` validation: TS rejects '@' (the chapter has one team per
    # session, no fully-qualified addressing).
    if "@" in to and not to.startswith(("bridge:", "uds:")):
        raise ToolInputError(
            "'to' must be a bare teammate name or '*' — no '@' addressing."
        )

    addr = parse_address(to)

    # Branch 1 — bridge: stub.
    if addr.scheme == "bridge":
        raise NotImplementedError(
            f"bridge: addressing is not supported in this build "
            f"(feature('UDS_INBOX') is off-by-default upstream; "
            f"target was {addr.target!r})."
        )

    # Branch 2 — uds: stub.
    if addr.scheme == "uds":
        raise NotImplementedError(
            f"uds: addressing is not supported in this build "
            f"(feature('UDS_INBOX') is off-by-default upstream; "
            f"target was {addr.target!r})."
        )

    if context.team_runtime is not None:
        if isinstance(raw_message, str) and not summary:
            raise ToolInputError("'summary' is required for plain-text messages.")
        if (
            isinstance(raw_message, str)
            and to != "*"
            and not context.team_runtime.has_recipient(to)
        ):
            local = await _route_in_process(
                to=to, message_text=raw_message, context=context
            )
            if local is not None:
                return local
        output = context.team_runtime.send(context, to, raw_message, summary)
        return ToolResult(name=SEND_MESSAGE_TOOL_NAME, output=output)

    # Determine sender name — the team config's ``sender_name`` if
    # set, else the agent's id. Plain leader uses 'team-lead'.
    sender_name = "team-lead"
    if context.team is not None:
        sender_name = context.team.get("sender_name") or sender_name

    # Resolve plain-text vs structured payload.
    if isinstance(raw_message, str):
        # Plain text path — Branches 3, 4, or 5.
        message_text = raw_message
        if not summary and to != "*":
            # Summary is required for plain-text messages per TS
            # validation; broadcast accepts no-summary because the UI
            # surfaces a generic broadcast indicator.
            raise ToolInputError(
                "'summary' is required for plain-text messages."
            )

        # Branch 3 — in-process.
        if to != "*":
            in_process_result = await _route_in_process(
                to=to, message_text=message_text, context=context,
            )
            if in_process_result is not None:
                return in_process_result

        # Branch 4 — team mailbox / broadcast.
        mailbox_result = await _route_team_mailbox(
            to=to, message_text=message_text, summary=summary,
            context=context,
        )
        if mailbox_result is not None:
            return mailbox_result

        # Branch 5 — not found.
        return _err(
            f"Recipient {to!r} not found (no in-process agent with that "
            f"name/id; no team context to mailbox into)."
        )

    if not isinstance(raw_message, dict):
        raise ToolInputError(
            "'message' must be a string or a structured-protocol object."
        )

    # Structured-protocol path — always routes via mailbox (no
    # in-process queue for protocol messages; the mailbox poller
    # translates them back into teammate state changes).
    envelope, _ = _structured_message_to_envelope(
        message_obj=raw_message, sender_name=sender_name, context=context,
    )
    if context.team is None:
        raise ToolInputError(
            "structured-protocol messages require an active team context."
        )
    team_file = read_team_file(context.workspace_root)
    if team_file is None:
        raise ToolInputError(
            "structured-protocol messages require a team file; "
            "TeamCreate hasn't run."
        )

    import json as _json
    msg = TeammateMessage(
        from_=sender_name,
        text=_json.dumps(envelope, ensure_ascii=False),
        timestamp=make_iso_timestamp(),
    )
    try:
        write_to_mailbox(
            to, msg, team_name=team_file.team_name,
            workspace_root=context.workspace_root,
        )
    except ValueError as exc:
        return _err(f"Invalid recipient: {exc}")
    return _ok(
        f"Structured {raw_message.get('type')!r} envelope delivered to "
        f"mailbox of {to!r}.",
        recipient=to, envelope_type=raw_message.get("type"),
    )


def _send_message_classifier_input(input_data: dict) -> str:
    """Mirror TS ``SendMessageTool.toAutoClassifierInput``. The TS
    classifier collapses string and structured envelope messages into
    a single line: ``to {to}: {message}`` for plain text, ``to {to}:
    <{type}>`` for structured envelopes."""
    d = input_data or {}
    to = d.get("to", "")
    msg = d.get("message", "")
    if isinstance(msg, str):
        return f"to {to}: {msg}"
    if isinstance(msg, dict):
        t = msg.get("type", "structured")
        return f"to {to}: <{t}>"
    return f"to {to}"


def _send_message_check_permissions(tool_input: dict, _context):
    """Mirror TS ``SendMessageTool.checkPermissions`` (SendMessageTool.ts:585).

    Local / team-mailbox / broadcast sends are allowed silently (coordination
    traffic must not prompt). A cross-machine ``bridge:`` / ``uds:`` recipient
    returns a non-classifier-approvable safety ``ask`` — bypass- and auto-mode
    immune — because it is cross-trust-boundary prompt injection. (Those
    transports are NotImplementedError stubs in this build, but the gate is
    wired now so it cannot be forgotten when they land.)
    """
    from src.permissions.types import (
        PermissionAllowDecision,
        PermissionAskDecision,
        SafetyCheckDecisionReason,
    )

    to = (tool_input or {}).get("to")
    if isinstance(to, str) and (to.startswith("bridge:") or to.startswith("uds:")):
        return PermissionAskDecision(
            behavior="ask",
            message=f"Send a message to cross-machine recipient {to}?",
            decision_reason=SafetyCheckDecisionReason(
                reason="Cross-machine message recipient requires confirmation",
                classifier_approvable=False,
            ),
        )
    return PermissionAllowDecision(behavior="allow", updated_input=tool_input)


SendMessageTool: Tool = build_tool(
    name=SEND_MESSAGE_TOOL_NAME,
    input_schema=SEND_MESSAGE_INPUT_SCHEMA,
    call=_send_message_call,
    check_permissions=_send_message_check_permissions,
    prompt=(
        "Send a message to a teammate, a remote peer, or broadcast to "
        "the team. The 'to' field accepts a teammate name, '*' for "
        "broadcast, or a local worker ID. Bridge and UDS transports are unsupported. "
        "The 'message' field is plain text or a structured protocol "
        "(shutdown_request, shutdown_response, plan_approval_response). "
        "A shutdown_request returns a generated request_id. Reply with that ID and approve true, "
        "or approve false with a reason. Plan responses must come from team-lead and include "
        "the plan request_id, approve, and optionally permission_mode. Rejection keeps plan restrictions."
    ),
    description="Send a message to a teammate / peer / team.",
    strict=False,  # ``message`` is a union, can't strict-validate via JSON Schema
    max_result_size_chars=2000,
    is_read_only=lambda _input: False,
    # ``is_concurrency_safe`` deliberately NOT overridden — defaults to
    # False to match the parity snapshot. SendMessage's registry +
    # mailbox writes are thread-safe (per A6/C5), but TS doesn't
    # declare the tool as concurrency-safe either; mirror that until
    # there's a demonstrated need.
    to_auto_classifier_input=_send_message_classifier_input,
)


__all__ = [
    "SEND_MESSAGE_TOOL_NAME",
    "SEND_MESSAGE_INPUT_SCHEMA",
    "SendMessageTool",
]

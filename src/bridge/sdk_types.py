"""CCR wire-format types — discriminated unions used by the bridge layer.

Ports a curated subset of the TS SDK schemas at
``typescript/src/entrypoints/sdk/{coreTypes.generated.ts, shared.ts,
controlSchemas.ts}`` (~3,393 lines combined). Only the variants this
plan's WIs touch are defined here (~250-300 LOC). For full schema, see
the TS sources.

We use ``TypedDict(total=False)`` per variant + a top-level ``Union``
alias so the discriminating field (``type`` / ``subtype``) tells callers
which variant they are looking at. This matches Python's `pyright` /
`mypy` discriminated-union pattern; consumers narrow via ``isinstance``
or ``match`` on the discriminator field.

NOTE on field naming: every wire-level field uses **snake_case** because
that is what the CCR servers send. ``src/types/messages.py`` has a
parallel ``Message`` hierarchy with ``camelCase`` fields for the local
REPL; the two never alias and live in separate files.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, Union

# ─── Core SDK message variants (the `type` discriminator) ──────────────────

# Each variant is a TypedDict with ``total=False`` so optional fields can
# be omitted. The discriminating ``type`` field is required by the
# discriminated-union convention.


class UserMessage(TypedDict, total=False):
    """User turn from the model API.

    Source: ``coreTypes.generated.ts`` UserMessage variant.
    """

    type: Literal['user']
    uuid: str
    session_id: str
    parent_tool_use_id: str | None
    message: dict[str, Any]


class AssistantMessage(TypedDict, total=False):
    """Assistant turn (text + tool calls) from the model API."""

    type: Literal['assistant']
    uuid: str
    session_id: str
    parent_tool_use_id: str | None
    message: dict[str, Any]


class SystemMessage(TypedDict, total=False):
    """System message: init, post_turn_summary, local_command, etc."""

    type: Literal['system']
    subtype: str
    uuid: str
    session_id: str
    data: dict[str, Any]


class SDKResultSuccess(TypedDict, total=False):
    """Result message — emitted on session teardown for archival.

    Used by ``make_result_message`` (WI-2.6c) to tell the server the
    session ended cleanly. See ``bridgeMessaging.ts:399-416``.
    """

    type: Literal['result']
    subtype: Literal['success']
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    result: str
    stop_reason: str | None
    total_cost_usd: float
    usage: dict[str, Any]
    modelUsage: dict[str, Any]
    permission_denials: list[dict[str, Any]]
    session_id: str
    uuid: str


class ToolResultMessage(TypedDict, total=False):
    """Tool execution result, emitted by the local REPL after a tool call."""

    type: Literal['tool_result']
    uuid: str
    session_id: str
    tool_use_id: str
    content: Any
    is_error: bool


class KeepAliveMessage(TypedDict, total=False):
    """Empty heartbeat from the server. Filtered by the ingress router."""

    type: Literal['keep_alive']


class StreamlinedTextMessage(TypedDict, total=False):
    """Streamlined text payload (compact wire form). Filtered by Direct Connect."""

    type: Literal['streamlined_text']
    text: str


class StreamlinedToolUseSummaryMessage(TypedDict, total=False):
    """Streamlined tool-use summary. Filtered by Direct Connect."""

    type: Literal['streamlined_tool_use_summary']
    tool_name: str
    summary: str


# ─── control_request inner subtypes (the `subtype` discriminator) ──────────

# control_request.request is itself a discriminated union on ``subtype``.
# We declare each subtype as a TypedDict and union them.


class InitializeRequest(TypedDict, total=False):
    subtype: Literal['initialize']


class SetModelRequest(TypedDict, total=False):
    subtype: Literal['set_model']
    model: str | None


class SetMaxThinkingTokensRequest(TypedDict, total=False):
    subtype: Literal['set_max_thinking_tokens']
    max_thinking_tokens: int | None


class SetPermissionModeRequest(TypedDict, total=False):
    subtype: Literal['set_permission_mode']
    mode: str  # PermissionMode in src/permissions/types.py — kept str on the wire.


class InterruptRequest(TypedDict, total=False):
    subtype: Literal['interrupt']


class SDKControlPermissionRequest(TypedDict, total=False):
    """``can_use_tool`` payload — the only request the server sends to ask
    a permission decision from the client.
    """

    subtype: Literal['can_use_tool']
    tool_name: str
    input: dict[str, Any]
    tool_use_id: str | None


SDKControlRequestInner = Union[
    InitializeRequest,
    SetModelRequest,
    SetMaxThinkingTokensRequest,
    SetPermissionModeRequest,
    InterruptRequest,
    SDKControlPermissionRequest,
]


class SDKControlRequest(TypedDict, total=False):
    """``{type:'control_request', request_id, request}`` envelope."""

    type: Literal['control_request']
    request_id: str
    request: SDKControlRequestInner


# ─── control_response variants ─────────────────────────────────────────────


class ControlResponseSuccess(TypedDict, total=False):
    subtype: Literal['success']
    request_id: str
    response: dict[str, Any] | None


class ControlResponseError(TypedDict, total=False):
    subtype: Literal['error']
    request_id: str
    error: str


ControlResponseInner = Union[ControlResponseSuccess, ControlResponseError]


class SDKControlResponse(TypedDict, total=False):
    """``{type:'control_response', response}`` envelope."""

    type: Literal['control_response']
    response: ControlResponseInner


# ─── Cancellation envelope ─────────────────────────────────────────────────


class SDKControlCancelRequest(TypedDict, total=False):
    """Server → client: cancel a pending permission prompt by ``request_id``."""

    type: Literal['control_cancel_request']
    request_id: str
    tool_use_id: str | None


# ─── Top-level union and aliases ───────────────────────────────────────────

# The full SDKMessage union — anything the read-side router can encounter
# on the bridge stream. Note this includes both data messages (user,
# assistant, etc.) and control messages (control_request, control_response,
# control_cancel_request); the router branches on ``type`` first.

SDKMessage = Union[
    UserMessage,
    AssistantMessage,
    SystemMessage,
    SDKResultSuccess,
    ToolResultMessage,
    KeepAliveMessage,
    StreamlinedTextMessage,
    StreamlinedToolUseSummaryMessage,
    SDKControlRequest,
    SDKControlResponse,
    SDKControlCancelRequest,
]

# Write-side alias — what the client sends to the server. control_request
# is excluded because the local CLI never originates a control_request
# (the server initiates those); control_response is what the client uses
# to reply. Matches ``StdoutMessage`` in TS.

StdoutMessage = Union[
    UserMessage,
    AssistantMessage,
    SystemMessage,
    SDKResultSuccess,
    ToolResultMessage,
    SDKControlResponse,
    SDKControlRequest,  # for client-originated interrupt
]


__all__ = [
    'AssistantMessage',
    'ControlResponseError',
    'ControlResponseInner',
    'ControlResponseSuccess',
    'InitializeRequest',
    'InterruptRequest',
    'KeepAliveMessage',
    'SDKControlCancelRequest',
    'SDKControlPermissionRequest',
    'SDKControlRequest',
    'SDKControlRequestInner',
    'SDKControlResponse',
    'SDKMessage',
    'SDKResultSuccess',
    'SetMaxThinkingTokensRequest',
    'SetModelRequest',
    'SetPermissionModeRequest',
    'StdoutMessage',
    'StreamlinedTextMessage',
    'StreamlinedToolUseSummaryMessage',
    'SystemMessage',
    'ToolResultMessage',
    'UserMessage',
]

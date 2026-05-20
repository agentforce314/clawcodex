"""Client-side advisor tool — the dispatch handler for ``tool_use(name=advisor)``.

This tool only fires in client-side advisor mode (3P providers or
``advisor_client_mode=True``). The server-side path emits
``server_tool_use`` blocks that the API handles; that path never
reaches this dispatcher.

The tool takes no parameters: the conversation history is forwarded
implicitly via ``ToolContext.messages`` (populated by ``_call_model_sync``
before the per-turn tool round). The advisor's reply is returned as
plain text in the ``ToolResult``.

``is_enabled`` is permanently False so the tool stays out of ``/tools``
listings and out of the default schema produced by ``get_tools``;
``_call_model_sync`` appends the schema manually only when client-side
mode is active for the request. The registry still finds the tool by
name when the dispatcher routes a matching ``tool_use`` block.
"""

from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult


def _advisor_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    # Local imports to avoid pulling settings / providers at module load
    # — this tool sits in the static registry and we don't want every
    # boot path to wake those packages.
    from src.settings.settings import get_settings
    from src.utils.advisor import (
        build_advisor_forwarded_messages,
        execute_client_advisor,
    )

    settings = get_settings()
    advisor_model = (getattr(settings, "advisor_model", "") or "").strip()
    if not advisor_model:
        # Defensive: the schema should never be exposed when advisor_model
        # is empty, so reaching this branch means activation drifted from
        # configuration. Surface as a tool failure (not a turn-killer) so
        # the worker keeps moving.
        return ToolResult(
            name="advisor",
            output="Advisor unavailable: no advisor_model configured.",
            is_error=True,
        )

    history = list(getattr(context, "messages", []) or [])
    forwarded = build_advisor_forwarded_messages(history)

    abort = getattr(context, "abort_controller", None)
    abort_signal = getattr(abort, "signal", None) if abort is not None else None

    ok, text = execute_client_advisor(
        advisor_model,
        forwarded,
        abort_signal=abort_signal,
    )
    return ToolResult(
        name="advisor",
        output=text,
        is_error=not ok,
    )


# The schema sent to the API is built in ``src/utils/advisor.py`` via
# ``build_client_advisor_tool_schema`` — kept there alongside the
# server-side schema so both wire formats live in one place. The
# ``input_schema`` here mirrors that exactly so registry validation
# (``validate_json_schema``) accepts the empty-args call.
AdvisorTool: Tool = build_tool(
    name="advisor",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    call=_advisor_call,
    prompt="",
    description=(
        "Consult a stronger reviewer model. The conversation is forwarded "
        "automatically; takes no parameters."
    ),
    # is_enabled=False keeps the tool out of /tools and out of the
    # default schema list. _call_model_sync injects the schema manually
    # when client-side mode is active for the request. Registry lookup
    # by name (used by dispatch) is unaffected by this flag.
    is_enabled=lambda: False,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    # Generous: advisor responses can be substantial. The TUI row trims
    # for display; the full text rides as tool_result content into the
    # next turn.
    max_result_size_chars=50_000,
)

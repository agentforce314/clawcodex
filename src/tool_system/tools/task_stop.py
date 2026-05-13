"""TaskStop tool — terminate a running background task by id.

Mirrors ``typescript/src/tools/TaskStopTool/TaskStopTool.ts``. Refactor
history:

* Chunk A / Phase 0 — closed the WI-0.1 ``stop_requested`` footgun,
  restored the deprecated ``shell_id`` field (WI-0.2), declared the
  ``KillShell`` alias (WI-0.3). Added a ``task_manager`` dispatch
  branch as a Phase-0 sub-fix to preserve the legacy
  ``TestTaskStopTool::test_task_stop`` test's intent.
* Chunk B / WI-1.4 — runtime_tasks typed-dispatch branch slotted in
  ahead of the legacy bash branch.
* Chunk D / WI-4.0 — function flipped to ``async def`` once the
  registry's ``_invoke_tool_call`` learned async.
* Chunk E / WI-5.1 + WI-5.2 — dispatch logic hoisted into
  ``src.tasks.stop_task.stop_task``; ``_task_stop_call`` is now a thin
  ~20-line shim that handles input parsing and ``StopTaskResult`` →
  ``ToolResult`` formatting only.

The shrink is the SOLID part: TaskStop's single responsibility is now
"map model input/output"; dispatch + per-type kill + timeout handling
all live in the typed helper.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult
from src.tasks.stop_task import stop_task


async def _task_stop_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    """Validate input, delegate to ``stop_task``, format the result.

    Shapes the model-facing output as ``{stopped, task_id, error?,
    error_code?, reason}`` so existing callers (Phase-0 tests,
    Chunk-B/D regression tests) keep matching. ``StopTaskResult`` is
    flattened via ``asdict`` and the optional ``StopTaskError`` is
    promoted to two top-level fields for convenience.
    """
    reason = tool_input.get("reason", "")
    # WI-0.2: ``shell_id`` is the deprecated KillShell field. ``task_id``
    # takes precedence when both are set.
    raw_id = tool_input.get("task_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raw_id = tool_input.get("shell_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return ToolResult(
            name="TaskStop",
            output={
                "stopped": False,
                "task_id": None,
                "error": "task_id is required",
                "reason": reason,
            },
            is_error=True,
        )
    task_id = raw_id.strip()

    result = await stop_task(task_id, context, reason=reason)
    output: dict[str, Any] = {
        "stopped": result.stopped,
        "task_id": result.task_id,
        "reason": reason,
    }
    if result.task_type is not None:
        output["task_type"] = result.task_type
    if result.error is not None:
        output["error"] = result.error.message
        output["error_code"] = result.error.code
    return ToolResult(
        name="TaskStop",
        output=output,
        is_error=result.is_error,
    )


TaskStopTool: Tool = build_tool(
    name="TaskStop",
    # WI-0.3: legacy alias for back-compat with KillShell-era transcripts.
    aliases=("KillShell",),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop.",
            },
            # WI-0.2: deprecated; kept so older transcripts that used
            # ``KillShell({shell_id: ...})`` still validate and dispatch.
            "shell_id": {
                "type": "string",
                "description": "Deprecated: use task_id instead.",
            },
            "reason": {
                "type": "string",
                "description": "Optional human-readable reason; echoed back in the result.",
            },
        },
    },
    call=_task_stop_call,
    prompt="""\
Stops a running background task by its ID.

- Takes a task_id parameter identifying the task to stop
- Returns a success or failure status
- Use this tool when you need to terminate a long-running task
""",
    description="Stop a running background task by its ID.",
    strict=True,
    max_result_size_chars=1000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    # Mirrors TS TaskStopTool.toAutoClassifierInput, including the
    # KillShell-era ``shell_id`` fallback.
    to_auto_classifier_input=lambda input_data: (
        (input_data or {}).get("task_id")
        or (input_data or {}).get("shell_id")
        or ""
    ),
)

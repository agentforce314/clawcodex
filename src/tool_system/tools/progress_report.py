"""ProgressReportTool - Agent 阶段性进度汇报工具。

Port of the phase-based progress reporting design from FEATURE_PLAN.md F-20.
Provides a dedicated tool for agents to report stage-based progress to the task board,
complementing the existing TaskUpdateTool with a progress-specific interface.

Architecture:
    Agent 执行到检查点 (方式一：检查点触发)
        ↓
    调用 ProgressReportTool (方式二：专用工具)
        ↓
    数据存入 ToolContext.tasks (方式三：持久化)

The tool updates task metadata with progress information rather than creating
a separate storage, keeping data in ToolContext.tasks for downstream consumers
like StatusDashboard.
"""

from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


# ---------------------------------------------------------------------------
# Classifier input helpers (for auto-classifier system)
# ---------------------------------------------------------------------------


def _progress_report_classifier_input(input_data: dict) -> str:
    """Build compact classifier input for ProgressReport tool."""
    d = input_data or {}
    parts: list[str] = []
    tid = d.get("taskId")
    if tid:
        parts.append(str(tid))
    stage = d.get("stage")
    if stage:
        parts.append(str(stage))
    progress = d.get("progress")
    if progress is not None:
        parts.append(str(progress))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


def _format_progress_report(
    task_id: str,
    stage: str,
    progress: float | None,
    success: bool,
    error: str | None = None,
) -> str:
    """Format ProgressReport result as human-readable text."""
    if not success:
        return error or f"Failed to report progress for task #{task_id}"
    progress_str = f"{progress}% " if progress is not None else ""
    return f"Progress reported for task #{task_id}: {progress_str}[{stage}]"


# ---------------------------------------------------------------------------
# Tool call implementation
# ---------------------------------------------------------------------------


def _progress_report_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    """Handle ProgressReport tool call.

    Updates task metadata with progress information. The actual progress data
    is stored in the task's metadata field, following the existing pattern
    where TaskUpdateTool uses metadata for extension data.

    Args:
        tool_input: Dict with taskId, stage, progress, summary, nextAction, metadata
        context: ToolContext with access to tasks storage

    Returns:
        ToolResult with success status and formatted message
    """
    task_id = tool_input.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolInputError("taskId must be a non-empty string")

    # Validate task exists
    task = context.tasks.get(task_id)
    if task is None:
        return ToolResult(
            name="ProgressReport",
            output={
                "success": False,
                "taskId": task_id,
                "error": "Task not found",
            },
        )

    stage = tool_input.get("stage")
    if not isinstance(stage, str) or not stage.strip():
        raise ToolInputError("stage must be a non-empty string")

    # Optional fields
    progress = tool_input.get("progress")
    if progress is not None:
        if not isinstance(progress, (int, float)):
            raise ToolInputError("progress must be a number when provided")
        if not (0 <= progress <= 100):
            raise ToolInputError("progress must be between 0 and 100")

    summary = tool_input.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ToolInputError("summary must be a string when provided")

    next_action = tool_input.get("nextAction")
    if next_action is not None and not isinstance(next_action, str):
        raise ToolInputError("nextAction must be a string when provided")

    metadata = tool_input.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ToolInputError("metadata must be an object when provided")

    # Build progress record to store in task metadata
    from datetime import datetime, timezone

    progress_record: dict[str, Any] = {
        "stage": stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if progress is not None:
        progress_record["progress"] = progress
    if summary:
        progress_record["summary"] = summary
    if next_action:
        progress_record["nextAction"] = next_action
    if metadata:
        progress_record["metadata"] = dict(metadata)

    # Update task metadata with progress information
    existing = dict(task.get("metadata") or {})

    # Initialize progress_stages if not present
    if "progress_stages" not in existing:
        existing["progress_stages"] = []

    # Append new progress record
    stages: list[dict[str, Any]] = existing["progress_stages"]
    stages.append(progress_record)
    existing["progress_stages"] = stages

    # Also track current stage info at top level for quick access
    existing["current_stage"] = stage
    if progress is not None:
        existing["progress"] = progress

    task["metadata"] = existing

    return ToolResult(
        name="ProgressReport",
        output={
            "success": True,
            "taskId": task_id,
            "stage": stage,
            "progress": progress,
            "recordedAt": progress_record["timestamp"],
        },
    )


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


ProgressReportTool: Tool = build_tool(
    name="ProgressReport",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "taskId": {
                "type": "string",
                "description": "ID of the task to report progress for",
            },
            "stage": {
                "type": "string",
                "description": "Current stage name (e.g., 'analysis', 'implementation', 'testing')",
            },
            "progress": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "description": "Progress percentage (0-100)",
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of what was accomplished in this stage",
            },
            "nextAction": {
                "type": "string",
                "description": "Next action planned after this stage",
            },
            "metadata": {
                "type": "object",
                "description": "Additional custom metadata for this progress report",
            },
        },
        "required": ["taskId", "stage"],
    },
    call=_progress_report_call,
    prompt="""\
Use this tool to report stage-based progress to the task board during agent orchestration.

## When to Use This Tool

- **Phase completion reporting**: When an agent completes a distinct phase of work
  (e.g., "analysis complete", "implementation done", "testing finished")
- **Checkpoint progress**: At designated checkpoints in multi-phase workflows
- **Progress tracking**: To update stakeholders on work status without blocking the workflow

## When NOT to Use This Tool

- For simple task status updates (use TaskUpdate instead)
- For creating new tasks (use TaskCreate)
- For detailed output reporting (use TaskOutput or write to files)

## Progress Data Flow

The progress data is stored in the task's metadata field, accessible by:
- StatusDashboard for real-time display
- TaskOutput for retrieval
- TaskGet for detailed view

## Examples

Report analysis phase completion:
```json
{
  "taskId": "abc123",
  "stage": "analysis",
  "progress": 100,
  "summary": "Completed requirements analysis for auth module",
  "nextAction": "Begin implementation of authentication endpoints"
}
```

Report mid-phase progress:
```json
{
  "taskId": "def456",
  "stage": "implementation",
  "progress": 50,
  "summary": "Completed user registration endpoint",
  "nextAction": "Implement password reset flow"
}
```
""",
    description="Report stage-based progress to the task board.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,
    is_concurrency_safe=lambda _input: True,
    to_auto_classifier_input=_progress_report_classifier_input,
)
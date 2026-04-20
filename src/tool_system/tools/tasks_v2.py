from __future__ import annotations

import uuid
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from src.utils.task_flags import is_todo_v2_enabled


_TASK_STATUSES = {"pending", "in_progress", "completed"}


def _new_task_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Result formatting helpers (port of TS mapToolResultToToolResultBlockParam)
# ---------------------------------------------------------------------------


def _format_task_created(task_id: str, subject: str) -> str:
    """Format TaskCreate result as human-readable text."""
    return f"Task #{task_id} created successfully: {subject}"


def _format_task_detail(task: dict[str, Any] | None) -> str:
    """Format TaskGet result as human-readable text."""
    if task is None:
        return "Task not found"
    lines = [
        f"Task #{task['id']}: {task['subject']}",
        f"Status: {task['status']}",
        f"Description: {task['description']}",
    ]
    blocked_by = task.get("blockedBy") or []
    if blocked_by:
        lines.append(f"Blocked by: {', '.join(f'#{bid}' for bid in blocked_by)}")
    blocks = task.get("blocks") or []
    if blocks:
        lines.append(f"Blocks: {', '.join(f'#{bid}' for bid in blocks)}")
    return "\n".join(lines)


def _format_task_list(tasks: list[dict[str, Any]]) -> str:
    """Format TaskList result as human-readable text."""
    if not tasks:
        return "No tasks found"
    lines = []
    for t in tasks:
        owner = f" ({t['owner']})" if t.get("owner") else ""
        blocked_by = t.get("blockedBy") or []
        blocked = (
            f" [blocked by {', '.join(f'#{bid}' for bid in blocked_by)}]"
            if blocked_by
            else ""
        )
        lines.append(f"#{t['id']} [{t['status']}] {t['subject']}{owner}{blocked}")
    return "\n".join(lines)


def _format_task_updated(
    success: bool,
    task_id: str,
    updated_fields: list[str],
    error: str | None = None,
    status_change: dict[str, str] | None = None,
) -> str:
    """Format TaskUpdate result as human-readable text."""
    if not success:
        return error or f"Task #{task_id} not found"
    return f"Updated task #{task_id} {', '.join(updated_fields)}"


# ---------------------------------------------------------------------------
# Cascade delete helper
# ---------------------------------------------------------------------------


def _cascade_delete(task_id: str, context: ToolContext) -> None:
    """Remove *task_id* from blocks/blockedBy lists of every other task."""
    for other in context.tasks.values():
        blocks = other.get("blocks")
        if blocks and task_id in blocks:
            other["blocks"] = [x for x in blocks if x != task_id]
        blocked_by = other.get("blockedBy")
        if blocked_by and task_id in blocked_by:
            other["blockedBy"] = [x for x in blocked_by if x != task_id]


# ---------------------------------------------------------------------------
# Tool call implementations
# ---------------------------------------------------------------------------


def _task_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    subject = tool_input.get("subject")
    description = tool_input.get("description")
    active_form = tool_input.get("activeForm") or ""
    metadata = tool_input.get("metadata") or {}
    if not isinstance(subject, str) or not subject.strip():
        raise ToolInputError("subject must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ToolInputError("description must be a non-empty string")
    if not isinstance(active_form, str):
        raise ToolInputError("activeForm must be a string when provided")
    if not isinstance(metadata, dict):
        raise ToolInputError("metadata must be an object when provided")

    task_id = _new_task_id()
    context.tasks[task_id] = {
        "id": task_id,
        "subject": subject,
        "description": description,
        "activeForm": active_form,
        "status": "pending",
        "owner": None,
        "blocks": [],
        "blockedBy": [],
        "metadata": dict(metadata),
        "output": "",
    }
    return ToolResult(
        name="TaskCreate",
        output={"task": {"id": task_id, "subject": subject}},
    )


TaskCreateTool: Tool = build_tool(
    name="TaskCreate",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "activeForm": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["subject", "description"],
    },
    call=_task_create_call,
    prompt="""\
Use this tool to create a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

- Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
- Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
- Plan mode - When using plan mode, create a task list to track the work
- User explicitly requests todo list - When the user directly asks you to use the todo list
- User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions - Immediately capture user requirements as tasks
- When you start working on a task - Mark it as in_progress BEFORE beginning work
- After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Fields

- **subject**: A brief, actionable title in imperative form (e.g., "Fix authentication bug in login flow")
- **description**: What needs to be done
- **activeForm** (optional): Present continuous form shown in the spinner when the task is in_progress (e.g., "Fixing authentication bug"). If omitted, the spinner shows the subject instead.

All tasks are created with status `pending`.

## Tips

- Create tasks with clear, specific subjects that describe the outcome
- After creating tasks, use TaskUpdate to set up dependencies (blocks/blockedBy) if needed
- Check TaskList first to avoid creating duplicate tasks
""",
    description="Create a new task in the task list.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    is_enabled=is_todo_v2_enabled,
)


def _task_get_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    task_id = tool_input.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolInputError("taskId must be a non-empty string")
    task = context.tasks.get(task_id)
    if task is None:
        return ToolResult(name="TaskGet", output={"task": None})
    task_data = {
        "id": task["id"],
        "subject": task["subject"],
        "description": task["description"],
        "status": task["status"],
        "blocks": list(task.get("blocks") or []),
        "blockedBy": list(task.get("blockedBy") or []),
    }
    return ToolResult(
        name="TaskGet",
        output={"task": task_data},
    )


TaskGetTool: Tool = build_tool(
    name="TaskGet",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"taskId": {"type": "string"}},
        "required": ["taskId"],
    },
    call=_task_get_call,
    prompt="""\
Use this tool to retrieve a task by its ID from the task list.

## When to Use This Tool

- When you need the full description and context before starting work on a task
- To understand task dependencies (what it blocks, what blocks it)
- After being assigned a task, to get complete requirements

## Output

Returns full task details:
- **subject**: Task title
- **description**: Detailed requirements and context
- **status**: 'pending', 'in_progress', or 'completed'
- **blocks**: Tasks waiting on this one to complete
- **blockedBy**: Tasks that must complete before this one can start

## Tips

- After fetching a task, verify its blockedBy list is empty before beginning work.
- Use TaskList to see all tasks in summary form.
""",
    description="Get a task by ID from the task list.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    is_enabled=is_todo_v2_enabled,
)


def _task_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    # Filter out internal tasks (metadata._internal == True)
    all_tasks = [
        t for t in context.tasks.values()
        if not (t.get("metadata") or {}).get("_internal")
    ]

    # Build set of completed task IDs for resolved blocker filtering
    completed_ids = {t["id"] for t in all_tasks if t.get("status") == "completed"}

    tasks = []
    for t in all_tasks:
        # Filter out resolved (completed) blockers from blockedBy
        raw_blocked_by = list(t.get("blockedBy") or [])
        active_blocked_by = [bid for bid in raw_blocked_by if bid not in completed_ids]
        tasks.append(
            {
                "id": t["id"],
                "subject": t["subject"],
                "status": t["status"],
                **({"owner": t["owner"]} if t.get("owner") else {}),
                "blockedBy": active_blocked_by,
            }
        )
    tasks.sort(key=lambda x: x["id"])
    return ToolResult(name="TaskList", output={"tasks": tasks})


TaskListTool: Tool = build_tool(
    name="TaskList",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_task_list_call,
    prompt="""\
Use this tool to list all tasks in the task list.

## When to Use This Tool

- To see what tasks are available to work on (status: 'pending', no owner, not blocked)
- To check overall progress on the project
- To find tasks that are blocked and need dependencies resolved
- After completing a task, to check for newly unblocked work or claim the next available task
- **Prefer working on tasks in ID order** (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones

## Output

Returns a summary of each task:
- **id**: Task identifier (use with TaskGet, TaskUpdate)
- **subject**: Brief description of the task
- **status**: 'pending', 'in_progress', or 'completed'
- **owner**: Agent ID if assigned, empty if available
- **blockedBy**: List of open task IDs that must be resolved first (tasks with blockedBy cannot be claimed until dependencies resolve)

Use TaskGet with a specific task ID to view full details including description and comments.
""",
    description="List all tasks in the task list.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    is_enabled=is_todo_v2_enabled,
)


def _task_update_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    task_id = tool_input.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolInputError("taskId must be a non-empty string")
    task = context.tasks.get(task_id)
    if task is None:
        return ToolResult(
            name="TaskUpdate",
            output={"success": False, "taskId": task_id, "updatedFields": [], "error": "Task not found"},
        )

    updated_fields: list[str] = []
    status_change: dict[str, str] | None = None

    for field in ("subject", "description", "activeForm", "owner"):
        if field in tool_input and tool_input[field] is not None:
            v = tool_input[field]
            if not isinstance(v, str):
                raise ToolInputError(f"{field} must be a string when provided")
            if v != task.get(field):
                task[field] = v
                updated_fields.append(field)

    if "status" in tool_input and tool_input["status"] is not None:
        status = tool_input["status"]
        if not isinstance(status, str) or status not in _TASK_STATUSES and status != "deleted":
            raise ToolInputError("status must be pending|in_progress|completed|deleted when provided")
        if status == "deleted":
            context.tasks.pop(task_id, None)
            # Cascade delete: remove this task's ID from all other tasks'
            # blocks and blockedBy arrays to prevent dangling references.
            _cascade_delete(task_id, context)
            return ToolResult(
                name="TaskUpdate",
                output={"success": True, "taskId": task_id, "updatedFields": ["deleted"]},
            )
        if status != task.get("status"):
            status_change = {"from": str(task.get("status")), "to": status}
            task["status"] = status
            updated_fields.append("status")

    for rel_field, input_key in (("blocks", "addBlocks"), ("blockedBy", "addBlockedBy")):
        if input_key in tool_input and tool_input[input_key] is not None:
            ids = tool_input[input_key]
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                raise ToolInputError(f"{input_key} must be an array of strings when provided")
            cur = list(task.get(rel_field) or [])
            for x in ids:
                if x not in cur:
                    cur.append(x)
            if cur != task.get(rel_field):
                task[rel_field] = cur
                updated_fields.append(rel_field)

    if "metadata" in tool_input and tool_input["metadata"] is not None:
        md = tool_input["metadata"]
        if not isinstance(md, dict):
            raise ToolInputError("metadata must be an object when provided")
        existing = dict(task.get("metadata") or {})
        for k, v in md.items():
            if v is None:
                existing.pop(k, None)
            else:
                existing[k] = v
        task["metadata"] = existing
        updated_fields.append("metadata")

    out: dict[str, Any] = {"success": True, "taskId": task_id, "updatedFields": updated_fields}
    if status_change is not None:
        out["statusChange"] = status_change
    return ToolResult(name="TaskUpdate", output=out)


TaskUpdateTool: Tool = build_tool(
    name="TaskUpdate",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "taskId": {"type": "string"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "activeForm": {"type": "string"},
            "status": {"type": "string"},
            "addBlocks": {"type": "array", "items": {"type": "string"}},
            "addBlockedBy": {"type": "array", "items": {"type": "string"}},
            "owner": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["taskId"],
    },
    call=_task_update_call,
    prompt="""\
Use this tool to update a task in the task list.

## When to Use This Tool

**Mark tasks as resolved:**
- When you have completed the work described in a task
- When a task is no longer needed or has been superseded
- IMPORTANT: Always mark your assigned tasks as resolved when you finish them
- After resolving, call TaskList to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

**Delete tasks:**
- When a task is no longer relevant or was created in error
- Setting status to `deleted` permanently removes the task

**Update task details:**
- When requirements change or become clearer
- When establishing dependencies between tasks

## Fields You Can Update

- **status**: The task status (see Status Workflow below)
- **subject**: Change the task title (imperative form, e.g., "Run tests")
- **description**: Change the task description
- **activeForm**: Present continuous form shown in spinner when in_progress (e.g., "Running tests")
- **owner**: Change the task owner (agent name)
- **metadata**: Merge metadata keys into the task (set a key to null to delete it)
- **addBlocks**: Mark tasks that cannot start until this one completes
- **addBlockedBy**: Mark tasks that must complete before this one can start

## Status Workflow

Status progresses: `pending` -> `in_progress` -> `completed`

Use `deleted` to permanently remove a task.

## Staleness

Make sure to read a task's latest state using `TaskGet` before updating it.

## Examples

Mark task as in progress when starting work:
```json
{"taskId": "1", "status": "in_progress"}
```

Mark task as completed after finishing work:
```json
{"taskId": "1", "status": "completed"}
```

Delete a task:
```json
{"taskId": "1", "status": "deleted"}
```

Claim a task by setting owner:
```json
{"taskId": "1", "owner": "my-name"}
```

Set up task dependencies:
```json
{"taskId": "2", "addBlockedBy": ["1"]}
```
""",
    description="Update a task in the task list.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    is_enabled=is_todo_v2_enabled,
)


def _task_output_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    task_id = tool_input.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolInputError("task_id must be a non-empty string")

    # Background Bash commands (spawned by ``Bash`` with ``run_in_background:
    # true``) register themselves on ``context.background_bash_tasks``. Check
    # that registry first so ``TaskOutput`` doubles as the polling tool for
    # long-running shell commands, matching
    # ``typescript/src/tools/BashTool/BashTool.tsx``.
    bg_tasks = getattr(context, "background_bash_tasks", None) or {}
    if task_id in bg_tasks:
        from src.tool_system.tools.bash.background import read_background_output

        snapshot = read_background_output(context, task_id)
        if snapshot is None:
            return ToolResult(
                name="TaskOutput",
                output={"retrieval_status": "success", "task": None},
            )
        return ToolResult(
            name="TaskOutput",
            output={
                "retrieval_status": "success",
                "task": {
                    "task_id": task_id,
                    "task_type": "bash_background",
                    "status": snapshot["status"],
                    "exit_code": snapshot["exit_code"],
                    "command": snapshot["command"],
                    "description": snapshot["description"],
                    "output": snapshot["output"],
                    "truncated": snapshot["truncated"],
                    "pid": snapshot["pid"],
                    "started_at": snapshot["started_at"],
                    "finished_at": snapshot["finished_at"],
                },
            },
        )

    task = context.tasks.get(task_id)
    if task is None:
        return ToolResult(name="TaskOutput", output={"retrieval_status": "success", "task": None})

    output = str(task.get("output") or "")
    retrieval_status = "success" if output else "not_ready"
    return ToolResult(
        name="TaskOutput",
        output={
            "retrieval_status": retrieval_status,
            "task": {
                "task_id": task_id,
                "task_type": "task_list",
                "status": task.get("status"),
                "description": task.get("description"),
                "output": output,
            },
        },
    )


TaskOutputTool: Tool = build_tool(
    name="TaskOutput",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string"},
            "block": {"type": "boolean"},
            "timeout": {"type": "number"},
        },
        "required": ["task_id"],
    },
    call=_task_output_call,
    prompt="""\
Get the output of a running or completed background task.

- Takes a task_id parameter identifying the task to get output for
- Returns the task status and any available output
- Use this tool to check on the progress or results of background tasks
""",
    description="Get output for a background task.",
    aliases=("AgentOutputTool", "BashOutputTool"),
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)

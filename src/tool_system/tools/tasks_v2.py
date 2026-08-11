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
# Auto-mode classifier helpers — mirror TS Task{Create,Get,Update,Output}
# ``toAutoClassifierInput``. The classifier never sees the full task body;
# it sees a compact identity/intent line so any future LLM classifier can
# focus on shape over content.
# ---------------------------------------------------------------------------


def _task_create_classifier_input(input_data: dict) -> str:
    return (input_data or {}).get("subject", "") or ""


def _task_get_classifier_input(input_data: dict) -> str:
    return (input_data or {}).get("taskId", "") or ""


def _task_update_classifier_input(input_data: dict) -> str:
    d = input_data or {}
    parts: list[str] = []
    tid = d.get("taskId")
    if tid:
        parts.append(str(tid))
    status = d.get("status")
    if status:
        parts.append(str(status))
    subject = d.get("subject")
    if subject:
        parts.append(str(subject))
    return " ".join(parts)


def _task_output_classifier_input(input_data: dict) -> str:
    return (input_data or {}).get("task_id", "") or ""


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
    to_auto_classifier_input=_task_create_classifier_input,
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
    to_auto_classifier_input=_task_get_classifier_input,
)


def _task_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    # FOLLOW-UP (chapter-10 / Chunk B / critic concern C2): this filter
    # was scoped out of WI-1.5 (which only migrated ``_task_output_call``).
    # Post-WI-1.5 ``context.tasks`` no longer hosts runtime-agent entries
    # (those moved to ``context.runtime_tasks``), so
    # ``metadata._internal=True`` should never appear here in steady
    # state. Keep the filter as defense-in-depth against legacy
    # transcript replays that might introduce such an entry; **drop in a
    # Phase-2-or-later one-line cleanup** once we have confidence no
    # such transcripts remain in CI fixtures.
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
    to_auto_classifier_input=_task_update_classifier_input,
)


# A stall must survive this many *additional* blocking polls past the first
# (which only records the baseline) before we nudge. So the nudge fires on the
# 4th identical poll — ~2 min at the 30s default — paired with a wall-clock
# floor below so a model using tiny timeouts can't trip it in seconds.
_STUCK_POLL_THRESHOLD = 3
# The stall must also have persisted at least this long in wall-clock, so
# ``block=True, timeout=1000`` (1s polls) cannot fire the guard on a job that
# has barely started.
_STUCK_MIN_WALL_SECONDS = 60.0


def _bg_output_size(entry: dict[str, Any]) -> int:
    """Monotonic byte size of a background task's log, or -1 if unknowable.

    The FILE size is the progress signal, deliberately not the length of the
    ``output`` string the poll returned: ``read_background_output`` caps that
    at the last 200KB and re-seeks, so a chatty, actively-progressing job's
    reported output plateaus near the cap and its length goes constant even as
    the job writes. Length would then read as "stuck" and falsely nudge it.
    The on-disk size keeps growing past the cap, so it stays a true progress
    signal for high-volume jobs. (Critic M1, 2026-07-27.)
    """
    path = entry.get("output_path")
    if not path:
        return -1
    try:
        from pathlib import Path as _P

        return _P(str(path)).stat().st_size
    except OSError:
        return -1


def _guard_repeated_polls(
    task_id: str,
    result: ToolResult,
    context: ToolContext,
) -> ToolResult:
    """Nudge the model to stop when it keeps polling a stalled background task.

    A background command that cannot finish on its own — an unbounded search,
    a brute-force, a server with no timeout — turns ``TaskOutput`` into an
    absorbing state: the model polls, gets "still running, no new output",
    polls again, and never reconsiders. On terminal-bench 2.1 crack-7z-hash
    (deepseek-v4-pro, 2026-07-27) an agent launched an incremental
    john-the-ripper brute-force in the background, then spent its final 20
    minutes and ~15 consecutive polls waiting on it until the harness killed
    the trial at 30 minutes. opus-5 finished the same task in 18 seconds.

    Model-agnostic and tool-level. For each background bash task, count
    consecutive *blocking* polls (``retrieval_status="timeout"``, task still
    running) whose on-disk log size did not grow. Once that count reaches
    ``_STUCK_POLL_THRESHOLD`` AND the stall has lasted ``_STUCK_MIN_WALL_SECONDS``,
    prepend a hint pointing at TaskStop. The hint repeats on every further
    stuck poll, so a model that ignores it once still sees it. Any growth in
    the log resets the counter — a slow but progressing job is never nudged,
    including one whose returned output has saturated the 200KB read window.

    The hint is PREPENDED (not appended) and also exposed as a top-level
    ``stuck_task_hint`` field, so it survives large-result persistence: an
    output over ~50KB is replaced with a disk pointer plus a HEAD preview
    (``content[:max_bytes]``), which would truncate a tail-appended note away.
    (Critic M2, 2026-07-27.)

    Both poll modes count. This is deliberate and load-bearing: on the
    crack-7z-hash trajectory, 13 of the 15 polls were ``block=False`` (which
    returns ``retrieval_status="success"`` on a still-running task, not
    ``"timeout"``). Gating on the timeout status alone — the obvious reading —
    would make the guard inert for the exact failure it exists to catch. The
    signal that matters is "task still running, log not growing", regardless
    of how the model polled. Counting fast ``block=False`` polls is safe only
    because ``_STUCK_MIN_WALL_SECONDS`` gates on elapsed time, not poll count,
    so a burst of instant non-blocking polls cannot fire the nudge on a job
    that just started.

    Agent subtasks (``LocalAgent``) are excluded: a "no new output" poll there
    is normal, not stuck.
    """
    import time

    out = result.output
    if not isinstance(out, dict):
        return result
    task = out.get("task")
    if not isinstance(task, dict):
        return result
    if task.get("task_type") != "bash_background":
        return result

    entry = context.background_bash_tasks.get(task_id)
    if not isinstance(entry, dict):
        return result

    # A terminal task needs no nudge — the model will stop polling naturally.
    # Reset so a later stall on a reused id starts fresh. Any non-running
    # status lands here regardless of retrieval_status.
    if task.get("status") != "running":
        entry["_stuck_polls"] = 0
        entry.pop("_stuck_since", None)
        return result

    cur_size = _bg_output_size(entry)
    prev_size = entry.get("_stuck_last_size")
    if prev_size is not None and cur_size == prev_size:
        entry["_stuck_polls"] = int(entry.get("_stuck_polls", 0)) + 1
        entry.setdefault("_stuck_since", time.monotonic())
    else:
        entry["_stuck_polls"] = 0
        entry["_stuck_last_size"] = cur_size
        entry.pop("_stuck_since", None)

    stalled_for = time.monotonic() - entry.get("_stuck_since", time.monotonic())
    if (
        entry.get("_stuck_polls", 0) < _STUCK_POLL_THRESHOLD
        or stalled_for < _STUCK_MIN_WALL_SECONDS
    ):
        return result

    polls = entry["_stuck_polls"] + 1  # +1: the first poll set the baseline
    hint = (
        f"[stuck-task guard] This background task has been polled {polls} "
        f"times with no new output for ~{stalled_for / 60:.0f} min and is "
        f"still running. If it may not terminate on its own — an unbounded "
        f"search, a brute-force, a wait for something that will not arrive — "
        f"stop it with TaskStop(task_id={task_id!r}) and take a different "
        f"approach instead of polling again. If it is genuinely making slow "
        f"progress, keep waiting."
    )
    # Copy so the stored snapshot / other readers are untouched. Prepend to the
    # output AND surface a top-level field, so the note lands in the head
    # preview if this result is persisted for size.
    new_task = dict(task)
    new_task["output"] = hint + "\n\n" + str(task.get("output") or "")
    # ``stuck_task_hint`` FIRST so it heads the serialized JSON: a >50KB result
    # is persisted to disk with a head preview (``content[:max_bytes]``), and a
    # leading key survives that where a trailing one would not. The prepend
    # into ``task.output`` is the second line of defense for the in-context
    # (un-persisted) read.
    new_out = {"stuck_task_hint": hint, **out, "task": new_task}
    return ToolResult(name=result.name, output=new_out)


async def _task_output_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    """Return the current state / output of a runtime task or todo.

    Chapter-10 / Chunk D / WI-4.1: full polling semantics. ``block``
    defaults to True; when set, the call polls ``runtime_tasks`` until
    the task reaches a terminal state or the ``timeout`` expires.
    Three ``retrieval_status`` values match TS
    ``TaskOutputTool.tsx:52``:

    * ``"success"`` — task is in a terminal state (chapter-10
      ``completed`` / ``failed`` / ``killed``) when polling stops.
      Also returned for non-terminal tasks when ``block=False``-and-
      task-already-terminal (which collapses to the same case).
    * ``"timeout"`` — ``block=True``, deadline expired, task still
      running. The return body still carries the latest snapshot so
      callers see the partial output collected so far.
    * ``"not_ready"`` — ``block=False`` and task is non-terminal.

    The function is ``async def`` so the polling loop can ``await
    asyncio.sleep`` instead of busy-waiting. The dispatch layer (Chunk
    D / WI-4.0) handles sync-vs-async at the registry level — calls
    from sync contexts (test fixtures, tools dispatched from tools)
    still work transparently.
    """
    task_id = tool_input.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolInputError("task_id must be a non-empty string")
    task_id = task_id.strip()

    block = bool(tool_input.get("block", True))
    timeout_ms = tool_input.get("timeout")
    if timeout_ms is None:
        timeout_ms = 30_000  # default per TS TaskOutputTool.tsx:33
    try:
        timeout_seconds = float(timeout_ms) / 1000.0
    except (TypeError, ValueError):
        raise ToolInputError("timeout must be a number")

    # Branch 1 — runtime_tasks (chapter-10 source of truth).
    runtime = context.runtime_tasks.get(task_id)
    if runtime is not None:
        if not block:
            result = _runtime_task_to_output(task_id, runtime, context)
        else:
            result = await _poll_runtime_until_terminal(
                task_id, timeout_seconds, context
            )
        return _guard_repeated_polls(task_id, result, context)

    # Branch 2 — TaskCreate / tasks_v2 todos. Same key space, different
    # semantics; no polling — output text is set or not at the moment
    # of the call.
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


async def _poll_runtime_until_terminal(
    task_id: str,
    timeout_seconds: float,
    context: ToolContext,
) -> ToolResult:
    """Block until the runtime task is terminal or the deadline expires.

    Returns the same shape ``_runtime_task_to_output`` produces, but
    sets ``retrieval_status="timeout"`` if the deadline expired with
    the task still running. ``await asyncio.sleep`` is bounded at 250ms
    per tick so cancellation is responsive but the loop doesn't burn
    CPU.

    Respects an abort signal on ``context.abort_controller`` if one is
    set: the abort exits the poll early with the current snapshot. The
    parent's stop signal must propagate.
    """
    import asyncio
    import time

    from src.tasks_core import is_terminal_task_status

    deadline = time.monotonic() + timeout_seconds
    poll_interval = 0.25
    while True:
        runtime = context.runtime_tasks.get(task_id)
        if runtime is None:
            # Task evicted mid-poll — treat as success/None like the
            # not-found branch above.
            return ToolResult(
                name="TaskOutput",
                output={"retrieval_status": "success", "task": None},
            )
        if is_terminal_task_status(runtime.status):
            return _runtime_task_to_output(task_id, runtime, context)

        # Abort fast-path — if the parent's controller is signalled,
        # exit with the current snapshot rather than waiting out the
        # remaining timeout. ``abort_controller`` is non-optional on
        # ``ToolContext`` so the historical truthiness/getattr indirection
        # is gone.
        if context.abort_controller.signal.aborted:
            return _runtime_task_to_output(task_id, runtime, context)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Timed out — return the running snapshot but flag it.
            snapshot = _runtime_task_to_output(task_id, runtime, context)
            timed_out = dict(snapshot.output) if isinstance(snapshot.output, dict) else {}
            timed_out["retrieval_status"] = "timeout"
            return ToolResult(name="TaskOutput", output=timed_out)
        await asyncio.sleep(min(poll_interval, max(remaining, 0.01)))


def _runtime_task_to_output(
    task_id: str,
    runtime: Any,
    context: ToolContext,
) -> ToolResult:
    """Project a ``runtime_tasks`` entry into the model-facing output shape."""
    # Local imports defer the cycle.
    from src.tasks.local_agent import LocalAgentTaskState
    from src.tasks.local_shell import LocalShellTaskState
    from src.tasks_core import is_terminal_task_status

    if isinstance(runtime, LocalShellTaskState):
        # Reuse the existing rich snapshot helper (combined output capture,
        # exit-code marker stripping, etc.) so behavior is unchanged.
        from src.tool_system.tools.bash.background import read_background_output

        snapshot = read_background_output(context, task_id)
        if snapshot is None:
            return ToolResult(
                name="TaskOutput",
                output={"retrieval_status": "success", "task": None},
            )
        # The on-disk log path, so ``_format_task_output`` can name the file
        # that still holds everything when it trims to the tail (TS reads the
        # same thing from ``getTaskOutputPath``). Not part of the snapshot
        # helper's contract, so read it off the task entry directly.
        entry = context.background_bash_tasks.get(task_id)
        output_path = entry.get("output_path") if isinstance(entry, dict) else None
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
                    "output_path": output_path,
                    "truncated": snapshot["truncated"],
                    "pid": snapshot["pid"],
                    "started_at": snapshot["started_at"],
                    "finished_at": snapshot["finished_at"],
                },
            },
        )

    if isinstance(runtime, LocalAgentTaskState):
        text = runtime.result_text or ""
        if is_terminal_task_status(runtime.status):
            retrieval_status = "success"
        else:
            retrieval_status = "not_ready"
        return ToolResult(
            name="TaskOutput",
            output={
                "retrieval_status": retrieval_status,
                "task": {
                    "task_id": task_id,
                    "task_type": "local_agent",
                    "status": runtime.status,
                    "description": runtime.description,
                    "agent_type": runtime.agent_type,
                    "output": text,
                    # So an over-32K subagent answer gets a truncation header
                    # that names a real file. TS passes getTaskOutputPath(id)
                    # unconditionally; without this the header degraded to
                    # "the task's output file", which the model cannot act on.
                    "output_path": runtime.output_file,
                    # TS' getTaskOutputData sets this for local_agent
                    # (TaskOutputTool.tsx:104) and the mapper emits <error>.
                    # Without it here that part was unreachable: a genuinely
                    # failed subagent surfaced nothing but its status.
                    "error": runtime.error,
                },
            },
        )

    # Unknown TaskStateBase subclass — surface what we have. Future task
    # types can extend this dispatch without touching the readers above.
    return ToolResult(
        name="TaskOutput",
        output={
            "retrieval_status": "success",
            "task": {
                "task_id": task_id,
                "task_type": runtime.type,
                "status": runtime.status,
                "description": runtime.description,
                "output": "",
            },
        },
    )


#: Port of TS ``TASK_MAX_OUTPUT_DEFAULT`` / ``TASK_MAX_OUTPUT_UPPER_LIMIT``
#: (typescript/src/utils/task/outputFormatting.ts:4-5).
_TASK_MAX_OUTPUT_DEFAULT = 32_000
_TASK_MAX_OUTPUT_UPPER_LIMIT = 160_000


def _max_task_output_length() -> int:
    """Port of ``getMaxTaskOutputLength`` — ``TASK_MAX_OUTPUT_LENGTH``, bounded.

    Mirrors ``validateBoundedIntEnvVar`` (typescript/src/utils/envValidation.ts:12):
    unset / unparseable / non-positive falls back to the default, and anything
    above the ceiling is clamped to it.
    """
    import os

    raw = os.environ.get("TASK_MAX_OUTPUT_LENGTH")
    if not raw:
        return _TASK_MAX_OUTPUT_DEFAULT
    try:
        parsed = int(raw, 10)
    except (TypeError, ValueError):
        return _TASK_MAX_OUTPUT_DEFAULT
    if parsed <= 0:
        return _TASK_MAX_OUTPUT_DEFAULT
    return min(parsed, _TASK_MAX_OUTPUT_UPPER_LIMIT)


def _format_task_output(text: str, output_path: Any) -> str:
    """Bound the captured output for the wire. Port of ``formatTaskOutput``
    (typescript/src/utils/task/outputFormatting.ts:22-38).

    Keeps the TAIL — for a build or a test run the failure is at the end — and
    leads with a header naming the file that still holds everything, so the
    model can ``Read`` the rest.

    The ceiling stays TS' 160,000 rather than the 50K persistence threshold, so
    a caller who raises ``TASK_MAX_OUTPUT_LENGTH`` past ~49.7K is back on the
    wrapper path. That is deliberate: clamping to the persistence constant
    would couple two unrelated modules, and the clients' defensive parse
    (``readOutputTag``) is the right backstop for it.

    This is NOT a redundant third bound on top of the ~200KB read window and
    the 50K persistence threshold, which is how an earlier revision of this
    change read it. It is the bound that keeps the whole tagged result *under*
    the persistence threshold, and that is load-bearing: over that threshold
    ``maybe_persist_large_tool_result`` replaces the entire content with a
    ``<persisted-output>`` wrapper around a 2KB HEAD preview, which cuts the
    part list mid-``<output>`` — no closing tag, no ``<truncated>``, no
    ``<error>``. Both clients then fail to parse what is left. Measured before
    this was ported: a 55KB docker-build log rendered as "(No output)".
    """
    limit = _max_task_output_length()
    if len(text) <= limit:
        return text
    path = str(output_path) if output_path else "the task's output file"
    header = f"[Truncated. Full output: {path}]\n\n"
    available = limit - len(header)
    if available <= 0:
        # DEVIATION from TS, deliberate. The reference computes
        # ``output.slice(-availableSpace)`` with no guard, and a non-positive
        # ``availableSpace`` silently inverts: ``slice(-0)`` is ``slice(0)``
        # and ``slice(59)`` counts from the FRONT, so the whole log comes back
        # and the bound is gone. Reachable here with a small
        # ``TASK_MAX_OUTPUT_LENGTH`` (measured: limit=1 returned 100,001
        # chars), or a pathologically long path. Replicating that would defeat
        # the one bound that keeps this result under the persistence threshold
        # — the entire point of the port. When there is no room for a body,
        # the pointer to the full file is the more useful half; keep it.
        return header.rstrip()
    return header + text[-available:]


def _task_output_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Serialize a TaskOutput result the way TS ``TaskOutputTool`` does.

    Port of ``mapToolResultToToolResultBlockParam``
    (typescript/src/tools/TaskOutputTool/TaskOutputTool.tsx:283-308): a short
    list of ``<tag>value</tag>`` parts joined by blank lines, with the task's
    output in its own ``<output>`` block.

    Without this the tool fell through to ``_default_map_result_to_api``,
    which ``json.dumps``es the whole result dict. That put the captured
    output on the wire as a JSON *string literal* — every newline escaped to
    a literal ``\\n``, wrapped in one unbroken line alongside ``pid`` /
    ``started_at`` / ``finished_at`` bookkeeping the model has no use for. A
    build log read that way is near-unparseable for the model AND for the
    human: the transcript renders the tool_result content, so the same blob
    was what the TUI printed under the ``TaskOutput`` row.

    Two deliberate additions over the TS part list:

    * ``<stuck_task_hint>`` leads when ``_guard_repeated_polls`` set it. That
      guard documents the top-level field as its primary channel precisely
      because a >50KB result is persisted to disk and replaced by a 2KB HEAD
      preview — a leading part survives that truncation, a trailing one does
      not. Emitting it first preserves the guarantee under the new format.
    * ``<truncated>`` when the ~200KB capture window clipped the output, so
      "this is not the whole log" stays on the wire. TS has no analog: its only
      truncation is ``formatTaskOutput``, which announces itself inline with a
      ``[Truncated. Full output: …]`` header. We now emit that header too, so
      the two signals are complementary — the header means "the wire copy is
      the tail", the tag means "even the captured copy is short of the real
      log".

    ``<description>`` is likewise ours. TS omits it because its renderer holds
    the live task object and reads the field straight off it; here the client
    is a separate process whose only view of the result is this content, and
    the ``task_list`` task type (a TaskCreate todo — TS serves those from
    TaskGet, not TaskOutput) renders as "description [status]", which is
    nothing at all without it.

    Output truncation goes through ``_format_task_output`` — see there for why
    porting TS' 32K cap is required rather than redundant.
    """
    if not isinstance(output, dict):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": output if isinstance(output, str) else str(output),
        }

    def tag(name: str, value: Any) -> str:
        return f"<{name}>{value}</{name}>"

    parts: list[str] = []

    def add(name: str, value: Any) -> None:
        """Append a part, skipping None. TS is type-guaranteed on these
        fields; a plain dict here is not, and ``<status>None</status>`` reads
        to the model as a status literally named None."""
        if value is not None:
            parts.append(tag(name, value))

    hint = output.get("stuck_task_hint")
    if isinstance(hint, str) and hint.strip():
        parts.append(tag("stuck_task_hint", hint))

    add("retrieval_status", output.get("retrieval_status"))

    task = output.get("task")
    if isinstance(task, dict):
        add("task_id", task.get("task_id"))
        add("task_type", task.get("task_type"))
        add("status", task.get("status"))

        description = task.get("description")
        if description:
            add("description", description)

        exit_code = task.get("exit_code")
        if exit_code is not None:
            add("exit_code", exit_code)

        if task.get("truncated"):
            add("truncated", "true")

        error = task.get("error")
        if error:
            add("error", error)

        # ``<output>`` LAST — deliberately after ``<error>``, where TS puts it
        # first. It is the only unbounded part, so trailing it means a head
        # truncation (the persistence wrapper's 2KB preview, which
        # `_format_task_output` now makes rare but the per-message aggregate
        # budget can still trigger) can only ever eat log lines, never the
        # metadata that says what happened. In the previous revision
        # ``<truncated>``/``<error>`` sat after ``<output>`` and were
        # therefore unreachable: `truncated` is only ever set for a >200KB
        # log, which is exactly the case that gets truncated away.
        text = task.get("output")
        if isinstance(text, str) and text.strip():
            # ``rstrip`` not ``strip``, matching TS' ``trimEnd()``: leading
            # indentation on the first output line is real content. The
            # newlines around it are delimiters the clients strip back off.
            body = _format_task_output(text.rstrip(), task.get("output_path"))
            parts.append(f"<output>\n{body}\n</output>")

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": "\n\n".join(parts),
    }


TaskOutputTool: Tool = build_tool(
    name="TaskOutput",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string"},
            "block": {
                "type": "boolean",
                "default": True,
                "description": "Whether to wait for task completion (default: true)",
            },
            # Chapter-10 / WI-4.1 schema bounds (mirrors TS
            # TaskOutputTool.tsx:33 ``z.number().min(0).max(600000)``).
            "timeout": {
                "type": "number",
                "minimum": 0,
                "maximum": 600000,
                "default": 30000,
                "description": "Max wait time in ms (default 30000; max 600000)",
            },
        },
        "required": ["task_id"],
    },
    call=_task_output_call,
    map_result_to_api=_task_output_map_result_to_api,
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
    to_auto_classifier_input=_task_output_classifier_input,
)

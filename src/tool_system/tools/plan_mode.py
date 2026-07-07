"""Plan-mode meta tools: EnterPlanMode / ExitPlanMode.

Faithful port of ``typescript/src/tools/EnterPlanModeTool/`` and
``typescript/src/tools/ExitPlanModeTool/`` (the V2 tool — the plan lives in
the session plan FILE, not in the tool input):

* **EnterPlanMode** — the "implicit plan mode" entry: the model calls it
  proactively for non-trivial implementation tasks. AUTO-ALLOWS (the
  reference's ``buildTool`` defaults ``checkPermissions`` to allow,
  Tool.ts:773-784, and EnterPlanMode defines none — there is no entry
  dialog); ``call()`` transitions the LIVE permission mode to ``plan``,
  stashing ``pre_plan_mode`` first.
* **ExitPlanMode** — the plan-approval gate: ``requires_user_interaction``
  (the ask survives bypassPermissions — the check.py step-1e analog of
  permissions.ts:1231-1237), asks "Exit plan mode?", and the TUI renders the
  plan-approval dialog. On approve the dialog's ``chosen_updates``
  (``setMode`` acceptEdits/default/bypassPermissions) flip the mode BEFORE
  ``call()`` runs; ``call()``'s mode-restore fallback covers hook
  auto-approves that supplied no update.

The permission-mode transition helper rebinds
``context.permission_context`` (the functional-update contract shared with
``registry._apply_and_persist_updates``) and fires
``context.on_permission_mode_change`` so the agent-server can push the new
mode to the TUI footer mid-turn.
"""

from __future__ import annotations

from typing import Any

from src.bootstrap.state import (
    set_has_exited_plan_mode,
    set_needs_plan_mode_exit_attachment,
)
from src.permissions.plan_transitions import transition_permission_mode
from src.permissions.types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionResult,
    PermissionUpdateSetMode,
)
from src.permissions.updates import apply_permission_update
from src.utils.plans import get_plan, get_plan_file_path

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolExecutionError
from ..protocol import ToolResult

ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"


def _set_permission_mode(context: ToolContext, to_mode: str) -> None:
    """Transition the LIVE permission mode from inside a tool.

    Runs the same seam the server controls use (``transition_permission_mode``
    fires the plan enter/exit attachment flags and manages ``pre_plan_mode``),
    applies the ``setMode`` update, rebinds ``context.permission_context``,
    and notifies the mode-change listener when the mode actually changed.
    """
    pc = context.permission_context
    from_mode = pc.mode
    if from_mode == to_mode:
        return
    next_context = transition_permission_mode(from_mode, to_mode, pc)
    next_context = apply_permission_update(
        next_context,
        PermissionUpdateSetMode(type="setMode", destination="session", mode=to_mode),  # type: ignore[arg-type]
    )
    context.permission_context = next_context
    cb = getattr(context, "on_permission_mode_change", None)
    if cb is not None:
        try:
            cb(to_mode)
        except Exception:  # noqa: BLE001 — a UI notification must not fail the tool
            pass


# ---------------------------------------------------------------------------
# EnterPlanMode
# ---------------------------------------------------------------------------

# Verbatim port of getEnterPlanModeToolPromptExternal()
# (typescript/src/tools/EnterPlanModeTool/prompt.ts:4-99, non-interview arm —
# the interview-phase gate is a GrowthBook experiment, default off; not ported).
_WHAT_HAPPENS_SECTION = """## What Happens in Plan Mode

In plan mode, you'll:
1. Thoroughly explore the codebase using Glob, Grep, and Read tools
2. Understand existing patterns and architecture
3. Design an implementation approach
4. Present your plan to the user for approval
5. Use AskUserQuestion if you need to clarify approaches
6. Exit plan mode with ExitPlanMode when ready to implement

"""

ENTER_PLAN_MODE_TOOL_PROMPT = f"""Use this tool proactively when you're about to start a non-trivial implementation task. Getting user sign-off on your approach before writing code prevents wasted effort and ensures alignment. This tool transitions you into plan mode where you can explore the codebase and design an implementation approach for user approval.

## When to Use This Tool

**Prefer using EnterPlanMode** for implementation tasks unless they're simple. Use it when ANY of these conditions apply:

1. **New Feature Implementation**: Adding meaningful new functionality
   - Example: "Add a logout button" - where should it go? What should happen on click?
   - Example: "Add form validation" - what rules? What error messages?

2. **Multiple Valid Approaches**: The task can be solved in several different ways
   - Example: "Add caching to the API" - could use Redis, in-memory, file-based, etc.
   - Example: "Improve performance" - many optimization strategies possible

3. **Code Modifications**: Changes that affect existing behavior or structure
   - Example: "Update the login flow" - what exactly should change?
   - Example: "Refactor this component" - what's the target architecture?

4. **Architectural Decisions**: The task requires choosing between patterns or technologies
   - Example: "Add real-time updates" - WebSockets vs SSE vs polling
   - Example: "Implement state management" - Redux vs Context vs custom solution

5. **Multi-File Changes**: The task will likely touch more than 2-3 files
   - Example: "Refactor the authentication system"
   - Example: "Add a new API endpoint with tests"

6. **Unclear Requirements**: You need to explore before understanding the full scope
   - Example: "Make the app faster" - need to profile and identify bottlenecks
   - Example: "Fix the bug in checkout" - need to investigate root cause

7. **User Preferences Matter**: The implementation could reasonably go multiple ways
   - If you would use AskUserQuestion to clarify the approach, use EnterPlanMode instead
   - Plan mode lets you explore first, then present options with context

## When NOT to Use This Tool

Only skip EnterPlanMode for simple tasks:
- Single-line or few-line fixes (typos, obvious bugs, small tweaks)
- Adding a single function with clear requirements
- Tasks where the user has given very specific, detailed instructions
- Pure research/exploration tasks (use the Agent tool with explore agent instead)

{_WHAT_HAPPENS_SECTION}## Examples

### GOOD - Use EnterPlanMode:
User: "Add user authentication to the app"
- Requires architectural decisions (session vs JWT, where to store tokens, middleware structure)

User: "Optimize the database queries"
- Multiple approaches possible, need to profile first, significant impact

User: "Implement dark mode"
- Architectural decision on theme system, affects many components

User: "Add a delete button to the user profile"
- Seems simple but involves: where to place it, confirmation dialog, API call, error handling, state updates

User: "Update the error handling in the API"
- Affects multiple files, user should approve the approach

### BAD - Don't use EnterPlanMode:
User: "Fix the typo in the README"
- Straightforward, no planning needed

User: "Add a console.log to debug this function"
- Simple, obvious implementation

User: "What files handle routing?"
- Research task, not implementation planning

## Important Notes

- This tool REQUIRES user approval - they must consent to entering plan mode
- If unsure whether to use it, err on the side of planning - it's better to get alignment upfront than to redo work
- Users appreciate being consulted before significant changes are made to their codebase
"""

# EnterPlanModeTool.ts:103-125 (mapToolResultToToolResultBlockParam,
# non-interview arm).
_ENTER_PLAN_MODE_RESULT_INSTRUCTIONS = """

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase."""


def _enter_plan_mode_check_permissions(
    tool_input: dict[str, Any], _context: ToolContext
) -> PermissionResult:
    # TS buildTool defaults checkPermissions to allow (Tool.ts:777-781) and
    # EnterPlanMode defines none → it AUTO-ALLOWS (no entry dialog). The
    # port's tool default is passthrough→ask, so restore the reference
    # behavior explicitly. The mode flip is restrictive-direction and fully
    # visible (transcript line + footer badge), and an ask here would DENY
    # proactive plan entry in headless runs.
    return PermissionAllowDecision(updated_input=tool_input)


def _enter_plan_mode_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.agent_id:
        # EnterPlanModeTool.ts:78-80
        raise ToolExecutionError("EnterPlanMode tool cannot be used in agent contexts")

    # handlePlanModeTransition + prepareContextForPlanMode + setMode — the
    # stash happens HERE, while the mode is still the pre-plan mode
    # (auto-allow means no pre-call setMode ran).
    _set_permission_mode(context, "plan")

    return ToolResult(
        name=ENTER_PLAN_MODE_TOOL_NAME,
        output={
            "message": (
                "Entered plan mode. You should now focus on exploring the "
                "codebase and designing an implementation approach."
            ),
        },
    )


def _enter_plan_mode_map_result(output: Any, tool_use_id: str) -> dict[str, Any]:
    message = ""
    if isinstance(output, dict):
        message = str(output.get("message", ""))
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": f"{message}{_ENTER_PLAN_MODE_RESULT_INSTRUCTIONS}",
    }


EnterPlanModeTool: Tool = build_tool(
    name=ENTER_PLAN_MODE_TOOL_NAME,
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_enter_plan_mode_call,
    prompt=ENTER_PLAN_MODE_TOOL_PROMPT,
    description="Requests permission to enter plan mode for complex tasks requiring exploration and design",
    search_hint="switch to plan mode to design an approach before coding",
    strict=True,
    should_defer=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    check_permissions=_enter_plan_mode_check_permissions,
    map_result_to_api=_enter_plan_mode_map_result,
)


# ---------------------------------------------------------------------------
# ExitPlanMode
# ---------------------------------------------------------------------------

# Verbatim port of EXIT_PLAN_MODE_V2_TOOL_PROMPT
# (typescript/src/tools/ExitPlanModeTool/prompt.ts:6-29).
EXIT_PLAN_MODE_TOOL_PROMPT = """Use this tool when you are in plan mode and have finished writing your plan to the plan file and are ready for user approval.

## How This Tool Works
- You should have already written your plan to the plan file specified in the plan mode system message
- This tool does NOT take the plan content as a parameter - it will read the plan from the file you wrote
- This tool simply signals that you're done planning and ready for the user to review and approve
- The user will see the contents of your plan file when they review it

## When to Use This Tool
IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task that requires writing code. For research tasks where you're gathering information, searching files, reading files or in general trying to understand the codebase - do NOT use this tool.

## Before Using This Tool
Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use AskUserQuestion first (in earlier phases)
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use AskUserQuestion to ask "Is this plan okay?" or "Should I proceed?" - that's exactly what THIS tool does. ExitPlanMode inherently requests user approval of your plan.

## Examples

1. Initial task: "Search for and understand the implementation of vim mode in the codebase" - Do not use the exit plan mode tool because you are not planning the implementation steps of a task.
2. Initial task: "Help me implement yank mode for vim" - Use the exit plan mode tool after you have finished planning the implementation steps of the task.
3. Initial task: "Add a new feature to handle user authentication" - If unsure about auth method (OAuth, JWT, etc.), use AskUserQuestion first, then use exit plan mode tool after clarifying the approach.
"""

# ExitPlanModeV2Tool.ts:212-217 (validateInput outside plan mode).
_NOT_IN_PLAN_MODE_MESSAGE = (
    "You are not in plan mode. This tool is only for exiting plan mode after "
    "writing a plan. If your plan was already approved, continue with "
    "implementation."
)


def _exit_plan_mode_validate(
    tool_input: dict[str, Any], context: ToolContext
) -> ValidationResult:
    pc = getattr(context, "permission_context", None)
    mode = getattr(pc, "mode", "default") if pc is not None else "default"
    if mode != "plan":
        return ValidationResult(result=False, message=_NOT_IN_PLAN_MODE_MESSAGE, error_code=1)
    return ValidationResult(result=True)


def _exit_plan_mode_check_permissions(
    tool_input: dict[str, Any], _context: ToolContext
) -> PermissionResult:
    # ExitPlanModeV2Tool.ts:233-238 — always confirm with the user. Paired
    # with requires_user_interaction so the ask survives bypassPermissions
    # (check.py:416-426, the permissions.ts step-1e analog).
    return PermissionAskDecision(message="Exit plan mode?", updated_input=tool_input)


def _exit_plan_mode_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    is_agent = bool(context.agent_id)
    file_path = get_plan_file_path(context.agent_id)

    # The dialog may pass an edited plan via updatedInput (CCR/Ctrl+G parity,
    # ExitPlanModeV2Tool.ts:247-261); otherwise the plan is read from disk.
    input_plan = tool_input.get("plan")
    if not isinstance(input_plan, str):
        input_plan = None
    plan = input_plan if input_plan is not None else get_plan(context.agent_id)

    if input_plan is not None:
        # Sync disk so later reads see the edit (ExitPlanModeV2Tool.ts:258-261).
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(input_plan, encoding="utf-8")
        except OSError:
            # logError parity — a failed sync must not fail the approval.
            pass

    # Ensure the mode is changed when exiting plan mode — the fallback for
    # flows where the permission resolution didn't set the mode (e.g. a
    # PermissionRequest hook auto-approve with no updatedPermissions). The
    # dialog path already applied its setMode via chosen_updates BEFORE this
    # runs, so this branch no-ops there (ExitPlanModeV2Tool.ts:318-403).
    pc = context.permission_context
    if pc.mode == "plan":
        restore_mode = pc.pre_plan_mode or "default"
        _set_permission_mode(context, restore_mode)

    # The one-shot exit flags fire on EVERY approved exit. On the dialog path
    # TS sets these from the dialog itself; the port's dialog is client-side,
    # so the tool owns them (idempotent booleans — the _set_permission_mode
    # transition above may already have set them on the fallback path).
    set_has_exited_plan_mode(True)
    set_needs_plan_mode_exit_attachment(True)

    has_task_tool = any(
        getattr(t, "name", None) == "Agent" for t in (context.options.tools or [])
    )

    out: dict[str, Any] = {
        "plan": plan,
        "isAgent": is_agent,
        "filePath": str(file_path),
    }
    if has_task_tool:
        out["hasTaskTool"] = True
    if input_plan is not None:
        out["planWasEdited"] = True
    return ToolResult(name=EXIT_PLAN_MODE_TOOL_NAME, output=out)


def _exit_plan_mode_map_result(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Verbatim mapToolResultToToolResultBlockParam (ExitPlanModeV2Tool.ts:419-492).

    (The teammate awaiting-leader-approval branch is not ported — in-process
    teammates are scaffolding in the port; see the design doc §3.8.)
    """
    data = output if isinstance(output, dict) else {}
    plan = data.get("plan")
    file_path = data.get("filePath")
    is_agent = bool(data.get("isAgent"))
    has_task_tool = bool(data.get("hasTaskTool"))
    plan_was_edited = bool(data.get("planWasEdited"))

    if is_agent:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": (
                "User has approved the plan. There is nothing else needed "
                'from you now. Please respond with "ok"'
            ),
        }

    if not plan or not str(plan).strip():
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": "User has approved exiting plan mode. You can now proceed.",
        }

    team_hint = (
        "\n\nIf this plan can be broken down into multiple independent tasks, "
        "consider using the TeamCreate tool to create a team and parallelize "
        "the work."
        if has_task_tool
        else ""
    )
    plan_label = "Approved Plan (edited by user)" if plan_was_edited else "Approved Plan"

    content = f"""User has approved your plan. You can now start coding. Start with updating your todo list if applicable

Your plan has been saved to: {file_path}
You can refer back to it if needed during implementation.{team_hint}

## {plan_label}:
{plan}"""
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


ExitPlanModeTool: Tool = build_tool(
    name=EXIT_PLAN_MODE_TOOL_NAME,
    input_schema={
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "allowedPrompts": {
                "type": "array",
                "description": (
                    "Prompt-based permissions needed to implement the plan. "
                    "These describe categories of actions rather than "
                    "specific commands."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tool": {"type": "string", "enum": ["Bash"]},
                        "prompt": {"type": "string"},
                    },
                    "required": ["tool", "prompt"],
                },
            },
            # Injected by the approval dialog when the user edited the plan
            # (normalizeToolInput/CCR parity) — normally absent; the plan is
            # read from disk.
            "plan": {"type": "string"},
        },
    },
    call=_exit_plan_mode_call,
    prompt=EXIT_PLAN_MODE_TOOL_PROMPT,
    description="Prompts the user to exit plan mode and start coding",
    search_hint="present plan for approval and start coding (plan mode only)",
    should_defer=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,  # writes the plan file on edited input
    is_concurrency_safe=lambda _input: True,
    validate_input=_exit_plan_mode_validate,
    check_permissions=_exit_plan_mode_check_permissions,
    requires_user_interaction=lambda: True,
    map_result_to_api=_exit_plan_mode_map_result,
    # Surface the first ~200 chars of the plan so a future classifier can
    # spot prompt-injection text being shipped through plan-mode exit.
    to_auto_classifier_input=lambda input_data: (
        ((input_data or {}).get("plan") or "")[:200]
    ),
)

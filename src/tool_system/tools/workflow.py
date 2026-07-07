"""The Workflow tool — entry point for dynamic multi-agent workflows.

A factory (like ``make_agent_tool``) that captures the tool ``registry`` and
``provider`` so each ``agent()`` in a workflow can spawn a real subagent. The
tool's ``call`` resolves the script, schedules :func:`run_workflow_task` in the
background (the session stays responsive), and returns a handle immediately.

``runner_factory`` is injectable so the engine path is unit-testable with a fake
runner; the default builds a ``LiveAgentRunner`` from the call's ``ToolContext``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from src.agent.constants import WORKFLOW_TOOL_NAME
from src.tasks_core import generate_task_id
from src.workflow.gating import is_workflows_enabled
from src.workflow.launch import run_workflow_task

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult

logger = logging.getLogger(__name__)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "script": {"type": "string", "description": "An inline Python workflow script to run."},
        "name": {"type": "string", "description": "Name of a saved workflow under .clawcodex/workflows."},
        "script_path": {"type": "string", "description": "Path to a workflow script file to run."},
        "args": {"description": "Structured input passed to the script as the `args` global."},
        "resume_from_run_id": {"type": "string", "description": "Resume a prior run by id (same session)."},
    },
    "additionalProperties": True,
}

_PROMPT = (
    "Run a dynamic workflow: a Python script that orchestrates many subagents at "
    "scale, executed in the background. The script defines a top-level `meta = "
    "{'name','description','phases'}` dict and uses injected async globals: "
    "`await agent(prompt, schema=?, label=?, phase=?, model=?)`, "
    "`await parallel([...])`, `await pipeline(items, stage1, stage2, ...)`, `phase(title)`, "
    "`log(msg)`, `args`, and `budget`. Top-level `return` is the result. The "
    "script itself has no filesystem/shell access (agents do the I/O); json/re/math "
    "are available. Pass `script` (inline) or `name`/`script_path`."
)


def resolve_named_workflow(name: str, cwd: Optional[Path]) -> Optional[str]:
    """Resolve a saved workflow ``name`` to its source (project wins over home)."""
    candidates = []
    if cwd is not None:
        candidates.append(Path(cwd) / ".clawcodex" / "workflows" / f"{name}.py")
    candidates.append(Path.home() / ".clawcodex" / "workflows" / f"{name}.py")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _resolve_source(tool_input: dict, cwd: Optional[Path]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(source, error)``."""
    script = tool_input.get("script")
    if isinstance(script, str) and script.strip():
        return script, None
    script_path = tool_input.get("script_path")
    if isinstance(script_path, str) and script_path.strip():
        try:
            return Path(script_path).read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, f"could not read script_path: {exc}"
    name = tool_input.get("name")
    if isinstance(name, str) and name.strip():
        source = resolve_named_workflow(name, cwd)
        if source is None:
            return None, f"no saved workflow named '{name}' under .clawcodex/workflows"
        return source, None
    return None, "provide one of: script, script_path, or name"


def _default_runner_factory(registry: Any, provider: Any) -> Callable[[ToolContext, str], Any]:
    def factory(context: ToolContext, run_id: str) -> Any:
        from src.workflow.runner import LiveAgentRunner

        def resolve(agent_type: str) -> Any:
            from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT
            try:
                from src.agent.agent_definitions import find_agent_by_type
                from src.agent.load_agents_dir import get_agent_definitions_with_overrides

                agents = get_agent_definitions_with_overrides(str(context.cwd or "."))
                found = find_agent_by_type(agents, agent_type)
                return found or GENERAL_PURPOSE_AGENT
            except Exception:
                logger.exception("agent resolution failed; falling back to general-purpose")
                return GENERAL_PURPOSE_AGENT

        # Cap per-agent turns. The subagent default (30) lets an aimless model
        # (e.g. deepseek-flash) loop WebSearch/WebFetch ~30 times without ever
        # finalizing via StructuredOutput — observed as agents "stuck" for
        # minutes and burning ~30x the tokens. Workflow agents do focused tasks
        # (search a few sources → emit; verify → emit; synthesize → write), so a
        # tighter bound forces progress. Disciplined models finish well under it.
        # Env-tunable for workflows that genuinely need deeper agents.
        import os

        max_turns = int(os.environ.get("CLAWCODEX_WORKFLOW_MAX_TURNS", "18"))

        return LiveAgentRunner(
            provider=provider,
            tool_registry=registry,
            parent_context=context,
            base_tools=list(registry.list_tools()),
            resolve_agent=resolve,
            run_id=run_id,
            max_turns=max_turns,
        )

    return factory


def make_workflow_tool(
    registry: Any,
    provider: Any = None,
    *,
    runner_factory: Optional[Callable[[ToolContext, str], Any]] = None,
) -> Tool:
    factory = runner_factory or _default_runner_factory(registry, provider)

    async def _call(tool_input: dict, context: ToolContext) -> ToolResult:
        if not is_workflows_enabled():
            return ToolResult(name=WORKFLOW_TOOL_NAME, output={"error": "dynamic workflows are disabled"}, is_error=True)

        source, error = _resolve_source(tool_input, context.cwd)
        if source is None:
            return ToolResult(name=WORKFLOW_TOOL_NAME, output={"error": error}, is_error=True)

        run_id = "wf_" + uuid.uuid4().hex[:12]
        task_id = generate_task_id("local_workflow")
        from src.agent.transcript import get_workflow_run_path

        output_file = get_workflow_run_path(run_id)
        runner = factory(context, run_id)

        # Same-session resume: replay the prior run's journal if asked.
        resume = None
        prior_run_id = tool_input.get("resume_from_run_id")
        if isinstance(prior_run_id, str) and prior_run_id.strip():
            from src.workflow.launch import load_journal

            try:
                resume = load_journal(get_workflow_run_path(prior_run_id))
            except ValueError:
                resume = None  # malformed run id

        coro = run_workflow_task(
            source=source,
            runner=runner,
            registry=context.runtime_tasks,
            task_id=task_id,
            run_id=run_id,
            output_file=output_file,
            args=tool_input.get("args"),
            resume=resume,
            tool_use_id=context.agent_id,
        )

        # Launch on a dedicated daemon thread that owns the run to completion.
        # The production dispatch path executes this tool's ``call`` inside a
        # throwaway ``asyncio.run`` loop (on a worker thread), so scheduling on
        # the *current* loop would be torn down the instant we return the handle
        # — the background run must outlive this call. ``task_manager.start``
        # invokes ``target(stop_event)``, hence the ``_stop`` parameter.
        context.task_manager.start(
            name=f"workflow:{run_id}",
            target=lambda _stop: asyncio.run(coro),
        )

        return ToolResult(
            name=WORKFLOW_TOOL_NAME,
            output={"status": "workflow_launched", "run_id": run_id, "task_id": task_id},
        )

    return build_tool(
        name=WORKFLOW_TOOL_NAME,
        input_schema=_INPUT_SCHEMA,
        call=_call,
        prompt=_PROMPT,
        description="Run a dynamic multi-agent workflow script in the background.",
        is_enabled=is_workflows_enabled,
        is_read_only=lambda _input: True,  # subagents carry their own permissions
        is_concurrency_safe=lambda _input: True,
        max_result_size_chars=10_000,
    )

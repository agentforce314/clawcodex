"""The workflow runtime: binds the orchestration primitives to per-run state
and executes a script end-to-end.

``run_workflow`` is the public entry point. It extracts ``meta`` (pre-flight),
builds a :class:`WorkflowRun` holding the scheduler / budget / journal /
progress / abort controller, injects the primitives into the sandbox namespace,
and runs the script. A script-level exception is captured into
``WorkflowResult.error`` (the run ends); a bad ``meta`` raises before the run
starts.

Resume keys come from :mod:`src.workflow.callpath` (deterministic call-paths),
so caching is correct even under concurrent multi-round fan-out.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from src.utils.abort_controller import (
    AbortController,
    AbortError,
    create_abort_controller,
    create_child_abort_controller,
)

from .budget import Budget
from .callpath import CallKey, current_branch, key_to_str, reset_branch, use_branch
from .constants import MAX_ITEMS_PER_CALL
from .errors import WorkflowError, WorkflowMetaError
from .journal import MISS, Journal, JournalRecord
from .primitives import await_item, run_stage
from .progress import WorkflowProgress
from .sandbox import execute_workflow, extract_meta
from .scheduler import Scheduler
from .types import AgentOutcome, AgentRunner, AgentSpec, WorkflowMeta


@dataclass
class WorkflowResult:
    meta: WorkflowMeta
    value: Any = None
    error: Optional[str] = None
    progress: Optional[WorkflowProgress] = None
    journal: dict[CallKey, JournalRecord] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class WorkflowRun:
    """Owns the state for one running script and implements the primitives."""

    def __init__(
        self,
        *,
        meta: WorkflowMeta,
        runner: AgentRunner,
        args: Any,
        run_id: str,
        scheduler: Scheduler,
        budget: Budget,
        journal: Journal,
        progress: WorkflowProgress,
        controller: AbortController,
        base_path: CallKey = (),
        resolve_workflow: Optional[Callable[[str], str]] = None,
        depth: int = 0,
    ) -> None:
        self._meta = meta
        self._runner = runner
        self._args = args
        self._run_id = run_id
        self._scheduler = scheduler
        self._budget = budget
        self._journal = journal
        self._progress = progress
        self._controller = controller
        self._base_path = base_path
        self._resolve_workflow = resolve_workflow
        self._depth = depth
        self._display = 0
        # S2: per-agent child controllers, keyed by call-path, reachable so the
        # task layer can stop/retry one agent without aborting the whole run.
        self._agent_controllers: dict[CallKey, AbortController] = {}

    @property
    def controller(self) -> AbortController:
        return self._controller

    def abort_agent(self, key: CallKey) -> bool:
        """Abort one in-flight agent by its call-path key. Returns whether a
        live controller was found."""
        controller = self._agent_controllers.get(key)
        if controller is None:
            return False
        controller.abort("agent_stopped")
        return True

    def namespace(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "parallel": self.parallel,
            "pipeline": self.pipeline,
            "phase": self.phase,
            "log": self.log,
            "workflow": self.workflow,
            "budget": self._budget,
        }

    # ── primitives ───────────────────────────────────────────────────────────

    async def agent(
        self,
        prompt: Any,
        *,
        label: Optional[str] = None,
        phase: Optional[str] = None,
        schema: Optional[Mapping[str, Any]] = None,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
        isolation: Optional[str] = None,
    ) -> Any:
        # Deterministic call-path key (taken synchronously, so it is stable
        # across runs regardless of fan-out timing).
        key = current_branch().path + (current_branch().next_slot(),)
        spec = AgentSpec(
            prompt=str(prompt),
            label=label,
            phase=phase,
            schema=schema,
            model=model,
            agent_type=agent_type,
            isolation=isolation,
        )
        eff_label = label or agent_type or "agent"
        eff_phase = phase or self._progress.current_phase

        cached = self._journal.lookup(key, spec)
        if cached is not MISS:
            record = self._progress.agent_started(self._next_display(), eff_label, eff_phase)
            self._progress.agent_finished(record, status="cached")
            return cached

        # Live calls only: count toward the per-run cap and the budget ceiling.
        self._controller.signal.throw_if_aborted()
        self._scheduler.reserve()
        self._budget.check()

        record = self._progress.agent_started(self._next_display(), eff_label, eff_phase)
        child = create_child_abort_controller(self._controller)
        self._agent_controllers[key] = child
        try:
            async with self._scheduler.slot():
                try:
                    outcome = await self._runner.run(spec, abort=child, index=key_to_str(key))
                except AbortError:
                    self._progress.agent_finished(record, status="failed", error="aborted")
                    raise
                except Exception as exc:  # noqa: BLE001 — a subagent death -> None
                    outcome = AgentOutcome(error=f"{type(exc).__name__}: {exc}")
        finally:
            self._agent_controllers.pop(key, None)

        self._budget.add(outcome.tokens)
        if outcome.error is not None:
            self._progress.agent_finished(record, status="failed", tokens=outcome.tokens, error=outcome.error)
            result: Any = None
        elif outcome.skipped:
            self._progress.agent_finished(record, status="skipped", tokens=outcome.tokens)
            result = None
        else:
            result = outcome.structured if schema is not None else outcome.text
            self._progress.agent_finished(record, status="completed", tokens=outcome.tokens)

        self._journal.record(key, spec, result)
        return result

    async def parallel(self, items) -> list:
        items = list(items)
        if len(items) > MAX_ITEMS_PER_CALL:
            self._close_coroutines(items)
            raise WorkflowError(
                f"parallel() received {len(items)} items; the per-call cap is {MAX_ITEMS_PER_CALL}"
            )
        base = current_branch().path + (current_branch().next_slot(),)

        async def guarded(index: int, item):
            token = use_branch(base + (index,))
            try:
                return await await_item(item)
            except Exception:
                return None  # the barrier never rejects
            finally:
                reset_branch(token)

        return list(await asyncio.gather(*(guarded(i, it) for i, it in enumerate(items))))

    async def pipeline(self, items, *stages) -> list:
        items = list(items)
        if len(items) > MAX_ITEMS_PER_CALL:
            self._close_coroutines(items)
            raise WorkflowError(
                f"pipeline() received {len(items)} items; the per-call cap is {MAX_ITEMS_PER_CALL}"
            )
        base = current_branch().path + (current_branch().next_slot(),)

        async def run_item(index: int, item):
            token = use_branch(base + (index,))
            try:
                prev = item
                for stage in stages:
                    try:
                        prev = await run_stage(stage, prev, item, index)
                    except Exception:
                        return None  # drop this item; siblings continue
                return prev
            finally:
                reset_branch(token)

        return list(await asyncio.gather(*(run_item(i, it) for i, it in enumerate(items))))

    def phase(self, title: str) -> None:
        self._progress.start_phase(str(title))

    def log(self, message: Any) -> None:
        self._progress.log(str(message))

    async def workflow(self, name_or_ref: str, args: Any = None) -> Any:
        if self._depth >= 1:
            raise WorkflowError("workflow() nesting is one level only")
        if self._resolve_workflow is None:
            raise WorkflowError("nested workflows are not available in this run")
        # Consume a slot so the nested run's keys don't collide with siblings.
        slot = current_branch().next_slot()
        source = self._resolve_workflow(name_or_ref)
        sub = await run_workflow(
            source,
            runner=self._runner,
            args=args,
            run_id=f"{self._run_id}/{name_or_ref}",
            resolve_workflow=self._resolve_workflow,
            scheduler=self._scheduler,  # share the concurrency cap
            budget=self._budget,        # share the budget pool
            controller=self._controller,
            base_path=current_branch().path + (slot,),
            _depth=self._depth + 1,
        )
        if not sub.ok:
            raise WorkflowError(f"nested workflow '{name_or_ref}' failed: {sub.error}")
        return sub.value

    # ── internals ──────────────────────────────────────────────────────────
    def _next_display(self) -> int:
        n = self._display
        self._display += 1
        return n

    @staticmethod
    def _close_coroutines(items) -> None:
        for item in items:
            if inspect.iscoroutine(item):
                item.close()


async def run_workflow(
    source: str,
    *,
    runner: AgentRunner,
    args: Any = None,
    run_id: str = "wf",
    on_progress: Optional[Callable[[WorkflowProgress], None]] = None,
    resume: Optional[Mapping[CallKey, JournalRecord]] = None,
    resolve_workflow: Optional[Callable[[str], str]] = None,
    budget_total: Optional[int] = None,
    max_concurrent: Optional[int] = None,
    controller: Optional[AbortController] = None,
    scheduler: Optional[Scheduler] = None,
    budget: Optional[Budget] = None,
    base_path: CallKey = (),
    _depth: int = 0,
) -> WorkflowResult:
    """Run a Python workflow ``source`` to completion.

    Raises :class:`WorkflowMetaError` if the ``meta`` block is missing/invalid
    (pre-flight). A script *runtime* exception is captured into
    ``WorkflowResult.error`` and ends the run gracefully.
    """
    meta = extract_meta(source)

    scheduler = scheduler if scheduler is not None else Scheduler(max_concurrent)
    budget = budget if budget is not None else Budget(budget_total)
    journal = Journal(resume)
    progress = WorkflowProgress(meta.phases, on_change=on_progress)
    controller = controller if controller is not None else create_abort_controller()

    run = WorkflowRun(
        meta=meta,
        runner=runner,
        args=args,
        run_id=run_id,
        scheduler=scheduler,
        budget=budget,
        journal=journal,
        progress=progress,
        controller=controller,
        base_path=base_path,
        resolve_workflow=resolve_workflow,
        depth=_depth,
    )

    # Establish this run's base branch for deterministic call-path keys, and
    # restore the caller's branch afterwards (matters for nested workflow()).
    token = use_branch(base_path)
    value: Any = None
    error: Optional[str] = None
    try:
        value = await execute_workflow(source, run.namespace(), args)
    except WorkflowMetaError:
        raise  # compile error surfaced during exec — treat as pre-flight
    except Exception as exc:  # noqa: BLE001 — any script error ends the run
        error = f"{type(exc).__name__}: {exc}"
    finally:
        reset_branch(token)

    return WorkflowResult(meta=meta, value=value, error=error, progress=progress, journal=journal.records)

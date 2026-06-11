"""The ``/workflows`` modal: a list of runs and a per-run phases→agents detail
view, with stop (``x``) and retry (``r``) wired to the task API.

``format_workflow_detail`` is a pure helper (unit-tested); the screens are
exercised with the Textual pilot harness. Pause (``p``) and save (``s``) are
noted as further work — the engine doesn't pause yet, and save needs the
``.claude/workflows`` write flow.
"""

from __future__ import annotations

from typing import Any, Iterator

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from src.tasks.local_workflow import (
    kill_workflow_task,
    retry_workflow_agent,
    skip_workflow_agent,
)

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


def format_workflow_detail(state: Any) -> list[str]:
    """Render a workflow run's header + phases → agents tree as display lines.

    Delegates to the shared :func:`src.workflow.progress.render_run_lines` so the
    REPL ``/workflows`` command and this TUI dialog show identical rich detail
    (status icons, agent type, tokens, tool count, duration, phase progress).
    """
    from src.workflow.progress import render_run_lines

    return render_run_lines(state)


def _agent_options(state: Any) -> list[SelectOption]:
    from src.workflow.progress import format_tokens

    progress = getattr(state, "progress", None)
    phases = getattr(progress, "phases", None) or []
    options: list[SelectOption] = []
    for phase in phases:
        for agent in getattr(phase, "agents", []) or []:
            stats = f"{format_tokens(agent.tokens)} tok"
            if agent.tool_count:
                stats += f" · {agent.tool_count} tools"
            options.append(
                SelectOption(
                    label=f"{agent.icon} {agent.label}",
                    value=agent.key or "",
                    description=f"{stats} · {phase.title}",
                    disabled=not agent.key,
                )
            )
    return options


class WorkflowDetailScreen(DialogScreen[None]):
    """Phases → agents for one run; x stops an agent, r retries it."""

    footer_hint = "x stop agent · r retry agent · Esc back"
    BINDINGS = [("x", "stop_agent", "Stop agent"), ("r", "retry_agent", "Retry agent")]

    def __init__(self, *, registry: Any, task_id: str) -> None:
        super().__init__()
        self._registry = registry
        self._task_id = task_id
        state = registry.get(task_id)
        self.title_text = f"Workflow · {getattr(state, 'workflow_name', 'run')}"
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        state = self._registry.get(self._task_id)
        yield Static(Text("\n".join(format_workflow_detail(state)), style="none"), markup=False)
        options = _agent_options(state)
        if options:
            self._select = SelectList(options, allow_cancel=True)
            yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def _current_key(self) -> str | None:
        if self._select is None or self._select.current is None:
            return None
        return str(self._select.current.value) or None

    def action_stop_agent(self) -> None:
        key = self._current_key()
        if key:
            skip_workflow_agent(self._task_id, key, self._registry)

    def action_retry_agent(self) -> None:
        key = self._current_key()
        if key:
            retry_workflow_agent(self._task_id, key, self._registry)

    def on_select_list_selection_cancelled(self, _: SelectList.SelectionCancelled) -> None:
        self.dismiss(None)


class WorkflowsScreen(DialogScreen[None]):
    """Modal list of workflow runs. Enter opens detail; x stops the run."""

    title_text = "Workflows"
    footer_hint = "Enter detail · x stop run · Esc close"
    BINDINGS = [("x", "stop_run", "Stop run")]

    def __init__(self, *, registry: Any) -> None:
        super().__init__()
        self._registry = registry
        self._select: SelectList | None = None

    def _runs(self) -> list[Any]:
        try:
            return [t for t in self._registry.all() if getattr(t, "type", None) == "local_workflow"]
        except Exception:
            return []

    def _options(self) -> list[SelectOption]:
        return [
            SelectOption(
                label=f"{getattr(r, 'workflow_name', 'workflow')}  [{r.status}]",
                value=r.id,
                description=getattr(r, "summary", None) or "",
            )
            for r in self._runs()
        ]

    def build_body(self) -> Iterator[Widget]:
        options = self._options()
        if not options:
            yield Static(Text("No workflow runs.", style="dim"), markup=False)
            return
        self._select = SelectList(options, allow_cancel=True)
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        task_id = str(event.option.value)
        if self._registry.get(task_id) is not None:
            self.app.push_screen(WorkflowDetailScreen(registry=self._registry, task_id=task_id))

    def on_select_list_selection_cancelled(self, _: SelectList.SelectionCancelled) -> None:
        self.dismiss(None)

    def action_stop_run(self) -> None:
        if self._select is None or self._select.current is None:
            return
        task_id = str(self._select.current.value)
        kill_workflow_task(task_id, self._registry)
        if self._select is not None:
            self._select.set_options(self._options(), keep_cursor=True)

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
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from src.tasks.local_workflow import (
    kill_workflow_task,
    retry_workflow_agent,
    skip_workflow_agent,
)
from src.workflow.progress import format_agent_line

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
    """Two-pane monitor for one run: Phases (left) ⟷ that phase's agents (right).

    Mirrors the Claude Code /workflows view. ``↑↓`` moves the phase selection and
    the right pane updates live; ``→``/``Tab`` focuses the agents pane (where
    ``x``/``r`` stop/retry the selected agent); ``x`` on the phases pane stops the
    whole workflow; ``Esc`` backs out. The view repaints once a second so progress
    advances live.
    """

    footer_hint = "↑↓ phase · → agents · x stop · r retry · p pause · s save · Esc back"
    BINDINGS = [
        ("x", "stop", "Stop"),
        ("r", "retry", "Retry agent"),
        ("p", "pause", "Pause"),
        ("s", "save", "Save"),
        ("right", "focus_agents", "Agents"),
        ("tab", "focus_agents", "Agents"),
        ("left", "focus_phases", "Phases"),
    ]

    DEFAULT_CSS = """
    WorkflowDetailScreen > #dialog-panel { width: 100; max-width: 96%; height: 90%; }
    WorkflowDetailScreen #dialog-body { height: 1fr; }
    WorkflowDetailScreen #wf-twopane { height: 1fr; }
    WorkflowDetailScreen #wf-phases-pane {
        width: 28;
        border-right: solid $primary-darken-2;
        padding: 0 1 0 0;
    }
    WorkflowDetailScreen #wf-agents-pane { width: 1fr; padding: 0 0 0 1; }
    WorkflowDetailScreen .wf-pane-title { text-style: bold; color: $primary; }
    """

    def __init__(self, *, registry: Any, task_id: str) -> None:
        super().__init__()
        self._registry = registry
        self._task_id = task_id
        state = registry.get(task_id)
        name = getattr(state, "workflow_name", "run")
        self.title_text = f"Workflow · {name}"
        desc = (getattr(state, "description", "") or "").strip()
        progress = getattr(state, "progress", None)
        summary = progress.summary() if progress is not None else ""
        self.subtitle_text = f"{desc}  ·  {summary}".strip(" ·") if desc else summary
        self._phases_list: SelectList | None = None
        self._agents_list: SelectList | None = None
        self._agents_title: Static | None = None

    # ---- data ----
    def _phases(self) -> list[Any]:
        progress = getattr(self._registry.get(self._task_id), "progress", None)
        return list(getattr(progress, "phases", None) or [])

    def _phase_options(self) -> list[SelectOption]:
        out: list[SelectOption] = []
        for i, p in enumerate(self._phases()):
            total = len(p.agents)
            out.append(SelectOption(
                label=f"{i + 1} {p.title}",
                value=str(i),
                description=f"{p.done_count}/{total}" if total else "",
            ))
        return out or [SelectOption(label="(no phases yet)", value="", disabled=True)]

    def _agent_options_for(self, idx: int) -> list[SelectOption]:
        phases = self._phases()
        if not (0 <= idx < len(phases)):
            return []
        return [
            SelectOption(label=format_agent_line(a, indent=""), value=a.key or "", disabled=not a.key)
            for a in phases[idx].agents
        ]

    def _agents_header(self, idx: int) -> str:
        phases = self._phases()
        if 0 <= idx < len(phases):
            return f"{phases[idx].title} · {len(phases[idx].agents)} agents"
        return "Agents"

    # ---- composition ----
    def build_body(self) -> Iterator[Widget]:
        self._phases_list = SelectList(self._phase_options(), allow_cancel=True)
        self._agents_title = Static(
            Text(self._agents_header(0)), markup=False, classes="wf-pane-title"
        )
        self._agents_list = SelectList(self._agent_options_for(0), allow_cancel=True)
        yield Horizontal(
            Vertical(
                Static(Text("Phases"), markup=False, classes="wf-pane-title"),
                self._phases_list,
                id="wf-phases-pane",
            ),
            Vertical(self._agents_title, self._agents_list, id="wf-agents-pane"),
            id="wf-twopane",
        )

    def _post_mount(self) -> None:
        if self._phases_list is not None:
            self._phases_list.focus()
        self.set_interval(1.0, self._refresh)

    # ---- live sync ----
    def _current_phase_idx(self) -> int:
        if self._phases_list is None or self._phases_list.current is None:
            return 0
        try:
            return int(self._phases_list.current.value)
        except (TypeError, ValueError):
            return 0

    def _sync_agents(self) -> None:
        idx = self._current_phase_idx()
        if self._agents_list is not None:
            self._agents_list.set_options(self._agent_options_for(idx), keep_cursor=True)
        if self._agents_title is not None:
            self._agents_title.update(Text(self._agents_header(idx)))

    def _refresh(self) -> None:
        if self._phases_list is not None:
            self._phases_list.set_options(self._phase_options(), keep_cursor=True)
        self._sync_agents()

    def on_select_list_option_highlighted(self, _: SelectList.OptionHighlighted) -> None:
        # Right pane tracks the LEFT (phase) selection; ignore highlight events
        # from the agents pane so navigating agents doesn't rebuild itself.
        if self.focused is self._phases_list:
            self._sync_agents()

    def on_select_list_selection_cancelled(self, _: SelectList.SelectionCancelled) -> None:
        self.dismiss(None)

    # ---- focus ----
    def action_focus_agents(self) -> None:
        if self._agents_list is not None and self._agents_list.options:
            self._agents_list.focus()

    def action_focus_phases(self) -> None:
        if self._phases_list is not None:
            self._phases_list.focus()

    # ---- control ----
    def _current_agent_key(self) -> str | None:
        if self._agents_list is None or self._agents_list.current is None:
            return None
        return str(self._agents_list.current.value) or None

    def action_stop(self) -> None:
        # On the agents pane, x stops the selected agent; otherwise the whole run.
        if self.focused is self._agents_list:
            key = self._current_agent_key()
            if key:
                skip_workflow_agent(self._task_id, key, self._registry)
                self._refresh()
                return
        kill_workflow_task(self._task_id, self._registry)
        self._refresh()

    def action_retry(self) -> None:
        key = self._current_agent_key()
        if key:
            retry_workflow_agent(self._task_id, key, self._registry)
            self._refresh()

    def action_pause(self) -> None:
        self.app.notify("Pause isn't supported by the engine yet.", severity="warning")

    def action_save(self) -> None:
        self.app.notify("Saving a run as a reusable workflow isn't supported yet.", severity="warning")


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

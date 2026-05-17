"""Public Python API for autonomous orchestration.

Top-level entry point for the orchestration subsystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..orchestrator.agent_runner import AgentRunner
from ..orchestrator.config.schema import WorkflowConfig
from ..orchestrator.linear.adapter import LinearAdapter
from ..orchestrator.orchestrator import Orchestrator
from ..orchestrator.status_dashboard import StatusDashboard
from ..orchestrator.tracker import TrackerAdapter
from ..orchestrator.workspace import WorkspaceConfig, WorkspaceManager

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationSubsystem:
    """Autonomous mode orchestration engine.

    Replaces Symphony's GenServer-based orchestrator with a Python-native
    async implementation, leveraging ClawCodex's existing infrastructure.
    """

    workflow: WorkflowConfig
    workspace_manager: WorkspaceManager
    linear_adapter: LinearAdapter
    agent_runner: AgentRunner
    status_dashboard: StatusDashboard
    _orchestrator: Orchestrator | None = None

    def __init__(self, workflow_config: WorkflowConfig) -> None:
        self.workflow = workflow_config
        self.workspace_manager = WorkspaceManager(
            WorkspaceConfig(
                root=Path(workflow_config.workspace.root),
                hooks=workflow_config.workspace.hooks,
            )
        )
        self.linear_adapter = LinearAdapter(
            api_key=workflow_config.tracker.api_key or "",
            project_slug=workflow_config.tracker.project_slug,
            endpoint=workflow_config.tracker.endpoint,
            active_states=workflow_config.tracker.active_states,
            assignee=workflow_config.tracker.assignee,
        )
        self.agent_runner = AgentRunner(
            agent_config=workflow_config.agent,
            codex_config=workflow_config.codex,
        )
        self.status_dashboard = StatusDashboard()
        self._orchestrator = None

    async def run(self) -> None:
        """Start polling and issue execution. Runs until cancelled."""
        self._orchestrator = Orchestrator(
            workflow=self.workflow,
            tracker=self.linear_adapter,
            workspace=self.workspace_manager,
            agent_runner=self.agent_runner,
            status_dashboard=self.status_dashboard,
        )
        await self._orchestrator.run()

    async def shutdown(self) -> None:
        """Graceful shutdown — stop polling, clean up workspaces."""
        if self._orchestrator:
            await self._orchestrator.shutdown()

"""Public Python API for autonomous orchestration.

Top-level entry point for the orchestration subsystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..orchestrator.agent_runner import AgentRunner
    from ..orchestrator.orchestrator import Orchestrator

from ..orchestrator.config.schema import WorkflowConfig
from ..orchestrator.status_dashboard import StatusDashboard
from ..orchestrator.tracker import (
    TrackerAdapter,
    create_tracker_adapter,
    repository_clone_url_for_tracker,
)
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
    tracker_adapter: TrackerAdapter
    agent_runner: "AgentRunner"
    status_dashboard: StatusDashboard
    _orchestrator: "Orchestrator | None" = None

    def __init__(self, workflow_config: WorkflowConfig) -> None:
        from ..orchestrator.agent_runner import AgentRunner
        from ..orchestrator.orchestrator import Orchestrator

        self.workflow = workflow_config
        self.workspace_manager = WorkspaceManager(
            WorkspaceConfig(
                root=Path(workflow_config.workspace.root),
                hooks=workflow_config.workspace.hooks,
                repo_clone_url=workflow_config.workspace.repo_clone_url
                or repository_clone_url_for_tracker(workflow_config.tracker),
                clone_depth=workflow_config.workspace.clone_depth,
                checkout_issue_branch=(
                    workflow_config.workspace.checkout_issue_branch
                ),
                git_username=workflow_config.workspace.git_username,
                git_token=workflow_config.workspace.git_token,
                gitignore_patterns=workflow_config.workspace.gitignore_patterns,
                strategy=workflow_config.workspace.strategy,
                base_branch=workflow_config.workspace.base_branch,
                integration_branch=workflow_config.workspace.integration_branch,
                require_clean_start=workflow_config.workspace.require_clean_start,
                require_clean_between_issues=(
                    workflow_config.workspace.require_clean_between_issues
                ),
                preserve_on_terminal=workflow_config.workspace.preserve_on_terminal,
                sequential_lock=workflow_config.workspace.sequential_lock,
            )
        )
        self.tracker_adapter = create_tracker_adapter(workflow_config.tracker)
        self.agent_runner = AgentRunner(
            agent_config=workflow_config.agent,
            codex_config=workflow_config.codex,
        )
        self.status_dashboard = StatusDashboard()
        self._orchestrator = None

    async def run(self) -> None:
        """Start polling and issue execution. Runs until cancelled."""
        from ..orchestrator.orchestrator import Orchestrator

        self._orchestrator = Orchestrator(
            workflow=self.workflow,
            tracker=self.tracker_adapter,
            workspace=self.workspace_manager,
            agent_runner=self.agent_runner,
            status_dashboard=self.status_dashboard,
        )
        await self._orchestrator.run()

    async def shutdown(self) -> None:
        """Graceful shutdown — stop polling, clean up workspaces."""
        if self._orchestrator:
            await self._orchestrator.shutdown()

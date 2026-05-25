"""ProgressReporter - Agent 阶段性进度汇报逻辑处理器。

将 AgentRunner 的 PhaseComplete 事件转换为 ProgressReportTool 调用，
实现检查点触发机制（方式一）与 ProgressReportTool（方式二）的连接。

数据存储在 ToolContext.tasks（方式三），由 ProgressReportTool 完成。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..api.query import PhaseComplete, SessionComplete, TurnComplete

if TYPE_CHECKING:
    from .agent_runner import AgentSession
    from ..tool_system.context import ToolContext

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Handle stage-based progress reporting for agent orchestration.

    Connects AgentRunner events (PhaseComplete) with ProgressReportTool calls.
    This bridge enables the three-combination approach:

        Agent 执行到检查点 (PhaseComplete event)
            ↓
        ProgressReporter.on_event() 拦截事件
            ↓
        调用 ProgressReportTool (via context)
            ↓
        数据存入 ToolContext.tasks.metadata
    """

    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._current_task_id: str | None = None
        self._phase_count = 0

    def set_task_id(self, task_id: str | None) -> None:
        """Set the task ID to report progress for."""
        self._current_task_id = task_id
        self._phase_count = 0

    def on_event(self, event: Any, session: AgentSession) -> None:
        """Intercept agent events and trigger progress reporting at phase boundaries.

        Args:
            event: QueryEvent from AgentRunner stream
            session: Current agent session
        """
        # Handle phase completion
        if isinstance(event, PhaseComplete):
            self._on_phase_complete(event, session)

        # Handle turn completion (track for phase reporting)
        elif isinstance(event, TurnComplete):
            self._on_turn_complete(event, session)

        # Handle session completion
        elif isinstance(event, SessionComplete):
            self._on_session_complete(event, session)

    def _on_phase_complete(
        self,
        event: PhaseComplete,
        session: AgentSession,
    ) -> None:
        """Report progress when a phase completes."""
        if self._current_task_id is None:
            return

        self._phase_count += 1
        phase_name = f"phase_{self._phase_count}"

        # Use context to call ProgressReportTool
        # This leverages the existing tool call infrastructure
        try:
            from ..tool_system.tools.progress_report import _progress_report_call

            tool_input = {
                "taskId": self._current_task_id,
                "stage": phase_name,
                "progress": min(self._phase_count * 25, 100),  # Example: 25% per phase
                "summary": f"Completed phase {self._phase_count}",
                "metadata": {
                    "turn_count": event.turn_count,
                    "phase": event.phase,
                },
            }
            _progress_report_call(tool_input, self._context)
            logger.debug(
                "Progress reported for task %s at phase %d",
                self._current_task_id,
                self._phase_count,
            )
        except Exception as exc:
            logger.warning(
                "Failed to report progress for task %s: %s",
                self._current_task_id,
                exc,
            )

        # Also call TaskUpdateTool to update task metadata
        try:
            from ..tool_system.tools.tasks_v2 import _task_update_call

            task_update_input = {
                "taskId": self._current_task_id,
                "metadata": {
                    "phase": event.phase,
                    "turn_count": event.turn_count,
                    "phase_name": phase_name,
                    "phase_complete": True,
                },
            }
            _task_update_call(task_update_input, self._context)
            logger.debug(
                "Task metadata updated for task %s at phase %d",
                self._current_task_id,
                self._phase_count,
            )
        except Exception as exc:
            logger.warning(
                "Failed to update task metadata for task %s: %s",
                self._current_task_id,
                exc,
            )

    def _on_turn_complete(
        self,
        event: TurnComplete,
        session: AgentSession,
    ) -> None:
        """Track turn completion for phase boundary detection."""
        # Currently just tracking - actual phase boundaries are determined
        # by the orchestrator workflow configuration, not by turns alone
        logger.debug(
            "Turn %d completed for issue %s",
            event.turn,
            session.issue.identifier if session.issue else "unknown",
        )

    def _on_session_complete(
        self,
        event: SessionComplete,
        session: AgentSession,
    ) -> None:
        """Report final status when session completes."""
        if self._current_task_id is None:
            return

        try:
            from ..tool_system.tools.progress_report import _progress_report_call

            final_stage = f"phase_{self._phase_count}_complete"
            tool_input = {
                "taskId": self._current_task_id,
                "stage": final_stage,
                "progress": 100 if event.reason == "success" else None,
                "summary": f"Session completed: {event.reason}",
                "nextAction": None,
                "metadata": {
                    "session_status": session.status,
                    "turn_count": session.turn_count,
                },
            }
            _progress_report_call(tool_input, self._context)
            logger.debug(
                "Final progress reported for task %s: %s",
                self._current_task_id,
                event.reason,
            )
        except Exception as exc:
            logger.warning(
                "Failed to report final progress for task %s: %s",
                self._current_task_id,
                exc,
            )
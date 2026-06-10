"""The clawcodex workflow engine — a deterministic multi-agent orchestrator.

Executes a model-authored *Python* workflow script in a sandbox, injecting the
async orchestration primitives (``agent`` / ``parallel`` / ``pipeline`` /
``phase`` / ``log`` / ``workflow`` / ``args`` / ``budget``) and fanning out
bounded-concurrency subagent calls through an injectable :class:`AgentRunner`.

See ``docs/workflow-engine.md`` (feature) and
``docs/workflow-engine-port-plan.md`` (architecture).
"""

from __future__ import annotations

from .budget import Budget
from .constants import (
    MAX_AGENTS_PER_RUN,
    MAX_ITEMS_PER_CALL,
    MAX_STRUCTURED_OUTPUT_RETRIES,
    max_concurrent_agents,
)
from .errors import (
    WorkflowBudgetExceeded,
    WorkflowError,
    WorkflowLimitError,
    WorkflowMetaError,
)
from .gating import is_workflows_enabled
from .journal import Journal, JournalRecord, fingerprint
from .progress import AgentRecord, PhaseRecord, WorkflowProgress
from .runner import LiveAgentRunner
from .runtime import WorkflowResult, WorkflowRun, run_workflow
from .sandbox import build_namespace, compile_workflow, execute_workflow, extract_meta
from .scheduler import Scheduler
from .structured import (
    StructuredOutputCollector,
    make_structured_output_tool,
    validate_structured,
)
from .types import AgentOutcome, AgentRunner, AgentSpec, WorkflowMeta

__all__ = [
    "run_workflow",
    "WorkflowRun",
    "WorkflowResult",
    "AgentRunner",
    "LiveAgentRunner",
    "AgentSpec",
    "AgentOutcome",
    "WorkflowMeta",
    "WorkflowProgress",
    "PhaseRecord",
    "AgentRecord",
    "Budget",
    "Scheduler",
    "Journal",
    "JournalRecord",
    "fingerprint",
    "StructuredOutputCollector",
    "make_structured_output_tool",
    "validate_structured",
    "extract_meta",
    "compile_workflow",
    "build_namespace",
    "execute_workflow",
    "WorkflowError",
    "WorkflowMetaError",
    "WorkflowLimitError",
    "WorkflowBudgetExceeded",
    "is_workflows_enabled",
    "MAX_AGENTS_PER_RUN",
    "MAX_ITEMS_PER_CALL",
    "MAX_STRUCTURED_OUTPUT_RETRIES",
    "max_concurrent_agents",
]

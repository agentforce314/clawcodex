"""Exception hierarchy for the workflow engine.

The split mirrors the two failure surfaces described in
``docs/workflow-engine-port-plan.md``: pre-flight validation errors (bad
``meta``, compile failure) are raised *before* a run starts, while
limit/budget errors are raised *inside* the running script from a
primitive (``agent``/``parallel``/``pipeline``) so the script may catch
them or let them abort the run.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all workflow-engine errors."""


class WorkflowMetaError(WorkflowError):
    """The script's ``meta`` block is missing, non-literal, or invalid, or
    the script failed to parse/compile. Raised pre-flight; the run never
    starts."""


class WorkflowLimitError(WorkflowError):
    """A hard runtime cap was exceeded (per-call item cap or per-run agent
    cap). Raised from inside a primitive."""


class WorkflowBudgetExceeded(WorkflowError):
    """The run reached its token ``budget`` ceiling. Raised from ``agent()``
    so the script stops spawning work."""

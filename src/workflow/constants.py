"""Runtime caps for the workflow engine.

Authoritative values from the official spec
(<https://code.claude.com/docs/en/workflows>): up to 16 concurrent agents
(fewer on low-core machines), 1,000 agents per run, and a per-call item
cap. The upstream concurrency cap is ``min(16, cpu_cores - 2)``; we honor
that (with a ``CLAUDE_CODE_WORKFLOW_MAX_AGENTS`` override) rather than the
house-style fixed integer, because the spec explicitly reduces parallelism
on small machines.
"""

from __future__ import annotations

import os

#: Hard backstop on total ``agent()`` calls across a run's whole lifetime.
MAX_AGENTS_PER_RUN = 1000

#: Maximum items accepted by a single ``parallel()`` / ``pipeline()`` call.
MAX_ITEMS_PER_CALL = 4096

#: Retry cap for schema-validated structured output (upstream parity).
MAX_STRUCTURED_OUTPUT_RETRIES = 5

#: Hard ceiling on the concurrency cap regardless of core count.
_CONCURRENCY_HARD_MAX = 16

_ENV_MAX_AGENTS = "CLAUDE_CODE_WORKFLOW_MAX_AGENTS"


def max_concurrent_agents() -> int:
    """Resolve the per-run concurrency cap.

    ``CLAUDE_CODE_WORKFLOW_MAX_AGENTS`` overrides everything when set to a
    positive integer; otherwise ``min(16, cpu_count - 2)`` with a floor of 1.
    """
    override = os.environ.get(_ENV_MAX_AGENTS, "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    cores = os.cpu_count() or 3
    return max(1, min(_CONCURRENCY_HARD_MAX, cores - 2))

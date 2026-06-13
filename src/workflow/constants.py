"""Runtime caps for the workflow engine.

Caps from the official spec (<https://code.claude.com/docs/en/workflows>):
1,000 agents per run and a per-call item cap. The spec allows *up to* 16
concurrent agents, but workflow agents are network/LLM-bound, not CPU-bound —
so the real limiter is your provider's rate-limit / token window, not core
count. Fanning out 16 at once can burn a plan's 5-hour window fast, so we
default to a deliberately gentle **4** concurrent and let heavy users opt up
via ``CLAUDE_CODE_WORKFLOW_MAX_AGENTS``. (This is a conscious deviation from the
upstream ``min(16, cpu_cores - 2)`` heuristic, which over-parallelizes on
big-core machines.)
"""

from __future__ import annotations

import os

#: Hard backstop on total ``agent()`` calls across a run's whole lifetime.
MAX_AGENTS_PER_RUN = 1000

#: Maximum items accepted by a single ``parallel()`` / ``pipeline()`` call.
MAX_ITEMS_PER_CALL = 4096

#: Retry cap for schema-validated structured output (upstream parity).
MAX_STRUCTURED_OUTPUT_RETRIES = 5

#: How many times a single agent may be re-spawned via the `r` (retry) action.
MAX_AGENT_RETRIES = 3

#: Default per-run concurrency cap. Small on purpose — see the module docstring:
#: the limiter is the rate-limit window, not CPU, and 4 keeps a run from eating a
#: plan's budget in one burst. Raise via ``CLAUDE_CODE_WORKFLOW_MAX_AGENTS``.
DEFAULT_MAX_CONCURRENT_AGENTS = 4

_ENV_MAX_AGENTS = "CLAUDE_CODE_WORKFLOW_MAX_AGENTS"


def max_concurrent_agents() -> int:
    """Resolve the per-run concurrency cap.

    ``CLAUDE_CODE_WORKFLOW_MAX_AGENTS`` overrides everything when set to a
    positive integer; otherwise the gentle :data:`DEFAULT_MAX_CONCURRENT_AGENTS`
    (4). Note: this caps how many agents run *at once* (the burst), not the
    total token spend of a run — that is governed by the workflow's own fan-out
    breadth and the ``budget`` ceiling.
    """
    override = os.environ.get(_ENV_MAX_AGENTS, "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return DEFAULT_MAX_CONCURRENT_AGENTS

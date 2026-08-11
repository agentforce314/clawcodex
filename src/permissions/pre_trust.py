from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PreTrustAction = Literal["hook", "mcp", "project-config"]
PreTrustSource = Literal[
    "enterprise",
    "managed",
    "policy",
    "user",
    "project",
    "local",
    "dynamic",
    "unknown",
]


@dataclass(frozen=True)
class PreTrustDecision:
    allow: bool
    reason: str


_TRUSTED_SOURCES: frozenset[str] = frozenset({
    "enterprise",
    "managed",
    "policy",
    "user",
    "dynamic",
})

_PROJECT_SCOPED_SOURCES: frozenset[str] = frozenset({
    "project",
    "local",
    "unknown",
})


def check_pre_trust_gate(
    action: PreTrustAction,
    *,
    source: str | None = None,
    cwd: str | Path | None = None,
    workspace_trusted: bool | None = None,
) -> PreTrustDecision:
    """Decide whether pre-trust executable project input may run.

    This is the common C2 gate for hooks, MCP servers, and project-scoped
    executable config. Trusted/operator-owned sources are allowed; project and
    local sources require workspace trust. Unknown source fails closed.
    """

    normalized = (source or "unknown").strip().lower().replace("_", "-")
    if normalized in _TRUSTED_SOURCES:
        return PreTrustDecision(
            allow=True,
            reason=f"{action}: trusted source {normalized}",
        )

    if workspace_trusted is None:
        try:
            from src.services.startup_gates import check_trust_accepted

            workspace_trusted = check_trust_accepted(cwd)
        except Exception:
            workspace_trusted = False

    if workspace_trusted and normalized in _PROJECT_SCOPED_SOURCES:
        return PreTrustDecision(
            allow=True,
            reason=f"{action}: workspace trusted",
        )

    return PreTrustDecision(
        allow=False,
        reason=f"{action}: workspace is not trusted for source {normalized}",
    )

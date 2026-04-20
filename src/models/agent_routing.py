"""Per-agent model routing matching TypeScript model/agent.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentModelConfig:
    """Model configuration for an agent."""
    model: str = ""
    inherit_parent: bool = True


def get_model_for_agent(
    agent_type: str,
    *,
    parent_model: str = "",
    config: dict[str, Any] | None = None,
) -> str:
    """Resolve which model an agent should use.

    Priority:
    1. Agent-specific model from config
    2. Agent definition model
    3. Parent model (inheritance)
    """
    if config:
        agent_models = config.get("agent_models", {})
        if agent_type in agent_models:
            model = agent_models[agent_type]
            if isinstance(model, str) and model:
                return model

    # Default: inherit parent model
    return parent_model

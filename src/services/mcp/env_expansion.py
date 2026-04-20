from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class EnvExpansionResult:
    expanded: str
    missing_vars: list[str] = field(default_factory=list)


def expand_env_vars_in_string(value: str) -> EnvExpansionResult:
    missing_vars: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        var_content = match.group(1)
        parts = var_content.split(":-", 1)
        var_name = parts[0]
        default_value = parts[1] if len(parts) > 1 else None

        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value

        missing_vars.append(var_name)
        return match.group(0)

    expanded = re.sub(r"\$\{([^}]+)\}", _replace, value)
    return EnvExpansionResult(expanded=expanded, missing_vars=missing_vars)

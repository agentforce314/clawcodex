from __future__ import annotations

import re

CLAUDEAI_SERVER_PREFIX = "claude.ai "


def normalize_name_for_mcp(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    if name.startswith(CLAUDEAI_SERVER_PREFIX):
        normalized = re.sub(r"_+", "_", normalized)
        normalized = normalized.strip("_")
    return normalized

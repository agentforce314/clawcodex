"""Best-effort per-run debug logging for orchestrator agent runs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def append_debug_event(path: str | Path | None, stage: str, **fields: Any) -> None:
    """Append one NDJSON debug event without affecting the agent run."""
    if not path:
        return
    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stage": stage,
            **fields,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.debug("Failed to append orchestrator debug log path=%s", path, exc_info=True)

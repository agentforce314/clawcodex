"""Session analytics metadata.

Mirrors TypeScript analytics/metadata.ts — collects session-level metadata
for analytics reporting.
"""
from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionAnalyticsMetadata:
    """Metadata collected at session start for analytics."""
    session_id: str = ""
    model: str = ""
    os_name: str = ""
    os_version: str = ""
    python_version: str = ""
    ide_type: str = ""
    ide_version: str = ""
    is_non_interactive: bool = False
    is_resume: bool = False
    start_time: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


def collect_session_metadata(
    session_id: str = "",
    model: str = "",
    ide_type: str = "",
    ide_version: str = "",
    is_non_interactive: bool = False,
    is_resume: bool = False,
) -> SessionAnalyticsMetadata:
    """Collect session metadata from the environment."""
    return SessionAnalyticsMetadata(
        session_id=session_id,
        model=model,
        os_name=platform.system(),
        os_version=platform.release(),
        python_version=platform.python_version(),
        ide_type=ide_type,
        ide_version=ide_version,
        is_non_interactive=is_non_interactive,
        is_resume=is_resume,
    )

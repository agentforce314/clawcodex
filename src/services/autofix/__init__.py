"""autoFix — run the user's lint/test command after a file edit and, on
failure, inject ``<auto_fix_feedback>`` telling the model to self-fix.

SERVICES-2 (services-folder parity). Port of typescript/src/services/autoFix/
(config + hook + runner). Settings opt-in (``settings.autoFix``), NOT
flag-gated. Python previously had only the ``/autofix`` slash shell that
DOCUMENTS the config; the runtime hook + runner were absent.
"""

from __future__ import annotations

from .config import AutoFixConfig, get_auto_fix_config
from .hook import AUTO_FIX_TOOLS, build_auto_fix_context, should_run_auto_fix
from .runner import AutoFixResult, run_auto_fix_check

__all__ = [
    "AutoFixConfig",
    "get_auto_fix_config",
    "AUTO_FIX_TOOLS",
    "build_auto_fix_context",
    "should_run_auto_fix",
    "AutoFixResult",
    "run_auto_fix_check",
]

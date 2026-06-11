"""doctor — ``/doctor`` diagnostics (port of TS local-jsx, components C6).

TS ``/doctor`` (``commands/doctor``) renders ``DiagnosticsDisplay``.
Python's rich surface is the (previously dormant, now wired) TUI
``DoctorScreen``; this registry command serves the NON-TUI surfaces in
the output-style precedent — ``run()`` returns a text report without
touching ``ctx.ui`` (headless/REPL/SDK safe).

Coexistence: **inversion** — the TUI intercepts ``/doctor`` and pushes
the screen; this command serves everything else.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)


def build_doctor_report(cwd: str | None = None) -> str:
    from src.services.config_health import collect_config_warnings

    lines: list[str] = ["Diagnostics:"]
    lines.append(
        f"• python {platform.python_version()} on "
        f"{platform.system()} {platform.machine()} ({sys.executable})"
    )
    try:
        from src.tool_system.utils.ripgrep import find_ripgrep

        rg = find_ripgrep()
        lines.append(f"• ripgrep: {rg or 'NOT FOUND (file search degraded)'}")
    except Exception:
        lines.append("• ripgrep: check failed")
    try:
        from src.services.session_storage import SESSIONS_DIR

        lines.append(f"• sessions dir: {SESSIONS_DIR}")
    except Exception:
        pass
    try:
        warnings = collect_config_warnings(cwd)
    except Exception:
        warnings = []
        lines.append("• config health: check failed")
    if warnings:
        lines.append("Config problems:")
        lines.extend(f"  ⚠ {w.message()}" for w in warnings)
    else:
        lines.append("• config files: OK")
    try:
        from src.services.config_health import collect_rule_warnings

        rule_warnings = collect_rule_warnings(cwd)
    except Exception:
        rule_warnings = []
    if rule_warnings:
        lines.append("Permission-rule warnings:")
        lines.extend(f"  ⚠ {w}" for w in rule_warnings)
    return "\n".join(lines)


@dataclass(frozen=True)
class DoctorCommand(InteractiveCommand):
    """Environment + config health report (text on every surface)."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        cwd = str(getattr(context, "cwd", "") or "") or None
        return InteractiveOutcome(
            message=build_doctor_report(cwd), display="system"
        )


DOCTOR_COMMAND = DoctorCommand(
    name="doctor",
    # TS doctor/index.ts:6 with the product name made neutral.
    description="Diagnose and verify your installation and settings",
    argument_hint="",
)


__all__ = ["DOCTOR_COMMAND", "DoctorCommand", "build_doctor_report"]

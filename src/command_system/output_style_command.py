"""output-style — deprecated ``/output-style`` command (port of TS local-jsx).

The TypeScript command (``typescript/src/commands/output-style/``) is a
``local-jsx`` command that renders nothing interactive: its ``call`` immediately
invokes ``onDone(<deprecation notice>, { display: 'system' })``. It exists only
to tell users the feature moved to ``/config``.

Ported as an :class:`InteractiveCommand` because ``local-jsx`` maps onto
``CommandType.INTERACTIVE`` (same remote-safety blocking by type). Unlike the
``/permissions`` exemplar it never touches ``context.ui`` — :meth:`run` returns
the deprecation :class:`InteractiveOutcome` directly, so it behaves identically
on every surface (REPL, Textual, and ``NullUIHost`` headless).
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)

# Verbatim from typescript/src/commands/output-style/output-style.tsx.
_DEPRECATION_NOTICE = (
    "/output-style has been deprecated. Use /config to change your output "
    "style, or set it in your settings file. Changes take effect on the next "
    "session."
)


@dataclass(frozen=True)
class OutputStyleCommand(InteractiveCommand):
    """Emit the deprecation notice and nothing else. Frozen + no new fields
    (the ``PermissionsCommand`` pattern); behavior lives entirely in
    :meth:`run`."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        return InteractiveOutcome(
            message=_DEPRECATION_NOTICE,
            display="system",
        )


OUTPUT_STYLE_COMMAND = OutputStyleCommand(
    name="output-style",
    description="Deprecated: use /config to change output style",
    is_hidden=True,
)


__all__ = ["OUTPUT_STYLE_COMMAND", "OutputStyleCommand"]

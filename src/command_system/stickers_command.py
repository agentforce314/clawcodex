"""stickers — ``/stickers`` open the sticker page (port of TS ``type:'local'``).

Port of ``typescript/src/commands/stickers/``. Opens the sticker-order page in the
default browser via stdlib :mod:`webbrowser` (the ``openBrowser`` analog); on failure
falls back to printing the URL. Messages verbatim from ``stickers.ts``.
"""
from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult

_URL = "https://www.stickermule.com/claudecode"


def stickers_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    try:
        import webbrowser

        success = webbrowser.open(_URL)
    except Exception:
        success = False
    if success:
        return LocalCommandResult(type="text", value="Opening sticker page in browser…")
    return LocalCommandResult(type="text", value=f"Failed to open browser. Visit: {_URL}")


STICKERS_COMMAND = LocalCommand(
    name="stickers",
    description="Order OpenClaude stickers",  # verbatim TS index.ts
    supports_non_interactive=False,  # verbatim TS index.ts
)
STICKERS_COMMAND.set_call(stickers_command_call)


__all__ = ["STICKERS_COMMAND", "stickers_command_call"]

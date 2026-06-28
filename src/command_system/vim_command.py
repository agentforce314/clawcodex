"""vim — ``/vim`` editor-mode toggle (port of TS ``type:'local'``).

Port of ``typescript/src/commands/vim/``. Toggles ``editorMode`` between ``"vim"`` and
``"normal"`` in the global config (the TS ``saveGlobalConfig`` channel; legacy
``"emacs"`` reads as ``"normal"``) and reports the new mode verbatim.

**Status (deferred):** with the in-process Textual TUI and Rich REPL removed, no
surviving Python surface reads ``editorMode`` — the TypeScript Ink TUI owns its own
editor mode. This command still persists the toggle to the global config faithfully,
but it is inert on the Python side until/unless the Ink client consumes ``editorMode``
over the protocol.
"""
from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult


def initial_vim_mode() -> bool:
    """Best-effort seed for ``PromptInput(vim_mode=...)``: True iff the persisted
    ``editorMode`` is ``"vim"`` (False on unset/normal/legacy-emacs/any error)."""
    try:
        from src.config import load_config

        return load_config().get("editorMode") == "vim"
    except Exception:
        return False


def vim_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    from src.config import _get_default_manager, load_config

    current = load_config().get("editorMode") or "normal"
    if current == "emacs":  # TS back-compat: treat 'emacs' as 'normal'
        current = "normal"
    new_mode = "vim" if current == "normal" else "normal"
    _get_default_manager().set_global("editorMode", new_mode)

    if new_mode == "vim":
        suffix = "Use Escape key to toggle between INSERT and NORMAL modes."
    else:
        suffix = "Using standard (readline) keyboard bindings."
    # Divergence from TS (which applies the toggle live): the trailing note keeps the
    # mid-session instruction honest — the seed is read at PromptInput construction.
    return LocalCommandResult(
        type="text",
        value=f"Editor mode set to {new_mode}. {suffix} Takes effect on next TUI launch.",
    )


VIM_COMMAND = LocalCommand(
    name="vim",
    description="Toggle between Vim and Normal editing modes",  # verbatim TS index.ts
    supports_non_interactive=False,  # verbatim TS index.ts
)
VIM_COMMAND.set_call(vim_command_call)


__all__ = ["VIM_COMMAND", "vim_command_call", "initial_vim_mode"]

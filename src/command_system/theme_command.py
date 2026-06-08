"""theme — interactive ``/theme`` command (port of TS local-jsx).

Port of ``typescript/src/commands/theme/`` (``theme.tsx`` + ``index.ts``). A
``local-jsx`` command becomes an :class:`InteractiveCommand` (blocked remotely by
type). Unlike ``/export`` — which proves its bridge wiring by *falling through* the
TUI dispatch so the registry arm runs — ``/theme`` is the **inverse**: the TUI keeps
intercepting ``/theme`` (``commands.py`` → ``open_dialog="theme"``) to preserve its
rich **live-preview** ``ThemePickerScreen``. This command therefore serves only the
surfaces that *consult the registry*: the REPL (numbered-menu ``select``), the SDK
(``NullUIHost`` → clean error), and the help/aggregator listings — where ``/theme``
was previously invisible because it lived only in the TUI's private ``LOCAL_BUILTINS``.

Faithfulness to TS (``theme.tsx``):

  * ``call(onDone, _context)`` **ignores args** — the picker is the only path. We do
    not parse args and add no headless write path; on ``NullUIHost`` (SDK) the
    ``select`` below raises → the engine returns a clean "needs an interactive
    surface" error. There is no ``/export``-style headless keystone because TS
    ``/theme`` has none.
  * **Success → ``display="user"``.** TS ``onDone(f"Theme set to {setting}")`` passes
    **no** options, so ``processSlashCommand`` routes it through ``createUserMessage``
    (model-visible) with ``shouldQuery ?? false``. Python's faithful equivalent is
    ``display="user"`` — the engine's own value for model-visible content
    (``CommandResult.success_prompt``) — with the default ``should_query=False``.
  * **Cancel → "Theme picker dismissed" / ``display="system"``**, **not** ``skip()``.
    Faithful to TS ``onDone("Theme picker dismissed", {display:"system"})`` (a
    user-visible system line). Cancel does **not** persist.

Deliberate divergences (documented for parity review):

  * **Python's own theme set.** Options come from :func:`list_theme_names`
    (``auto``/``dark``/``light``/``claude``) — the same source the TUI
    ``ThemePickerScreen`` uses, so both surfaces stay in lockstep — not TS's
    ``light-daltonized``/``*-ansi`` variants (no Python ``Palette`` exists for them)
    nor TS's flag-gated ``auto`` (Python offers ``auto`` unconditionally). Labels are
    the **raw** theme names, mirroring ``ThemePickerScreen``'s ``SelectOption(label=
    name…)``, not TS's friendly labels.
  * **Persist-only, no live hot-swap.** The command_system surface has no ``App``
    handle (``CommandContext`` carries workspace_root/cwd/conversation/ui, not the
    Textual app), so "Theme set to {name}" means *persisted; applies on next launch*
    on non-TUI surfaces. The live hot-swap stays the TUI dialog's job (``apply_theme``).

``list_theme_names`` is imported **lazily** inside :func:`_theme_options` (not at
module top) so a bare ``import src.command_system`` does not pull the heavy
``src.tui`` package — ``src/tui/__init__.py`` eagerly imports the Textual app. This
mirrors the local-import discipline already used in ``builtins.py`` (buddy) and
``app.py`` (config).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import load_config, set_theme

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)

_DEFAULT_THEME = "dark"


def _current_theme() -> str:
    """The persisted theme, or ``"dark"`` — the same happy-path read as
    ``app._resolve_theme_name()`` (``load_config().get("theme") or "dark"``).
    ``_resolve_theme_name`` additionally wraps the read in ``try/except → "dark"``;
    here we intentionally let a ``load_config()`` failure propagate, because the
    engine wraps :meth:`ThemeCommand.run` and converts any exception into a clean
    error ``CommandResult`` (whereas the TUI swallows it to keep rendering). The
    *merged* read is correct here: we only need the *effective* current value to seed
    the picker, not a write."""
    return load_config().get("theme") or _DEFAULT_THEME


def _theme_options(current: str) -> list[UIOption]:
    """Build picker options from :func:`list_theme_names`, marking the option equal to
    ``current`` with ``description="current"`` (the same marker the TUI
    ``ThemePickerScreen`` shows). Labels are the raw theme names.

    Imported lazily — see the module docstring."""
    from src.tui.theme import list_theme_names

    options: list[UIOption] = []
    for name in list_theme_names():
        desc = "current" if name == current else None
        options.append(UIOption(value=name, label=name, description=desc))
    return options


@dataclass(frozen=True)
class ThemeCommand(InteractiveCommand):
    """Pick a color theme and persist it.

    Frozen + no new fields (the ``ExportCommand``/``StatuslineCommand`` pattern);
    behavior lives entirely in :meth:`run`.
    """

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        # TS `call(onDone, _context)` ignores args — the picker is the only path.
        # No args path => no headless keystone (unlike /export); on NullUIHost the
        # select below raises -> the engine returns a clean error.
        current = _current_theme()
        options = _theme_options(current)
        picked = await context.ui.select("Select theme:", options, current=current)
        if picked is None:
            # TS cancel: onDone("Theme picker dismissed", {display:"system"}).
            # A visible system line — NOT skip() (which /export uses for Esc).
            return InteractiveOutcome(
                message="Theme picker dismissed", display="system"
            )
        set_theme(picked)  # persist (TS setTheme -> setThemeSetting -> saveGlobalConfig)
        # TS success: onDone("Theme set to …") with NO options => createUserMessage
        # (model-visible, shouldQuery=false). Faithful map = display="user".
        return InteractiveOutcome(message=f"Theme set to {picked}", display="user")


THEME_COMMAND = ThemeCommand(
    name="theme",
    # Verbatim from typescript/src/commands/theme/index.ts. (The TUI popup shows
    # "Change the color theme" from _LOCAL_BUILTIN_DESCRIPTIONS, which wins the
    # suggestion dedup; this TS-verbatim text surfaces on the registry/SDK listings.)
    description="Change the theme",
)


__all__ = [
    "THEME_COMMAND",
    "ThemeCommand",
]

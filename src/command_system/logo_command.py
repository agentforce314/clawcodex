"""logo — interactive ``/logo`` command (port of TS local-jsx).

Port of ``typescript/src/commands/logo/`` (``logo.tsx`` + ``index.ts``). Picks the
startup-banner color palette and persists it to the global config ``logoColor`` key;
the two startup banners (REPL + TUI) resolve and render it on next launch.

Coexistence: **fall-through** (the ``/export`` pattern, NOT the ``/theme``/``/effort``/
``/model`` inversion). There is **no TUI dialog** for ``/logo`` (it is not in the TUI's
``LOCAL_BUILTINS``/``open_dialog`` map), so the dispatch falls through and this
``InteractiveCommand`` serves every surface via the ``UIHost`` port — TUI
(``TextualUIHost.select``), REPL (``ReplUIHost.select``), SDK (``NullUIHost`` → clean
error), and the help/aggregator listings.

Faithfulness to TS (``logo.tsx``):
  * ``call(onDone, _context)`` **ignores args** — the picker is the only path; no
    headless keystone (on ``NullUIHost`` the ``select`` raises → engine clean error).
  * **Success → ``display="user"``** (TS ``onDone("Startup logo set to …")`` with no
    options → model-visible ``createUserMessage``).
  * **Cancel → "Logo picker dismissed" / ``display="system"``** (verbatim TS).

Deliberate divergences (documented for parity review):
  * **No swatch preview** in the picker — TS ``LogoPicker`` shows an ANSI gradient
    swatch per option, but the ``select`` primitive has plain labels. Labels are the
    friendly ``LOGO_PALETTE_LABELS`` (TS shows labels too).
  * **Static description** ("Change the startup logo color scheme"); TS's is dynamic
    ``…(current: {label})`` — a frozen ``CommandBase.description`` can't be a getter.
    The current palette is the picker's ``current=`` marker.
  * **Persist via config ``logoColor``** (top-level key, like ``/theme``).

``logo_palettes`` / ``set_logo_color`` are imported lazily (the ``theme``/``app.py``
discipline).
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)


def _current_logo() -> str:
    """The persisted palette name, or the default — mirrors TS index.ts:13-14 /
    logo.tsx:20-23 (validate via ``is_logo_palette_name``)."""
    from src.config import load_config
    from src.utils.logo_palettes import DEFAULT_LOGO_PALETTE, is_logo_palette_name

    current = load_config().get("logoColor")
    return current if is_logo_palette_name(current) else DEFAULT_LOGO_PALETTE


def _logo_options(current: str) -> list[UIOption]:
    """Picker options: friendly labels, marking the current palette. Lazy import."""
    from src.utils.logo_palettes import LOGO_PALETTE_LABELS, LOGO_PALETTE_NAMES

    return [
        UIOption(
            value=name,
            label=LOGO_PALETTE_LABELS[name],
            description="current" if name == current else None,
        )
        for name in LOGO_PALETTE_NAMES
    ]


@dataclass(frozen=True)
class LogoCommand(InteractiveCommand):
    """Pick the startup logo color palette and persist it. Frozen + no new fields
    (the ``ThemeCommand`` pattern); behavior lives in :meth:`run`."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        # TS call ignores args — the picker is the only path. No headless keystone;
        # on NullUIHost the select below raises -> the engine returns a clean error.
        from src.config import set_logo_color
        from src.utils.logo_palettes import LOGO_PALETTE_LABELS

        current = _current_logo()
        picked = await context.ui.select(
            "Select startup logo color:", _logo_options(current), current=current
        )
        if picked is None:
            # TS cancel: onDone("Logo picker dismissed", {display:"system"}).
            return InteractiveOutcome(message="Logo picker dismissed", display="system")
        set_logo_color(picked)  # persist (TS saveGlobalConfig logoColor)
        # TS success: onDone("Startup logo set to …") with NO options => model-visible.
        return InteractiveOutcome(
            message=f"Startup logo set to {LOGO_PALETTE_LABELS[picked]}. Visible on next launch.",
            display="user",
        )


LOGO_COMMAND = LogoCommand(
    name="logo",
    description="Change the startup logo color scheme",  # static (TS dynamic — see docstring)
)


__all__ = ["LOGO_COMMAND", "LogoCommand"]

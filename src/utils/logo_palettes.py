"""Color palettes for the startup banner logo.

Port of ``typescript/src/components/StartupScreen.palettes.ts``. Selected via the
``/logo`` command, persisted in the global config ``logoColor`` key (written by
:func:`src.config.set_logo_color` — the ``set_logo_color`` agent-server control).

The rendering consumer is the Ink TUI banner: ``ui-tui/src/lib/logoPalettes.ts``
carries the SAME verbatim palette table (keep the two in sync) and
``ui-tui/src/banner.ts`` paints the wordmark/mascot rows from it. The old Rich
REPL / Textual TUI banners that this module originally styled were deleted in the
UI consolidation (PR #566), and their ``banner_palette`` / ``mascot_gradient_text``
helpers went with them; this module now only keeps the palette table + validators
for the ``/logo`` command and the server control.
"""
from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class LogoPalette:
    gradient: tuple[RGB, ...]  # top→bottom stops painted across the mascot rows
    accent: RGB  # title / version highlight
    cream: RGB  # soft body text (table values)
    dim: RGB  # label names / footer
    border: RGB  # Panel border


# Verbatim from StartupScreen.palettes.ts:21-78.
LOGO_PALETTES: dict[str, LogoPalette] = {
    "sunset": LogoPalette(
        gradient=(
            (255, 180, 100),
            (240, 140, 80),
            (217, 119, 87),
            (193, 95, 60),
            (160, 75, 55),
            (130, 60, 50),
        ),
        accent=(240, 148, 100),
        cream=(220, 195, 170),
        dim=(120, 100, 82),
        border=(100, 80, 65),
    ),
    "forest": LogoPalette(
        gradient=(
            (180, 240, 170),
            (130, 215, 130),
            (85, 180, 95),
            (55, 145, 75),
            (40, 110, 60),
            (25, 80, 45),
        ),
        accent=(120, 200, 120),
        cream=(200, 220, 190),
        dim=(90, 120, 90),
        border=(70, 95, 70),
    ),
    "ocean": LogoPalette(
        gradient=(
            (170, 220, 255),
            (125, 185, 240),
            (80, 150, 220),
            (55, 115, 190),
            (40, 85, 150),
            (25, 55, 110),
        ),
        accent=(110, 180, 230),
        cream=(195, 215, 235),
        dim=(90, 115, 145),
        border=(70, 90, 115),
    ),
    "monochrome": LogoPalette(
        gradient=(
            (225, 225, 225),
            (195, 195, 195),
            (160, 160, 160),
            (125, 125, 125),
            (95, 95, 95),
            (70, 70, 70),
        ),
        accent=(200, 200, 200),
        cream=(210, 210, 210),
        dim=(120, 120, 120),
        border=(95, 95, 95),
    ),
}

LOGO_PALETTE_NAMES: list[str] = list(LOGO_PALETTES.keys())

DEFAULT_LOGO_PALETTE = "sunset"

# Verbatim from StartupScreen.palettes.ts:86-91.
LOGO_PALETTE_LABELS: dict[str, str] = {
    "sunset": "Sunset (default)",
    "forest": "Forest green",
    "ocean": "Ocean blue",
    "monochrome": "Monochrome",
}


def is_logo_palette_name(value: object) -> bool:
    """True iff ``value`` is a known palette name."""
    return isinstance(value, str) and value in LOGO_PALETTES


def resolve_logo_palette(name: str | None) -> LogoPalette:
    """The palette for ``name``, or the default (``sunset``) for unknown/None."""
    if is_logo_palette_name(name):
        return LOGO_PALETTES[name]  # type: ignore[index]
    return LOGO_PALETTES[DEFAULT_LOGO_PALETTE]


__all__ = [
    "RGB",
    "LogoPalette",
    "LOGO_PALETTES",
    "LOGO_PALETTE_NAMES",
    "DEFAULT_LOGO_PALETTE",
    "LOGO_PALETTE_LABELS",
    "is_logo_palette_name",
    "resolve_logo_palette",
]

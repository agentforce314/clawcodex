"""Color palettes for the startup banner logo.

Port of ``typescript/src/components/StartupScreen.palettes.ts``. Selected via the
``/logo`` command, persisted in the global config ``logoColor`` key, and applied by
the two startup banners (``src/repl/core.py::_print_startup_header`` and
``src/tui/widgets/header.py::StartupHeader._render_banner``).

Imports only ``rich`` + stdlib (NO Textual) so the REPL banner can import it. The
palette ROLES (gradient / accent / cream / dim / border) are mapped onto Python's
mascot+table+Panel banner (which differs from TS's gradient ASCII logo) via
:func:`banner_palette` + :func:`mascot_gradient_text`.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

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


def rgb_hex(rgb: RGB) -> str:
    """``(r,g,b)`` → ``"#rrggbb"`` (Rich truecolor style)."""
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass(frozen=True)
class BannerStyles:
    """Resolved Rich style strings for the banner elements. ``bold`` weight is
    preserved where the original hardcoded styles were bold (so ``/logo`` changes
    only hue, not weight)."""

    border: str  # Panel border_style (current "bright_black" — not bold)
    title: str  # Panel title (current "bold bright_cyan")
    accent: str  # version row (current "bold white"/"bold cyan")
    value: str  # table values (current "bold magenta"/"green"/"blue")
    label: str  # label column (current "bright_black" — not bold)
    dim: str  # footer / subtitle (current "dim" — not bold)


def banner_palette(name: str | None) -> BannerStyles:
    """Resolve ``name`` → the banner style strings. Never raises (unknown/None →
    default ``sunset``)."""
    p = resolve_logo_palette(name)
    return BannerStyles(
        border=rgb_hex(p.border),
        title=f"bold {rgb_hex(p.accent)}",
        accent=f"bold {rgb_hex(p.accent)}",
        value=f"bold {rgb_hex(p.cream)}",
        label=rgb_hex(p.dim),
        dim=rgb_hex(p.dim),
    )


def mascot_gradient_text(name: str | None, mascot_lines: list[str]) -> Text:
    """Build the mascot as a Rich ``Text`` with a vertical gradient: each line gets a
    gradient stop sampled across the palette (bold, matching the original
    ``bold orange3``). Never raises."""
    palette = resolve_logo_palette(name)
    gradient = palette.gradient
    n = len(mascot_lines)
    text = Text(no_wrap=True)
    for i, line in enumerate(mascot_lines):
        if n <= 1:
            stop = gradient[0]
        else:
            stop = gradient[round(i * (len(gradient) - 1) / (n - 1))]
        suffix = "\n" if i < n - 1 else ""
        text.append(line + suffix, style=f"bold {rgb_hex(stop)}")
    return text


__all__ = [
    "RGB",
    "LogoPalette",
    "LOGO_PALETTES",
    "LOGO_PALETTE_NAMES",
    "DEFAULT_LOGO_PALETTE",
    "LOGO_PALETTE_LABELS",
    "is_logo_palette_name",
    "resolve_logo_palette",
    "rgb_hex",
    "BannerStyles",
    "banner_palette",
    "mascot_gradient_text",
]

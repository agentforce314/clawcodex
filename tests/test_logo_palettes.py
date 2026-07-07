"""Tests for the logo color-palette module (Phase 8).

Guards the direct data port of ``StartupScreen.palettes.ts``. The Rich
banner-style helpers this file also covered (``banner_palette``,
``mascot_gradient_text``, ``rgb_hex``) were deleted along with their REPL/Textual
consumers (UI consolidation, PR #566) — the rendering twin now lives in
``ui-tui/src/lib/logoPalettes.ts`` + ``ui-tui/src/banner.ts`` with its own
vitest coverage (``logoPalettes.test.ts``).
"""
from __future__ import annotations

from src.utils.logo_palettes import (
    DEFAULT_LOGO_PALETTE,
    LOGO_PALETTE_LABELS,
    LOGO_PALETTE_NAMES,
    LOGO_PALETTES,
    is_logo_palette_name,
    resolve_logo_palette,
)


def test_palette_names_and_default():
    assert LOGO_PALETTE_NAMES == ["sunset", "forest", "ocean", "monochrome"]
    assert DEFAULT_LOGO_PALETTE == "sunset"
    assert LOGO_PALETTE_LABELS == {
        "sunset": "Sunset (default)",
        "forest": "Forest green",
        "ocean": "Ocean blue",
        "monochrome": "Monochrome",
    }


def test_palette_data_verbatim_spotcheck():
    # Guards the RGB data against StartupScreen.palettes.ts.
    sunset = LOGO_PALETTES["sunset"]
    assert sunset.gradient[0] == (255, 180, 100)
    assert sunset.gradient[5] == (130, 60, 50)
    assert sunset.accent == (240, 148, 100)
    assert sunset.cream == (220, 195, 170)
    assert sunset.dim == (120, 100, 82)
    assert sunset.border == (100, 80, 65)
    assert len(sunset.gradient) == 6
    # Each palette has a 6-stop gradient.
    for name in LOGO_PALETTE_NAMES:
        assert len(LOGO_PALETTES[name].gradient) == 6
    assert LOGO_PALETTES["ocean"].gradient[0] == (170, 220, 255)
    assert LOGO_PALETTES["monochrome"].accent == (200, 200, 200)


def test_is_logo_palette_name():
    assert is_logo_palette_name("sunset") is True
    assert is_logo_palette_name("ocean") is True
    assert is_logo_palette_name("bogus") is False
    assert is_logo_palette_name(None) is False
    assert is_logo_palette_name(123) is False


def test_resolve_logo_palette_falls_back_to_default():
    assert resolve_logo_palette("ocean") is LOGO_PALETTES["ocean"]
    assert resolve_logo_palette("bogus") is LOGO_PALETTES["sunset"]
    assert resolve_logo_palette(None) is LOGO_PALETTES["sunset"]

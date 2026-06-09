"""Tests for the logo color-palette module (Phase 8).

Guards the direct data port of ``StartupScreen.palettes.ts`` and the banner-style
helpers (``banner_palette``, ``mascot_gradient_text``, ``rgb_hex``).
"""
from __future__ import annotations

from src.utils.logo_palettes import (
    DEFAULT_LOGO_PALETTE,
    LOGO_PALETTE_LABELS,
    LOGO_PALETTE_NAMES,
    LOGO_PALETTES,
    banner_palette,
    is_logo_palette_name,
    mascot_gradient_text,
    resolve_logo_palette,
    rgb_hex,
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


def test_rgb_hex():
    assert rgb_hex((255, 180, 100)) == "#ffb464"
    assert rgb_hex((0, 0, 0)) == "#000000"
    assert rgb_hex((100, 80, 65)) == "#645041"


def test_banner_palette_styles():
    st = banner_palette("ocean")
    p = LOGO_PALETTES["ocean"]
    assert st.border == rgb_hex(p.border)  # not bold (Panel border)
    assert st.title == f"bold {rgb_hex(p.accent)}"
    assert st.accent == f"bold {rgb_hex(p.accent)}"
    assert st.value == f"bold {rgb_hex(p.cream)}"  # bold preserved
    assert st.label == rgb_hex(p.dim)  # not bold
    assert st.dim == rgb_hex(p.dim)


def test_banner_palette_default_on_unset():
    assert banner_palette(None) == banner_palette("sunset")
    assert banner_palette("bogus") == banner_palette("sunset")


def test_mascot_gradient_text_per_line():
    lines = ["a", "b", "c", "d"]  # the 4-line mascot shape
    text = mascot_gradient_text("sunset", lines)
    assert text.plain == "a\nb\nc\nd"
    grad = LOGO_PALETTES["sunset"].gradient
    # 4 lines sample stops [0, 2, 3, 5].
    expected = [grad[0], grad[2], grad[3], grad[5]]
    styles = [span.style for span in text.spans]
    assert styles == [f"bold {rgb_hex(s)}" for s in expected]


def test_mascot_gradient_text_single_line_guard():
    # N<=1 must not divide-by-zero.
    text = mascot_gradient_text("forest", ["only"])
    assert text.plain == "only"
    assert text.spans[0].style == f"bold {rgb_hex(LOGO_PALETTES['forest'].gradient[0])}"

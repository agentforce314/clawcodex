"""Theme palette for the Claw Codex Textual TUI.

Mirrors the palette keys defined in ``typescript/src/utils/theme.js`` so
widgets port 1-for-1 (``theme.primary``, ``theme.success`` etc.). Three
built-in palettes are exposed: ``dark``, ``light``, and ``claude`` — the
dark variant is the default and is tuned to match the ink reference.

The palette is deliberately plain-dataclass so Textual CSS references
like ``$primary`` continue to work via ``App.theme`` + ``ColorSystem``;
we expose the raw hex constants so Rich renderables inside widgets
(panels, markdown, etc.) can use the same colors as the CSS layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    # Foreground + background
    text: str
    text_muted: str
    background: str
    surface: str
    surface_alt: str
    border: str
    # Semantic colors (match typescript/src/utils/theme.js keys)
    primary: str
    secondary: str
    success: str
    warning: str
    error: str
    info: str
    # Role-specific
    user: str
    assistant: str
    tool: str
    tool_running: str
    tool_success: str
    tool_error: str
    system: str
    # Streaming / spinner
    spinner: str
    verb: str


DARK = Palette(
    name="dark",
    text="#e6e6e6",
    text_muted="#9a9a9a",
    background="#0b0b0f",
    surface="#141418",
    surface_alt="#1c1c22",
    border="#2a2a33",
    primary="#8ab4f8",
    secondary="#c58af9",
    success="#7ee787",
    warning="#f5c451",
    error="#ff7b72",
    info="#79c0ff",
    user="#8ab4f8",
    assistant="#c58af9",
    tool="#f5c451",
    tool_running="#f5c451",
    tool_success="#7ee787",
    tool_error="#ff7b72",
    system="#9a9a9a",
    spinner="#f5c451",
    verb="#f0f0f0",
)

LIGHT = Palette(
    name="light",
    text="#1a1a1a",
    text_muted="#6a6a6a",
    background="#fafafa",
    surface="#ffffff",
    surface_alt="#f0f0f2",
    border="#d0d0d5",
    primary="#1a73e8",
    secondary="#8430ce",
    success="#1a7f37",
    warning="#bf8700",
    error="#cf222e",
    info="#0969da",
    user="#1a73e8",
    assistant="#8430ce",
    tool="#bf8700",
    tool_running="#bf8700",
    tool_success="#1a7f37",
    tool_error="#cf222e",
    system="#6a6a6a",
    spinner="#bf8700",
    verb="#1a1a1a",
)

CLAUDE = Palette(
    name="claude",
    text="#f5f5f0",
    text_muted="#a8a396",
    background="#1c1b18",
    surface="#24231e",
    surface_alt="#2d2b24",
    border="#3a372e",
    primary="#d97706",
    secondary="#c58af9",
    success="#a7c957",
    warning="#d97706",
    error="#e63946",
    info="#6a9fb5",
    user="#f5deb3",
    assistant="#d97706",
    tool="#d97706",
    tool_running="#d97706",
    tool_success="#a7c957",
    tool_error="#e63946",
    system="#a8a396",
    spinner="#d97706",
    verb="#f5f5f0",
)


_PALETTES: dict[str, Palette] = {
    "dark": DARK,
    "light": LIGHT,
    "claude": CLAUDE,
}


def list_theme_names() -> list[str]:
    """Return every selectable theme id, including ``auto``."""

    return ["auto", *_PALETTES.keys()]


def resolve_auto_theme(*, env: dict[str, str] | None = None) -> str:
    """Best-effort OS appearance detection for ``auto``.

    Mirrors the behaviour of ``watchSystemTheme`` in
    ``typescript/src/utils/theme.js`` but does not attempt to install
    any system-level watcher — we snapshot once at boot. Detection
    order:

    1. ``CLAWCODEX_THEME`` env var — explicit override, returned verbatim
       if it names a known palette.
    2. ``COLORFGBG`` (VTE / iTerm2) — trailing digit; ``0``/``dark``
       means dark surface.
    3. macOS ``defaults read -g AppleInterfaceStyle`` via subprocess —
       returns ``"dark"`` when Dark Mode is on.
    4. Fallback: ``"dark"``.
    """

    import os
    import subprocess

    environment = env if env is not None else os.environ

    forced = environment.get("CLAWCODEX_THEME", "").strip().lower()
    if forced and forced != "auto" and forced in _PALETTES:
        return forced

    cfgbg = environment.get("COLORFGBG", "").strip()
    if cfgbg:
        try:
            trailing = cfgbg.split(";")[-1].strip()
            bg = int(trailing)
            # Low numbers (0-6) are generally dark; 7-15 bright.
            return "dark" if bg < 7 else "light"
        except (ValueError, IndexError):
            pass

    if environment.get("__CFBundleIdentifier") or environment.get("TERM_PROGRAM"):
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            if out.returncode == 0 and "dark" in out.stdout.lower():
                return "dark"
            if out.returncode != 0:
                # The key is absent when Light Mode is active.
                return "light"
        except Exception:
            pass

    return "dark"


def get_palette(name: str | None, *, env: dict[str, str] | None = None) -> Palette:
    """Return a palette by name with graceful fallback.

    ``auto`` runs :func:`resolve_auto_theme` to pick between ``dark``
    and ``light`` at boot. Unknown names fall back to ``dark``.
    """

    if not name:
        return DARK
    key = name.strip().lower()
    if key == "auto":
        return _PALETTES.get(resolve_auto_theme(env=env), DARK)
    return _PALETTES.get(key, DARK)


def textual_css_overrides(palette: Palette) -> str:
    """Textual ``App.CSS`` overrides that map the palette onto the theme
    variables referenced throughout the widget CSS.

    Textual exposes ``$primary``, ``$background`` etc. via
    ``ColorSystem``; for Phase 1 we keep this as a flat CSS block so the
    widgets can reference raw palette colors alongside built-ins without
    fighting ``textual.theme`` subclassing.
    """

    return f"""
    Screen {{
        background: {palette.background};
        color: {palette.text};
    }}
    """

"""Load custom output styles from a directory of markdown files.

Phase-9 of the ch13 refactor (gap #7) closes the gap between the prior
plain-body-only loader and chapter
``typescript/src/outputStyles/loadOutputStylesDir.ts``:

* YAML frontmatter parsing — ``name`` / ``description`` / ``model``
  override the defaults derived from the file basename. Reuses the
  existing :mod:`src.skills.frontmatter` parser; no new dependency.
* Default ``~/.claude/outputStyles/`` lookup — ``resolve_output_style``
  with ``search_dir=None`` now consults the user-config directory before
  falling back to built-ins. Previously the ``None`` path returned
  built-ins only.
* Built-ins remain the same (``default``, ``explanatory``) — user files
  with a colliding ``name`` win, mirroring the chapter's "user overrides
  built-in" semantic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.skills.frontmatter import parse_frontmatter

from .styles import BUILTIN_OUTPUT_STYLES, OutputStyle


_logger = logging.getLogger(__name__)


def _user_style_dirs() -> list[Path]:
    """User output-style directories, canon first (OS-1 G4).

    Primary: ``GLOBAL_CONFIG_DIR/outputStyles`` (this port's config home —
    the INTEG-1 canon rule; lazy import so test re-points are honored).
    Legacy fallback: ``~/.claude/outputStyles`` — the TS config home this
    loader originally copied; kept readable for continuity, canon wins on
    name collisions. Lazy so tests that monkeypatch ``HOME`` /
    ``GLOBAL_CONFIG_DIR`` after import see the override.
    """
    from src.config import GLOBAL_CONFIG_DIR

    return [
        Path(GLOBAL_CONFIG_DIR) / "outputStyles",
        Path("~/.claude/outputStyles").expanduser(),
    ]


def _load_default_user_styles() -> dict[str, OutputStyle]:
    """Merged builtins + legacy-dir + canon-dir styles (canon wins)."""
    canon, legacy = _user_style_dirs()
    styles: dict[str, OutputStyle] = dict(BUILTIN_OUTPUT_STYLES)
    for directory in (legacy, canon):  # canon merged LAST → wins collisions
        if directory.is_dir():
            styles.update(load_output_styles_dir(directory))
    return styles


def load_output_styles_dir(path: str | Path) -> dict[str, OutputStyle]:
    """Load every ``*.md`` under ``path`` and merge with built-ins.

    User-supplied files whose ``name`` collides with a built-in win.
    Files without YAML frontmatter still load (using ``file.stem`` as
    the default name and the entire body as the prompt) so legacy users
    who hadn't adopted frontmatter aren't locked out.
    """

    root = Path(path).expanduser().resolve()
    styles: dict[str, OutputStyle] = dict(BUILTIN_OUTPUT_STYLES)
    if not root.exists() or not root.is_dir():
        return styles

    for file in sorted(root.glob("*.md")):
        try:
            raw = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _logger.warning("could not read output style at %s: %s", file, exc)
            continue
        if not raw.strip():
            continue

        result = parse_frontmatter(raw)
        meta = result.frontmatter or {}
        body = result.body.strip()

        # ``name`` precedence: explicit frontmatter > file stem.
        name = _coerce_str(meta.get("name")) or file.stem
        prompt_field = _coerce_str(meta.get("prompt"))
        # ``prompt`` precedence: explicit frontmatter > body content.
        # Frontmatter ``prompt`` lets a user keep documentation in the
        # body without shipping it to the model.
        prompt = prompt_field if prompt_field else body
        if not prompt:
            continue

        styles[name] = OutputStyle(
            name=name,
            prompt=prompt,
            source_path=file,
            description=_coerce_str(meta.get("description")),
            model=_coerce_str(meta.get("model")),
        )
    return styles


def resolve_output_style(
    name: str | None,
    search_dir: str | Path | None = None,
) -> OutputStyle:
    """Look up an output style by name; auto-discover the user dir.

    Resolution order:

    1. ``search_dir`` if explicitly given.
    2. Default ``~/.claude/outputStyles/`` if it exists.
    3. Built-ins (``default``, ``explanatory``).

    Falls back to the ``"default"`` style when ``name`` is not known —
    matching the pre-Phase-9 behavior.
    """

    if search_dir is not None:
        styles = load_output_styles_dir(search_dir)
    else:
        styles = _load_default_user_styles()

    key = (name or "default").strip() or "default"
    return styles.get(key, styles["default"])


def _coerce_str(value: object) -> str | None:
    """Return ``str(value).strip()`` when ``value`` is a non-empty string."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


__all__ = [
    "load_output_styles_dir",
    "resolve_output_style",
]

def output_style_from_settings(cwd: str | None = None) -> str | None:
    """The startup producer (OS-1 G1): ``settings.output_style.style`` →
    the style name the live context should carry, or ``None`` for default.

    TS reads ``settings?.outputStyle`` at prompt-build time
    (constants/outputStyles.ts:207); this port carries the style on the
    tool context, so entrypoints call this once at context construction.
    Never raises (a broken settings file must not block startup).
    """
    try:
        from src.settings.settings import load_settings

        style = load_settings(cwd=cwd).output_style.style
        if style and style != "default":
            return str(style)
    except Exception:  # noqa: BLE001
        _logger.debug("output_style_from_settings failed", exc_info=True)
    return None

def available_output_styles(search_dir: str | Path | None = None) -> list[str]:
    """Names of all resolvable styles — builtins ∪ user files. OS-1 W3:
    feeds the set_output_style validation/reply and the client
    /output-style listing. Mirrors resolve_output_style's directory
    resolution exactly (same styles resolvable = same names listed)."""
    try:
        styles = (
            load_output_styles_dir(search_dir)
            if search_dir is not None
            else _load_default_user_styles()
        )
        return list(styles)
    except Exception:  # noqa: BLE001 — listing is best-effort
        _logger.debug("available_output_styles: scan failed", exc_info=True)
        return list(BUILTIN_OUTPUT_STYLES)


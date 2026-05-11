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


def _default_user_dir() -> Path:
    """Resolve the default user output-styles directory lazily.

    Lazy so test environments that monkeypatch ``HOME`` after import see
    the override. Mirrors the keybindings-loader pattern.
    """

    return Path("~/.claude/outputStyles").expanduser()


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
        default_dir = _default_user_dir()
        if default_dir.is_dir():
            styles = load_output_styles_dir(default_dir)
        else:
            styles = dict(BUILTIN_OUTPUT_STYLES)

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

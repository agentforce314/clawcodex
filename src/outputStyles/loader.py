from __future__ import annotations

from pathlib import Path

from .styles import BUILTIN_OUTPUT_STYLES, OutputStyle


def load_output_styles_dir(path: str | Path) -> dict[str, OutputStyle]:
    root = Path(path).expanduser().resolve()
    styles: dict[str, OutputStyle] = dict(BUILTIN_OUTPUT_STYLES)
    if not root.exists() or not root.is_dir():
        return styles

    for file in sorted(root.glob("*.md")):
        name = file.stem
        try:
            prompt = file.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if not prompt:
            continue
        styles[name] = OutputStyle(name=name, prompt=prompt, source_path=file)
    return styles


def resolve_output_style(name: str | None, search_dir: str | Path | None = None) -> OutputStyle:
    if search_dir is None:
        styles = dict(BUILTIN_OUTPUT_STYLES)
    else:
        styles = load_output_styles_dir(search_dir)
    key = (name or "default").strip() or "default"
    return styles.get(key, styles["default"])


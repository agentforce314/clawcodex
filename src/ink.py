from __future__ import annotations


def render_markdown_panel(text: str) -> str:
    border = '=' * 40
    return f"{border}\n{text}\n{border}"

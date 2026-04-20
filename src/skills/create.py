from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def create_skill(
    *,
    directory: str | Path,
    name: str,
    description: str,
    when_to_use: Optional[str] = None,
    allowed_tools: Optional[Sequence[str]] = None,
    arguments: Optional[Sequence[str]] = None,
    user_invocable: bool = True,
    disable_model_invocation: bool = False,
    context: str = "inline",
    agent: Optional[str] = None,
    version: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    paths: Optional[Sequence[str]] = None,
    body: str = "",
) -> Path:
    base = Path(directory).expanduser().resolve()
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    fm: dict[str, Any] = {
        "description": description,
        "user-invocable": user_invocable,
        "disable-model-invocation": disable_model_invocation,
    }
    if when_to_use is not None:
        fm["when_to_use"] = when_to_use
    if allowed_tools:
        fm["allowed-tools"] = list(allowed_tools)
    if arguments:
        fm["arguments"] = list(arguments)
    if context and context != "inline":
        fm["context"] = context
    if agent is not None:
        fm["agent"] = agent
    if version is not None:
        fm["version"] = version
    if model is not None:
        fm["model"] = model
    if effort is not None:
        fm["effort"] = effort
    if paths:
        fm["paths"] = list(paths)

    content = _render_frontmatter(fm) + "\n" + (body or "")
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def _render_frontmatter(fm: Mapping[str, Any]) -> str:
    lines: list[str] = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)

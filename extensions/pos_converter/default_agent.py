"""Default Agent 替换机制 — 启动时自动检测总览 Agent。

优先级（高→低）:
  1. ``--agent <agent-type>`` CLI 显式指定
  2. ``.claude/agents/clawcodex-overview.md`` 自动检测
  3. ``GENERAL_PURPOSE_AGENT`` 当前默认行为（兜底）

总览 Agent 的 system prompt 以 ``append_system_prompt`` 形式注入，
保留 ``build_full_system_prompt()`` 的所有标准节。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Name convention for the overview agent
OVERVIEW_AGENT_NAME = "clawcodex-overview"


def resolve_default_agent(cwd: str | Path = ".") -> dict[str, Any] | None:
    """扫描工作目录下的默认总览 Agent。

    检测 ``.claude/agents/clawcodex-overview.md`` 是否存在，
    如果存在则解析并返回其 frontmatter + body。

    Parameters
    ----------
    cwd : str | Path
        工作目录（默认为当前目录）。

    Returns
    -------
    dict | None
        ``{"name", "description", "model", "tools", "skills",
        "system_prompt_body"}`` 或 None（未找到时）。
    """
    agents_dir = Path(cwd).resolve() / ".claude" / "agents"
    overview_file = agents_dir / f"{OVERVIEW_AGENT_NAME}.md"

    if not overview_file.is_file():
        return None

    return _parse_agent_file(overview_file)


def resolve_agent_by_type(
    cwd: str | Path,
    agent_type: str,
) -> dict[str, Any] | None:
    """按 agent type（frontmatter name）查找 agent 定义。

    扫描 ``.claude/agents/*.md``，返回匹配的第一个 agent。

    Parameters
    ----------
    cwd : str | Path
        工作目录。
    agent_type : str
        frontmatter ``name`` 字段的值。

    Returns
    -------
    dict | None
        匹配的 agent 定义，或 None。
    """
    agents_dir = Path(cwd).resolve() / ".claude" / "agents"
    if not agents_dir.is_dir():
        return None

    for md_file in _list_markdown_files(agents_dir):
        try:
            frontmatter, body = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
            if frontmatter.get("name") == agent_type:
                return {
                    "name": frontmatter.get("name", agent_type),
                    "description": frontmatter.get("description", ""),
                    "model": frontmatter.get("model"),
                    "tools": frontmatter.get("tools", []),
                    "skills": frontmatter.get("skills", []),
                    "system_prompt_body": body,
                }
        except (ValueError, OSError) as exc:
            logger.warning("Failed to parse %s: %s", md_file, exc)
            continue

    return None


def _parse_agent_file(file_path: Path) -> dict[str, Any]:
    """解析 agent markdown 文件，返回 frontmatter + body。"""
    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(content)
    return {
        "name": frontmatter.get("name", file_path.stem),
        "description": frontmatter.get("description", ""),
        "model": frontmatter.get("model"),
        "tools": frontmatter.get("tools", []),
        "skills": frontmatter.get("skills", []),
        "system_prompt_body": body,
    }


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 YAML-like frontmatter（--- 分隔符内）。

    Parameters
    ----------
    content : str
        markdown 文件完整内容。

    Returns
    -------
    (frontmatter_dict, body_str)
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, content

    # Find closing ---
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return {}, content

    frontmatter_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1:]).strip()

    frontmatter: dict[str, Any] = {}
    current_key: str | None = None

    for line in frontmatter_lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check if this is a list item continuation
        if stripped.startswith("- ") and current_key is not None:
            value = stripped[2:].strip()
            if isinstance(frontmatter.get(current_key), list):
                frontmatter[current_key].append(value)
            else:
                frontmatter.setdefault(current_key, []).append(value)
            continue

        # Check for dict value (indented key: value)
        if line.startswith(" ") and ":" in stripped and current_key is not None:
            k, _, v = stripped.partition(":")
            if isinstance(frontmatter.get(current_key), dict):
                frontmatter[current_key][k.strip()] = v.strip()
            continue

        # Key: value pair
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            value = value.strip()
            # If value is empty, this might be a list or dict start
            if not value:
                frontmatter[current_key] = []
            else:
                # Try to parse as YAML-like scalar
                frontmatter[current_key] = value

    # Normalize: if "tools" is a list, keep as-is; if it's a single string, wrap it
    for key in ("tools", "skills"):
        if key in frontmatter and isinstance(frontmatter[key], str):
            frontmatter[key] = [frontmatter[key]]

    return frontmatter, body


def _list_markdown_files(agents_dir: Path) -> list[Path]:
    """列出目录下所有 markdown 文件。"""
    if not agents_dir.is_dir():
        return []
    return sorted(agents_dir.rglob("*.md"))

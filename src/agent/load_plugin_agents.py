"""Load agent definitions exposed by enabled plugins.

Mirrors ``loadPluginAgents`` in
typescript/src/utils/plugins/loadPluginAgents.ts. For each enabled
plugin with a non-empty ``agents_paths``, walks the directory
recursively for ``*.md`` files, parses each via ``parse_agent_from_markdown``,
and namespaces the resulting ``agent_type`` as
``"<plugin-name>:<sub:dirs>:<base>"`` so nested folders cannot collide.

Plugin agents intentionally drop ``permission_mode``, ``hooks``, and
``mcp_servers`` from the parsed definition — those grant capabilities
beyond install-time trust and must come from user-controlled settings,
not third-party plugin manifests.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from src.agent.agent_definitions import AgentDefinition
from src.agent.parse_agent_markdown import parse_agent_from_markdown
from src.plugins.types import LoadedPlugin
from src.skills.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


def _scan_md_files(directory: str) -> list[tuple[str, str]]:
    """Recursively list ``*.md`` files under ``directory``.

    Returns ``(absolute_file_path, relative_namespace)`` pairs where
    ``relative_namespace`` is the parent-dir path relative to
    ``directory``, with separators turned into ``:`` (so a file at
    ``<dir>/foo/bar.md`` yields namespace ``"foo"``). Files directly
    under ``directory`` yield ``""``.
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    out: list[tuple[str, str]] = []
    try:
        for path in base.rglob("*.md"):
            if not path.is_file():
                continue
            rel = path.parent.relative_to(base)
            namespace = ":".join(rel.parts) if rel.parts else ""
            out.append((str(path), namespace))
    except (OSError, PermissionError):
        return []
    return sorted(out)


def _build_namespaced_agent_type(
    plugin_name: str, namespace: str, base_name: str,
) -> str:
    parts = [plugin_name]
    if namespace:
        parts.append(namespace)
    parts.append(base_name)
    return ":".join(parts)


def load_plugin_agents(plugins: list[LoadedPlugin]) -> list[AgentDefinition]:
    """Return all agent definitions discovered across the given plugins.

    Agent types are namespaced as ``<plugin>:<sub:dirs>:<base>`` to
    mirror the TS ``walkPluginMarkdown`` convention — without the
    ``<sub:dirs>`` segment, plugins shipping multiple agents named
    ``review.md`` in different folders would silently collide.
    """
    agents: list[AgentDefinition] = []
    for plugin in plugins:
        if not plugin.enabled or not plugin.agents_paths:
            continue
        for agents_dir in plugin.agents_paths:
            for file_path, namespace in _scan_md_files(agents_dir):
                try:
                    content = Path(file_path).read_text(encoding="utf-8")
                except (OSError, PermissionError, UnicodeDecodeError) as exc:
                    logger.debug(
                        "plugin %s: failed to read %s: %s",
                        plugin.name, file_path, exc,
                    )
                    continue
                parsed = parse_frontmatter(content)
                agent = parse_agent_from_markdown(
                    file_path=file_path,
                    frontmatter=parsed.frontmatter,
                    body=parsed.body,
                    source="plugin",
                    base_dir=plugin.path,
                )
                if agent is None:
                    continue
                namespaced = replace(
                    agent,
                    agent_type=_build_namespaced_agent_type(
                        plugin.name, namespace, agent.agent_type,
                    ),
                    source="plugin",
                    # Strip elevated capabilities: plugins cannot grant
                    # permission overrides, hooks, or MCP servers.
                    permission_mode=None,
                    hooks=None,
                    mcp_servers=None,
                )
                agents.append(namespaced)
    return agents

"""Discover and merge custom agent definitions from disk + plugins.

Port of ``getAgentDefinitionsWithOverrides`` in
typescript/src/tools/AgentTool/loadAgentsDir.ts. Combines built-in agents
(``src/agent/agent_definitions.py:get_built_in_agents``), plugin agents
(via ``load_plugin_agents``), and on-disk custom agents from managed /
user / project directories (via ``load_markdown_files_for_subdir``).

Last-wins merge order on duplicate ``agent_type``:
    [built-in, plugin, user, project, flag, managed]

``flag`` agents come from the SDK ``initialize`` control request, via
``register_sdk_agents`` — they're the Python equivalent of TS's
``flagSettings`` source.

A module-level cache keyed on cwd avoids re-walking the filesystem on
every prompt build. Call ``clear_agent_definitions_cache()`` after a
known on-disk change (e.g., the user edits ``~/.claude/agents/foo.md``)
to force a refresh.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from src.agent.agent_definitions import AgentDefinition, get_built_in_agents
from src.agent.parse_agent_markdown import parse_agent_from_markdown
from src.utils.markdown_config_loader import (
    SOURCE_MANAGED,
    SOURCE_PROJECT,
    SOURCE_USER,
    load_markdown_files_for_subdir,
)

logger = logging.getLogger(__name__)


# Each disk source maps to the matching ``AgentSource`` literal so
# downstream consumers can distinguish a managed-policy agent from a
# user one (e.g., to enforce "managed cannot be overridden by user").
_SOURCE_TO_AGENT_SOURCE: dict[str, str] = {
    SOURCE_MANAGED: "managed",
    SOURCE_USER: "user",
    SOURCE_PROJECT: "project",
}

# Priority order for last-wins merge — earlier entries are overridden by
# later ones if they share an agent_type. ``flag`` agents come from the
# SDK ``initialize`` control request (mirrors TS ``flagSettings``).
_MERGE_ORDER: tuple[str, ...] = (
    "built-in",
    "plugin",
    SOURCE_USER,
    SOURCE_PROJECT,
    "flag",
    SOURCE_MANAGED,
)


# Agents registered programmatically by the SDK bridge's ``initialize``
# handler. Treated as ``flagSettings`` source in TS terms — they can
# override built-ins / plugin / user / project agents but not policy.
_sdk_flag_agents: list[AgentDefinition] = []


def register_sdk_agents(agents: list[AgentDefinition]) -> None:
    """Replace the SDK-injected agent set (clears the cache as a side effect)."""
    _sdk_flag_agents[:] = list(agents)
    clear_agent_definitions_cache()


def get_sdk_agents() -> list[AgentDefinition]:
    return list(_sdk_flag_agents)


def clear_sdk_agents() -> None:
    _sdk_flag_agents.clear()
    clear_agent_definitions_cache()


def _is_restricted_to_plugin_only(scope: str) -> bool:
    """Stub for TS ``isRestrictedToPluginOnly(scope)`` policy gate.

    TS reads ``policySettings.strictPluginOnlyCustomization`` (boolean or
    list of scopes) to decide whether user/project agents are loaded.
    When the policy is set for ``agents``, only built-in / plugin /
    managed sources contribute — user and project are skipped.

    The Python settings layer doesn't surface that policy yet; this stub
    returns False so behaviour matches today's loader. Plumbing future
    policy reads through here means call sites won't change when the
    real check lands.
    """
    _ = scope  # documented hook for the future policy reader
    return False


# Cache is keyed on ``os.path.realpath(cwd)`` so symlinked / trailing-slash
# variants of the same project collapse into a single entry. The cache is
# session-bound; SDK callers that hop between unrelated projects can grow
# it unboundedly — acceptable for now since per-cwd discovery is cheap.
_agent_dir_cache: dict[str, list[AgentDefinition]] = {}


def _cache_key(cwd: str) -> str:
    try:
        return os.path.realpath(cwd)
    except (OSError, ValueError):
        return cwd


def clear_agent_definitions_cache() -> None:
    """Drop the discovery cache. Call after on-disk agent changes."""
    _agent_dir_cache.clear()


def get_active_agents_from_list(
    agents: Iterable[AgentDefinition],
) -> list[AgentDefinition]:
    """Last-wins dedup by ``agent_type`` while preserving input order.

    Mirrors ``getActiveAgentsFromList`` from loadAgentsDir.ts:192-220.
    Callers are responsible for arranging input order so the desired
    override priority is honoured (lowest priority first, highest last).
    """
    by_type: dict[str, AgentDefinition] = {}
    order: list[str] = []
    for agent in agents:
        if agent.agent_type not in by_type:
            order.append(agent.agent_type)
        by_type[agent.agent_type] = agent
    return [by_type[t] for t in order]


def _load_custom_agents(cwd: str) -> dict[str, list[AgentDefinition]]:
    """Group disk-discovered agents by their disk source label."""
    grouped: dict[str, list[AgentDefinition]] = {
        SOURCE_USER: [],
        SOURCE_PROJECT: [],
        SOURCE_MANAGED: [],
    }
    files = load_markdown_files_for_subdir("agents", cwd)
    for md in files:
        agent_source = _SOURCE_TO_AGENT_SOURCE.get(md.source, "user")
        agent = parse_agent_from_markdown(
            file_path=md.file_path,
            frontmatter=md.frontmatter,
            body=md.body,
            source=agent_source,  # type: ignore[arg-type]
            base_dir=md.base_dir,
        )
        if agent is None:
            continue
        grouped[md.source].append(agent)
    return grouped


def get_agent_definitions_with_overrides(cwd: str) -> list[AgentDefinition]:
    """Return the merged list of agents visible from ``cwd``.

    Cache-keyed on ``cwd``. Built-ins are always included; the user can
    override a built-in by defining an agent with the same ``agent_type``.
    On any unexpected loader error the built-ins are returned alone — a
    broken custom agent file should never disable the model's ability to
    spawn the built-in agents.
    """
    key = _cache_key(cwd)
    cached = _agent_dir_cache.get(key)
    if cached is not None:
        return list(cached)

    try:
        builtins = list(get_built_in_agents())
        try:
            from src.agent.load_plugin_agents import load_plugin_agents
            from src.plugins import get_loaded_plugins
            plugin_agents = load_plugin_agents(get_loaded_plugins())
        except Exception:
            logger.exception("plugin agent loading failed; continuing without plugin agents")
            plugin_agents = []

        custom = _load_custom_agents(cwd)
        plugin_only = _is_restricted_to_plugin_only("agents")
        user_agents = [] if plugin_only else custom[SOURCE_USER]
        project_agents = [] if plugin_only else custom[SOURCE_PROJECT]
        flag_agents = [] if plugin_only else list(_sdk_flag_agents)

        sources_in_order: dict[str, list[AgentDefinition]] = {
            "built-in": builtins,
            "plugin": plugin_agents,
            SOURCE_USER: user_agents,
            SOURCE_PROJECT: project_agents,
            "flag": flag_agents,
            SOURCE_MANAGED: custom[SOURCE_MANAGED],
        }
        flat: list[AgentDefinition] = []
        for source_key in _MERGE_ORDER:
            flat.extend(sources_in_order.get(source_key, []))

        active = get_active_agents_from_list(flat)
        _agent_dir_cache[key] = active
        return list(active)
    except Exception:
        logger.exception("agent discovery failed; falling back to built-ins")
        return list(get_built_in_agents())

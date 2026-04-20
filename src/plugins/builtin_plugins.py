from __future__ import annotations

from typing import Any

from src.skills.model import Skill

from .types import BuiltinPluginDefinition, LoadedPlugin, PluginManifest

BUILTIN_MARKETPLACE_NAME = "builtin"

_builtin_plugins: dict[str, BuiltinPluginDefinition] = {}


def register_builtin_plugin(definition: BuiltinPluginDefinition) -> None:
    _builtin_plugins[definition.name] = definition


def is_builtin_plugin_id(plugin_id: str) -> bool:
    return plugin_id.endswith(f"@{BUILTIN_MARKETPLACE_NAME}")


def get_builtin_plugin_definition(
    name: str,
) -> BuiltinPluginDefinition | None:
    return _builtin_plugins.get(name)


def get_builtin_plugins() -> dict[str, list[LoadedPlugin]]:
    enabled: list[LoadedPlugin] = []
    disabled: list[LoadedPlugin] = []

    for name, definition in _builtin_plugins.items():
        if definition.is_available and not definition.is_available():
            continue

        plugin_id = f"{name}@{BUILTIN_MARKETPLACE_NAME}"
        is_enabled = definition.default_enabled

        plugin = LoadedPlugin(
            name=name,
            manifest=PluginManifest(
                name=name,
                description=definition.description,
                version=definition.version,
            ),
            path=BUILTIN_MARKETPLACE_NAME,
            source=plugin_id,
            repository=plugin_id,
            enabled=is_enabled,
            is_builtin=True,
            hooks_config=definition.hooks,
            mcp_servers=definition.mcp_servers,
        )

        if is_enabled:
            enabled.append(plugin)
        else:
            disabled.append(plugin)

    return {"enabled": enabled, "disabled": disabled}


def get_builtin_plugin_skill_commands() -> list[Skill]:
    result = get_builtin_plugins()
    skills: list[Skill] = []

    for plugin in result["enabled"]:
        definition = _builtin_plugins.get(plugin.name)
        if not definition or not definition.skills:
            continue
        for skill_def in definition.skills:
            if isinstance(skill_def, Skill):
                skills.append(skill_def)

    return skills


def clear_builtin_plugins() -> None:
    _builtin_plugins.clear()

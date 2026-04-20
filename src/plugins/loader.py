from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import LoadedPlugin, PluginError, PluginManifest
from .validator import validate_manifest

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "plugin.json"

TRUST_LEVELS = ("bundled", "managed", "user", "project", "mcp")


@dataclass
class PluginDiscoveryResult:
    plugins: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)


_loaded_plugins: dict[str, LoadedPlugin] = {}


def discover_plugins(directory: str | Path) -> PluginDiscoveryResult:
    result = PluginDiscoveryResult()
    base = Path(directory)
    if not base.is_dir():
        return result

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / MANIFEST_FILENAME
        if not manifest_path.exists():
            continue

        try:
            plugin = load_plugin_from_directory(entry)
            result.plugins.append(plugin)
        except PluginError as e:
            result.errors.append(e)
        except Exception as e:
            result.errors.append(PluginError(entry.name, str(e)))

    return result


def load_plugin_from_directory(
    plugin_dir: str | Path,
    *,
    source: str = "user",
) -> LoadedPlugin:
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise PluginError(plugin_dir.name, f"No {MANIFEST_FILENAME} found")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PluginError(plugin_dir.name, f"Failed to read manifest: {e}") from e

    errors = validate_manifest(raw)
    if errors:
        raise PluginError(
            plugin_dir.name,
            f"Invalid manifest: {'; '.join(e.message for e in errors)}",
        )

    manifest = PluginManifest(
        name=raw["name"],
        description=raw.get("description", ""),
        version=raw.get("version", "1.0.0"),
    )

    plugin = LoadedPlugin(
        name=manifest.name,
        manifest=manifest,
        path=str(plugin_dir),
        source=source,
        repository=raw.get("repository", ""),
        enabled=raw.get("enabled", True),
        hooks_config=raw.get("hooks"),
        mcp_servers=raw.get("mcp_servers"),
    )

    return plugin


def register_plugin(plugin: LoadedPlugin) -> None:
    _loaded_plugins[plugin.name] = plugin
    logger.debug("Registered plugin: %s", plugin.name)


def unregister_plugin(name: str) -> bool:
    if name in _loaded_plugins:
        del _loaded_plugins[name]
        return True
    return False


def get_loaded_plugins() -> list[LoadedPlugin]:
    return list(_loaded_plugins.values())


def get_loaded_plugin(name: str) -> LoadedPlugin | None:
    return _loaded_plugins.get(name)


def get_enabled_plugins() -> list[LoadedPlugin]:
    return [p for p in _loaded_plugins.values() if p.enabled]


def load_plugins_from_directories(
    directories: list[str | Path],
    *,
    source: str = "user",
) -> PluginDiscoveryResult:
    combined = PluginDiscoveryResult()
    for directory in directories:
        result = discover_plugins(directory)
        for plugin in result.plugins:
            plugin.source = source
            register_plugin(plugin)
            combined.plugins.append(plugin)
        combined.errors.extend(result.errors)
    return combined


def clear_loaded_plugins() -> None:
    _loaded_plugins.clear()

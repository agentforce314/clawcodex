from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import load_plugin_from_directory, register_plugin
from .types import LoadedPlugin, PluginError, PluginManifest
from .validator import validate_manifest

logger = logging.getLogger(__name__)


@dataclass
class MarketplaceEntry:
    name: str
    description: str
    version: str
    repository: str = ""
    author: str = ""
    downloads: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class MarketplaceIndex:
    entries: list[MarketplaceEntry] = field(default_factory=list)
    last_updated: str = ""


_index: MarketplaceIndex | None = None


def load_marketplace_index(index_path: str | Path) -> MarketplaceIndex:
    global _index
    path = Path(index_path)
    if not path.exists():
        _index = MarketplaceIndex()
        return _index

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _index = MarketplaceIndex()
        return _index

    entries: list[MarketplaceEntry] = []
    for item in raw.get("plugins", []):
        entries.append(MarketplaceEntry(
            name=item.get("name", ""),
            description=item.get("description", ""),
            version=item.get("version", "1.0.0"),
            repository=item.get("repository", ""),
            author=item.get("author", ""),
            downloads=item.get("downloads", 0),
            tags=item.get("tags", []),
        ))

    _index = MarketplaceIndex(
        entries=entries,
        last_updated=raw.get("last_updated", ""),
    )
    return _index


def get_marketplace_index() -> MarketplaceIndex | None:
    return _index


def search_marketplace(
    query: str,
    *,
    tags: list[str] | None = None,
) -> list[MarketplaceEntry]:
    if _index is None:
        return []

    query_lower = query.lower()
    results: list[MarketplaceEntry] = []

    for entry in _index.entries:
        if query_lower in entry.name.lower() or query_lower in entry.description.lower():
            if tags:
                if not any(t in entry.tags for t in tags):
                    continue
            results.append(entry)

    return results


def list_marketplace(
    *,
    sort_by: str = "name",
    limit: int = 50,
) -> list[MarketplaceEntry]:
    if _index is None:
        return []

    entries = list(_index.entries)
    if sort_by == "downloads":
        entries.sort(key=lambda e: e.downloads, reverse=True)
    elif sort_by == "name":
        entries.sort(key=lambda e: e.name)

    return entries[:limit]


def install_plugin(
    source_dir: str | Path,
    target_dir: str | Path,
    plugin_name: str,
) -> LoadedPlugin:
    source = Path(source_dir) / plugin_name
    target = Path(target_dir) / plugin_name

    if not source.is_dir():
        raise PluginError(plugin_name, f"Plugin source directory not found: {source}")

    manifest_path = source / "plugin.json"
    if not manifest_path.exists():
        raise PluginError(plugin_name, "No plugin.json found in source")

    target.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    plugin = load_plugin_from_directory(target, source="marketplace")
    register_plugin(plugin)
    return plugin


def uninstall_plugin(
    plugin_dir: str | Path,
    plugin_name: str,
) -> bool:
    target = Path(plugin_dir) / plugin_name
    if not target.exists():
        return False

    shutil.rmtree(target)
    return True


def clear_marketplace_index() -> None:
    global _index
    _index = None

"""Configuration management for Claw Codex.

Three-level config hierarchy matching TypeScript config.ts:
  Global:  ~/.clawcodex/config.json
  Project: <git-root>/.claude/config.json
  Local:   <git-root>/.claude/config.local.json

Inheritance: local > project > global (deep merge).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_DIR = Path.home() / ".clawcodex"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.json"
HISTORY_FILE = GLOBAL_CONFIG_DIR / "history.jsonl"

PROJECT_CONFIG_DIR_NAME = ".claude"
PROJECT_CONFIG_FILE_NAME = "config.json"
LOCAL_CONFIG_FILE_NAME = "config.local.json"


def _find_git_root(cwd: str | Path | None = None) -> Path | None:
    """Find the git root directory starting from *cwd*."""
    start = Path(cwd) if cwd else Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=str(start), timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def get_global_config_path() -> Path:
    return GLOBAL_CONFIG_FILE


def get_project_config_path(cwd: str | Path | None = None) -> Path | None:
    root = _find_git_root(cwd)
    if root is None:
        return None
    return root / PROJECT_CONFIG_DIR_NAME / PROJECT_CONFIG_FILE_NAME


def get_local_config_path(cwd: str | Path | None = None) -> Path | None:
    root = _find_git_root(cwd)
    if root is None:
        return None
    return root / PROJECT_CONFIG_DIR_NAME / LOCAL_CONFIG_FILE_NAME


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Atomic JSON I/O
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning empty dict on any error."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.debug("Failed to read config %s: %s", path, exc)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via temp-file + rename, chmod 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, str(path))
        if os.name != "nt":
            os.chmod(str(path), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

def get_default_config() -> dict[str, Any]:
    """Generate default global configuration."""
    try:
        from src.providers import PROVIDER_INFO
        providers = {
            name: {
                "api_key": "",
                "base_url": info["default_base_url"],
                "default_model": info["default_model"],
            }
            for name, info in PROVIDER_INFO.items()
        }
    except Exception:
        providers = {}

    return {
        "default_provider": "anthropic",
        "providers": providers,
        "session": {"auto_save": True, "max_history": 100},
    }


# ---------------------------------------------------------------------------
# ConfigManager — three-level loading + merge
# ---------------------------------------------------------------------------

@dataclass
class ConfigManager:
    """Manages the three-level config hierarchy."""

    cwd: str | Path | None = None
    _global_cache: dict[str, Any] | None = field(default=None, repr=False)
    _project_cache: dict[str, Any] | None = field(default=None, repr=False)
    _local_cache: dict[str, Any] | None = field(default=None, repr=False)

    def invalidate(self) -> None:
        self._global_cache = None
        self._project_cache = None
        self._local_cache = None

    # --- loaders ---

    def load_global(self) -> dict[str, Any]:
        if self._global_cache is None:
            path = get_global_config_path()
            on_disk = _read_json(path) if path.exists() else {}
            self._global_cache = _deep_merge(get_default_config(), on_disk)
        return dict(self._global_cache)

    def load_project(self) -> dict[str, Any]:
        if self._project_cache is None:
            path = get_project_config_path(self.cwd)
            self._project_cache = _read_json(path) if path else {}
        return dict(self._project_cache)

    def load_local(self) -> dict[str, Any]:
        if self._local_cache is None:
            path = get_local_config_path(self.cwd)
            self._local_cache = _read_json(path) if path else {}
        return dict(self._local_cache)

    def get_merged(self) -> dict[str, Any]:
        """Return fully merged config: global < project < local."""
        merged = self.load_global()
        merged = _deep_merge(merged, self.load_project())
        merged = _deep_merge(merged, self.load_local())
        return merged

    # --- writers ---

    def save_global(self, data: dict[str, Any]) -> None:
        _atomic_write_json(get_global_config_path(), data)
        self._global_cache = None

    def save_project(self, data: dict[str, Any]) -> None:
        path = get_project_config_path(self.cwd)
        if path is None:
            raise RuntimeError("No git root found — cannot save project config")
        _atomic_write_json(path, data)
        self._project_cache = None

    def save_local(self, data: dict[str, Any]) -> None:
        path = get_local_config_path(self.cwd)
        if path is None:
            raise RuntimeError("No git root found — cannot save local config")
        _atomic_write_json(path, data)
        self._local_cache = None

    # --- convenience ---

    def get(self, key: str, default: Any = None) -> Any:
        return self.get_merged().get(key, default)

    def set_global(self, key: str, value: Any) -> None:
        cfg = self.load_global()
        cfg[key] = value
        self.save_global(cfg)

    def set_project(self, key: str, value: Any) -> None:
        cfg = self.load_project()
        cfg[key] = value
        self.save_project(cfg)


# ---------------------------------------------------------------------------
# History (JSONL paste history)
# ---------------------------------------------------------------------------

def append_history_entry(content: str, *, source: str = "paste") -> None:
    """Append a history entry to the JSONL history file."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": time.time(), "source": source, "content": content}
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history_entries(limit: int = 100) -> list[dict[str, Any]]:
    """Read recent history entries."""
    if not HISTORY_FILE.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []
    return entries[-limit:]


# ---------------------------------------------------------------------------
# Backward-compatible API (used by existing code)
# ---------------------------------------------------------------------------

_default_manager: ConfigManager | None = None


def _get_default_manager() -> ConfigManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = ConfigManager()
    return _default_manager


def get_config_path() -> Path:
    """Get the path to the global configuration file."""
    return get_global_config_path()


def load_config() -> dict[str, Any]:
    """Load merged configuration."""
    return _get_default_manager().get_merged()


def save_config(config: dict[str, Any]) -> None:
    """Save configuration to global config file."""
    _get_default_manager().save_global(config)


def get_provider_config(provider: str) -> dict[str, Any]:
    """Get configuration for a specific provider."""
    config = load_config()
    providers = config.get("providers", {})
    if provider not in providers:
        raise ValueError(f"Unknown provider: {provider}")
    return providers[provider]


def set_api_key(
    provider: str,
    api_key: str,
    base_url: Optional[str] = None,
    default_model: Optional[str] = None,
) -> None:
    """Set API key for a provider."""
    mgr = _get_default_manager()
    config = mgr.load_global()
    if "providers" not in config:
        config["providers"] = {}
    if provider not in config["providers"]:
        config["providers"][provider] = {}
    config["providers"][provider]["api_key"] = api_key
    if base_url is not None:
        config["providers"][provider]["base_url"] = base_url
    if default_model is not None:
        config["providers"][provider]["default_model"] = default_model
    mgr.save_global(config)


def set_default_provider(provider: str) -> None:
    """Set the default provider."""
    mgr = _get_default_manager()
    config = mgr.load_global()
    config["default_provider"] = provider
    mgr.save_global(config)


def get_default_provider() -> str:
    """Get the default provider."""
    return load_config().get("default_provider", "anthropic")

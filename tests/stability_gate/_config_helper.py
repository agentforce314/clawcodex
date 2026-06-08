"""共享的配置辅助函数 — 用于稳定性门禁测试。

创建临时 .clawcodex/config.json 并重定向 ConfigManager。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def make_config(home_path: Path, provider: str = "anthropic") -> Path:
    """Create a fake .clawcodex/config.json at *home_path*.

    Returns the config file path.
    """
    config_dir = home_path / ".clawcodex"
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "default_provider": provider,
        "providers": {
            provider: {
                "api_key": "fake-stability-gate-key",
                "base_url": "https://api.anthropic.com/v1",
                "default_model": "claude-sonnet-4-20250514",
            }
        },
    }
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    return config_file


def redirect_global_config(config_file: Path):
    """Point ``ConfigManager`` at *config_file* and drop cached state.

    Returns the patcher (call ``.stop()`` in teardown).
    """
    import src.config as config_module

    patcher = patch.object(config_module, "GLOBAL_CONFIG_FILE", config_file)
    patcher.start()
    config_module._default_manager = None
    return patcher


def cleanup_config():
    """Reset ConfigManager singleton."""
    import src.config as config_module

    config_module._default_manager = None

"""Tests for src/secret_store.py — config-backed secret/API-key storage."""

from __future__ import annotations

import json

import pytest

import src.config as config_module
import src.secret_store as ss


# --- pure helpers ---------------------------------------------------------


def test_coerce_env_map_filters_non_strings():
    src = {
        "TAVILY_API_KEY": "tvly-x",
        "PORT": 8080,            # int -> str
        "RATIO": 1.5,            # float -> str
        "ENABLED": True,         # bool -> skipped (not a credential)
        "MISSING": None,         # None -> skipped
        "NESTED": {"a": 1},      # container -> skipped
        "": "noname",            # empty name -> skipped
    }
    out = ss._coerce_env_map(src)
    assert out == {"TAVILY_API_KEY": "tvly-x", "PORT": "8080", "RATIO": "1.5"}


def test_coerce_env_map_non_dict():
    assert ss._coerce_env_map(None) == {}
    assert ss._coerce_env_map(["x"]) == {}


# --- get_secret resolution order -----------------------------------------


def test_get_secret_env_wins_over_config(monkeypatch):
    monkeypatch.setenv("MYKEY", "from-env")
    monkeypatch.setattr(ss, "_config_env", lambda: {"MYKEY": "from-config"})
    assert ss.get_secret("MYKEY") == "from-env"


def test_get_secret_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("MYKEY", raising=False)
    monkeypatch.setattr(ss, "_config_env", lambda: {"MYKEY": "from-config"})
    assert ss.get_secret("MYKEY") == "from-config"


def test_get_secret_empty_env_uses_config(monkeypatch):
    monkeypatch.setenv("MYKEY", "   ")  # whitespace == unset
    monkeypatch.setattr(ss, "_config_env", lambda: {"MYKEY": "from-config"})
    assert ss.get_secret("MYKEY") == "from-config"


def test_get_secret_default_when_missing(monkeypatch):
    monkeypatch.delenv("MYKEY", raising=False)
    monkeypatch.setattr(ss, "_config_env", lambda: {})
    assert ss.get_secret("MYKEY") is None
    assert ss.get_secret("MYKEY", "fallback") == "fallback"


# --- global-tier scoping (ch02 round-3) -----------------------------------
# Env APPLICATION moved to permissions.trust_boundary; the read fallback
# here must see ONLY the global tier so untrusted project config can never
# feed get_secret consumers before the trust gate.


def test_config_env_reads_global_tier_only(monkeypatch):
    from unittest import mock

    monkeypatch.delenv("MYKEY", raising=False)
    monkeypatch.delenv("EVIL", raising=False)
    with mock.patch(
        "src.config.ConfigManager.load_global",
        return_value={"env": {"MYKEY": "from-global"}},
    ), mock.patch(
        "src.config.ConfigManager.load_project",
        return_value={"env": {"MYKEY": "from-project", "EVIL": "x"}},
    ) as mock_project:
        assert ss.get_secret("MYKEY") == "from-global"
        assert ss.get_secret("EVIL") is None
        mock_project.assert_not_called()


def test_list_secret_names(monkeypatch):
    monkeypatch.setattr(ss, "_config_env", lambda: {"Z": "1", "A": "2"})
    assert ss.list_secret_names() == ["A", "Z"]  # sorted


# --- set_secret / delete_secret round-trip through the global config ------


@pytest.fixture
def isolated_global_config(tmp_path, monkeypatch):
    """Point the global config at a tmp file; no project/local bleed."""
    cfg = tmp_path / ".clawcodex" / "config.json"
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_FILE", cfg)
    monkeypatch.setattr(config_module, "_default_manager", None)
    # Neutralize project/local discovery so the real repo's .claude/config.json
    # doesn't leak into the merged view.
    monkeypatch.setattr(config_module, "_find_git_root", lambda *a, **k: None)
    return cfg


def test_set_secret_writes_config_and_resolves(isolated_global_config, monkeypatch):
    monkeypatch.delenv("ROUNDTRIP_KEY", raising=False)
    ss.set_secret("ROUNDTRIP_KEY", "s3cret-value")

    # 1. persisted to the single config file under "env"
    data = json.loads(isolated_global_config.read_text(encoding="utf-8"))
    assert data["env"]["ROUNDTRIP_KEY"] == "s3cret-value"

    # 2. mirrored into the live process immediately
    import os

    assert os.environ["ROUNDTRIP_KEY"] == "s3cret-value"

    # 3. resolves from config even after the live mirror is cleared
    monkeypatch.delenv("ROUNDTRIP_KEY", raising=False)
    monkeypatch.setattr(config_module, "_default_manager", None)  # drop cache
    assert ss.get_secret("ROUNDTRIP_KEY") == "s3cret-value"
    assert "ROUNDTRIP_KEY" in ss.list_secret_names()


def test_set_secret_rejects_empty_name(isolated_global_config):
    with pytest.raises(ValueError):
        ss.set_secret("  ", "x")


def test_delete_secret(isolated_global_config, monkeypatch):
    monkeypatch.delenv("TO_DELETE", raising=False)
    ss.set_secret("TO_DELETE", "v")
    monkeypatch.setattr(config_module, "_default_manager", None)
    assert ss.delete_secret("TO_DELETE") is True

    monkeypatch.setattr(config_module, "_default_manager", None)
    data = json.loads(isolated_global_config.read_text(encoding="utf-8"))
    assert "TO_DELETE" not in data.get("env", {})
    # deleting an absent key is a no-op returning False
    monkeypatch.setattr(config_module, "_default_manager", None)
    assert ss.delete_secret("NEVER_EXISTED") is False

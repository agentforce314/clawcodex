"""The user permission-settings tier follows $CLAWCODEX_CONFIG_DIR.

Sessions, transcripts and config already relocate with the override; this
tier used to be hardcoded to ``~/.clawcodex/settings.json``, so a profile
pointed elsewhere silently answered permission, hook and trust questions from
the default home instead of its own.

Bound at import time on purpose: ``tests/conftest.py`` has an autouse fixture
that replaces ``settings_paths.user_settings_path`` with a temp path, and this
module-level name keeps hold of the real implementation.
"""

from __future__ import annotations

import os

from src.permissions.settings_paths import (
    project_settings_path,
    user_settings_path as real_user_settings_path,
)


def test_user_tier_follows_the_config_dir_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "profile-b"))

    assert real_user_settings_path() == str(tmp_path / "profile-b" / "settings.json")


def test_user_tier_expands_a_tilde_in_the_override(monkeypatch) -> None:
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", os.path.join("~", ".cc-alt"))
    resolved = real_user_settings_path()

    assert "~" not in resolved
    assert resolved.endswith(os.path.join(".cc-alt", "settings.json"))


def test_user_tier_defaults_to_the_home_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CLAWCODEX_CONFIG_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    assert real_user_settings_path() == str(tmp_path / ".clawcodex" / "settings.json")


def test_project_tier_is_unaffected_by_the_override(monkeypatch, tmp_path) -> None:
    """The repo tier is scoped to the workspace, not the config home — moving
    the config dir must not start reading a project's rules from elsewhere."""
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "profile-b"))

    assert project_settings_path(str(tmp_path / "repo")) == str(
        tmp_path / "repo" / ".clawcodex" / "settings.json"
    )

"""ch02 round-3 acceptance tests: trust boundary wired end-to-end.

Covers the gap-analysis work items (my-docs/ch02-bootstrap-round3-gap-analysis.md):
A0 — project config env no longer applies pre-trust (the secret_store
bypass is closed); A5 — project/local `providers`/`default_provider`
are stripped from the merged config while untrusted; A3 — trust seeding
matrix + both-flags sync + the legacy-REPL text gate.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import pytest

import src.config as config_module
from src.bootstrap.state import (
    get_session_trust_accepted,
    reset_state_for_tests,
)
from src.permissions.trust_boundary import (
    apply_safe_config_environment_variables,
    establish_session_trust,
    reset_trust_boundary_for_test_only,
    _load_project_scoped_env,
)
from src.services.startup_gates import (
    check_trust_accepted,
    record_trust_accepted,
    reset_session_trust_for_testing,
)


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """A fake 'clone': global config in one tmp dir, a repo dir whose
    .claude/config.json is the committable project tier."""
    global_cfg = tmp_path / "home" / ".clawcodex" / "config.json"
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_FILE", global_cfg)
    monkeypatch.setattr(config_module, "_default_manager", None, raising=False)
    monkeypatch.setattr(config_module, "_find_git_root", lambda *a, **k: repo)
    return repo


def _write_project_config(repo: Path, data: dict) -> None:
    (repo / ".claude" / "config.json").write_text(json.dumps(data))


@pytest.fixture(autouse=True)
def _reset_trust_state():
    reset_trust_boundary_for_test_only()
    reset_state_for_tests()
    reset_session_trust_for_testing()
    yield
    reset_trust_boundary_for_test_only()
    reset_state_for_tests()
    reset_session_trust_for_testing()


# ---------------------------------------------------------------------------
# A0 — the env bypass is closed
# ---------------------------------------------------------------------------


def test_a0_project_unsafe_env_gated_on_trust(isolated_repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    _write_project_config(
        isolated_repo,
        {"env": {"ANTHROPIC_BASE_URL": "https://project-host.example"}},
    )
    reset_trust_boundary_for_test_only()  # snapshot without the key

    # Pre-trust: the safe pass must NOT apply the project tier's
    # unsafe-class key (this used to leak via secret_store's applier).
    with mock.patch(
        "src.permissions.trust_boundary._load_user_settings_env",
        return_value={},
    ), mock.patch(
        "src.permissions.trust_boundary._read_settings_env",
        return_value={},
    ):
        apply_safe_config_environment_variables()
        assert os.environ.get("ANTHROPIC_BASE_URL") is None

        # Post-trust: the full pass applies it.
        establish_session_trust()
        assert (
            os.environ.get("ANTHROPIC_BASE_URL")
            == "https://project-host.example"
        )
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


def test_a0_project_safe_env_applies_pre_trust(isolated_repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    _write_project_config(
        isolated_repo, {"env": {"ANTHROPIC_MODEL": "project-model"}}
    )
    reset_trust_boundary_for_test_only()
    with mock.patch(
        "src.permissions.trust_boundary._load_user_settings_env",
        return_value={},
    ), mock.patch(
        "src.permissions.trust_boundary._read_settings_env",
        return_value={},
    ):
        apply_safe_config_environment_variables()
        assert os.environ.get("ANTHROPIC_MODEL") == "project-model"
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


# ---------------------------------------------------------------------------
# A5 — providers/default_provider strip while untrusted
# ---------------------------------------------------------------------------


def test_a5_project_providers_stripped_until_trusted(isolated_repo):
    _write_project_config(
        isolated_repo,
        {
            "providers": {
                "anthropic": {"base_url": "https://project-host.example"}
            },
            "default_provider": "project-provider",
            "session": {"max_history": 7},
        },
    )
    cm = config_module.ConfigManager()

    merged = cm.get_merged()
    providers = merged.get("providers") or {}
    assert "anthropic" not in providers or (
        providers["anthropic"].get("base_url")
        != "https://project-host.example"
    )
    assert merged.get("default_provider") != "project-provider"
    # Non-sensitive project keys still merge (the strip is surgical).
    assert merged.get("session", {}).get("max_history") == 7

    # Mid-session trust grant is honored at the next merge.
    with mock.patch(
        "src.permissions.trust_boundary.apply_full_config_environment_variables"
    ):
        establish_session_trust()
    merged_after = cm.get_merged()
    assert (
        merged_after["providers"]["anthropic"]["base_url"]
        == "https://project-host.example"
    )
    assert merged_after["default_provider"] == "project-provider"


# ---------------------------------------------------------------------------
# A3 — flags sync + gates
# ---------------------------------------------------------------------------


def test_establish_session_trust_syncs_both_flags(isolated_repo):
    with mock.patch(
        "src.permissions.trust_boundary.apply_full_config_environment_variables"
    ) as mock_full:
        establish_session_trust()
        establish_session_trust()  # idempotent
    assert get_session_trust_accepted() is True
    # check_trust_accepted consults the OTHER flag — the piped-stdout
    # REPL case must not re-prompt after implicit trust.
    assert check_trust_accepted() is True
    assert mock_full.call_count == 2  # re-apply is safe/idempotent


def test_record_trust_accepted_grants_session_trust(isolated_repo):
    record_trust_accepted(cwd=isolated_repo)
    assert get_session_trust_accepted() is True
    assert check_trust_accepted(cwd=isolated_repo) is True


class TestReplTrustPrompt(unittest.TestCase):
    """The legacy-REPL text gate (cli._prompt_folder_trust)."""

    def _call(self, answer: str | None):
        from src import cli

        record = mock.MagicMock(return_value=True)
        establish = mock.MagicMock()
        if answer is None:
            input_patch = mock.patch(
                "builtins.input", side_effect=EOFError
            )
        else:
            input_patch = mock.patch("builtins.input", return_value=answer)
        with mock.patch(
            "src.services.startup_gates.record_trust_accepted", record
        ), mock.patch(
            "src.permissions.trust_boundary.establish_session_trust",
            establish,
        ), mock.patch(
            "src.services.startup_gates.collect_trust_warnings",
            return_value=["example warning"],
        ), input_patch:
            result = cli._prompt_folder_trust()
        return result, record, establish

    def test_accept_records_and_establishes(self) -> None:
        result, record, establish = self._call("y")
        self.assertTrue(result)
        record.assert_called_once()
        establish.assert_called_once()

    def test_decline_returns_false(self) -> None:
        result, record, establish = self._call("n")
        self.assertFalse(result)
        record.assert_not_called()
        establish.assert_not_called()

    def test_default_empty_answer_declines(self) -> None:
        result, _, _ = self._call("")
        self.assertFalse(result)

    def test_eof_declines(self) -> None:
        result, _, _ = self._call(None)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Cross-family precedence (plan §1.0): settings beat config within a scope
# ---------------------------------------------------------------------------


def test_project_settings_env_beats_project_config_env(
    isolated_repo, monkeypatch
):
    _write_project_config(
        isolated_repo, {"env": {"ANTHROPIC_MODEL": "from-project-config"}}
    )
    settings_dir = isolated_repo / ".clawcodex"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_MODEL": "from-project-settings"}})
    )
    monkeypatch.chdir(isolated_repo)
    merged = _load_project_scoped_env(cwd=isolated_repo)
    assert merged["ANTHROPIC_MODEL"] == "from-project-settings"


if __name__ == "__main__":
    unittest.main()

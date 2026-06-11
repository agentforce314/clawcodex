"""C7 tests: .mcp.json approval status, persistence, enforcement, UI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.services.mcp_approval import (
    DISABLED_KEY,
    ENABLE_ALL_KEY,
    ENABLED_KEY,
    filter_unapproved_mcpjson_servers,
    get_mcpjson_server_status,
    list_pending_mcpjson_servers,
    record_mcpjson_choice,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Isolated cwd with a local .mcp.json declaring two servers."""

    import src.services.mcp.config as mcp_config_mod

    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "alpha": {"command": "alpha-server"},
                    "beta": {"command": "beta-server"},
                }
            }
        )
    )
    monkeypatch.setattr(mcp_config_mod, "_get_cwd", lambda: str(tmp_path))
    # Approval reads/writes default to the process cwd — pin it too so
    # the no-cwd production call paths resolve to this project.
    monkeypatch.chdir(tmp_path)
    # C8: non-interactive sessions auto-approve (TS utils.ts:399-403).
    # The bootstrap default is non-interactive, so pin interactive mode
    # to exercise the pending/approval flows these tests are about.
    from src.bootstrap import state as bootstrap_state

    monkeypatch.setattr(bootstrap_state._STATE, "is_interactive", True)
    return tmp_path


def _settings(tmp_path) -> dict:
    path = tmp_path / ".clawcodex" / "settings.local.json"
    return json.loads(path.read_text()) if path.exists() else {}


class TestStatusAndPersistence:
    def test_default_is_pending(self, project) -> None:
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "pending"

    def test_enable_round_trip(self, project) -> None:
        assert record_mcpjson_choice("alpha", "enable", cwd=str(project))
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"
        assert _settings(project)[ENABLED_KEY] == ["alpha"]

    def test_disable_beats_enable(self, project) -> None:
        record_mcpjson_choice("alpha", "enable", cwd=str(project))
        record_mcpjson_choice("alpha", "disable", cwd=str(project))
        # TS precedence: disabled list is checked FIRST.
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "rejected"
        data = _settings(project)
        assert "alpha" in data[ENABLED_KEY] and "alpha" in data[DISABLED_KEY]

    def test_enable_all(self, project) -> None:
        record_mcpjson_choice("beta", "enable_all", cwd=str(project))
        assert _settings(project)[ENABLE_ALL_KEY] is True
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"
        assert get_mcpjson_server_status("never-seen", cwd=str(project)) == "approved"

    def test_name_normalization(self, project) -> None:
        # normalize_name_for_mcp maps non-[a-zA-Z0-9_-] chars to "_",
        # so "alpha server" and "alpha_server" are the same identity.
        record_mcpjson_choice("alpha server", "enable", cwd=str(project))
        assert (
            get_mcpjson_server_status("alpha_server", cwd=str(project))
            == "approved"
        )

    def test_invalid_choice_rejected(self, project) -> None:
        assert not record_mcpjson_choice("alpha", "bogus", cwd=str(project))

    def test_non_interactive_session_auto_approves(
        self, project, monkeypatch
    ) -> None:
        """TS utils.ts:399-403: SDK / -p / piped sessions can't show a
        popup; the mode itself is the consent."""

        from src.bootstrap import state as bootstrap_state

        monkeypatch.setattr(bootstrap_state._STATE, "is_interactive", False)
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"
        # Explicit disable still wins.
        record_mcpjson_choice("alpha", "disable", cwd=str(project))
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "rejected"

    def test_bypass_acceptance_auto_approves(self, project) -> None:
        """TS utils.ts:377-390: a user who accepted the bypass dialog
        (skipDangerousModePermissionPrompt) consents to repo servers."""

        from src.services.startup_gates import record_bypass_accepted

        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "pending"
        assert record_bypass_accepted()
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"


class TestPendingListing:
    def test_lists_both_until_decided(self, project) -> None:
        pending = list_pending_mcpjson_servers(str(project))
        assert pending == ["alpha", "beta"]
        record_mcpjson_choice("alpha", "enable", cwd=str(project))
        assert list_pending_mcpjson_servers(str(project)) == ["beta"]
        record_mcpjson_choice("beta", "disable", cwd=str(project))
        assert list_pending_mcpjson_servers(str(project)) == []


class TestEnforcement:
    def _scoped(self, scope: str):
        return SimpleNamespace(scope=scope)

    def test_pending_mcpjson_scope_dropped_with_notice(self, project) -> None:
        servers = {
            "alpha": self._scoped("local"),
            "userserver": self._scoped("user"),
        }
        kept, notices = filter_unapproved_mcpjson_servers(
            servers, cwd=str(project)
        )
        assert "alpha" not in kept
        assert "userserver" in kept  # non-.mcp.json scopes untouched
        assert any("awaiting approval" in n for n in notices)

    def test_approved_kept_rejected_silently_dropped(self, project) -> None:
        record_mcpjson_choice("alpha", "enable", cwd=str(project))
        record_mcpjson_choice("beta", "disable", cwd=str(project))
        servers = {
            "alpha": self._scoped("local"),
            "beta": self._scoped("project"),
        }
        kept, notices = filter_unapproved_mcpjson_servers(
            servers, cwd=str(project)
        )
        assert list(kept) == ["alpha"]
        # An explicit rejection is a decision, not a health problem —
        # no perpetual warning.
        assert notices == []

    def test_get_all_mcp_configs_excludes_pending(self, project) -> None:
        """End-to-end through the REAL aggregator: a pending .mcp.json
        server never reaches the merged set (headless enforcement)."""

        from src.services.mcp.config import get_all_mcp_configs

        configs, errors = get_all_mcp_configs()
        assert "alpha" not in configs and "beta" not in configs
        assert any(
            "awaiting approval" in (e.message or "") for e in errors
        )
        record_mcpjson_choice("alpha", "enable", cwd=str(project))
        configs2, _errors2 = get_all_mcp_configs()
        assert "alpha" in configs2
        assert "beta" not in configs2

    def test_repo_name_collision_cannot_shadow_user_server(
        self, project, tmp_path, monkeypatch
    ) -> None:
        """Regression (critic finding 1): an unapproved/rejected repo
        .mcp.json server name-colliding with a user-scope server must
        not knock the user's own server out of the merged set."""

        from src.services.mcp.config import get_all_mcp_configs

        user_dir = tmp_path / "user-config"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(
            json.dumps(
                {"mcpServers": {"alpha": {"command": "user-alpha"}}}
            )
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))

        configs, _errors = get_all_mcp_configs()
        assert "alpha" in configs  # user's own server survives
        assert configs["alpha"].scope == "user"

        record_mcpjson_choice("alpha", "disable", cwd=str(project))
        configs2, _errors2 = get_all_mcp_configs()
        assert "alpha" in configs2  # explicit rejection of the repo's
        assert configs2["alpha"].scope == "user"  # copy keeps the user's

    def test_get_mcp_config_by_name_gates_mcpjson_scopes(
        self, project
    ) -> None:
        """The per-name resolve path (reconnect etc.) honors the gate."""

        from src.services.mcp.config import get_mcp_config_by_name

        assert get_mcp_config_by_name("alpha") is None  # pending
        record_mcpjson_choice("beta", "disable", cwd=str(project))
        assert get_mcp_config_by_name("beta") is None  # rejected
        record_mcpjson_choice("alpha", "enable", cwd=str(project))
        resolved = get_mcp_config_by_name("alpha")
        assert resolved is not None and resolved.scope == "local"

    def test_user_tier_enable_all_is_honored(self, project) -> None:
        """TS reads these keys from merged settings (user→local). A
        user-level enableAllProjectMcpServers opt-in approves repo
        servers; the project settings tier is deliberately excluded."""

        from src.permissions import settings_paths

        with open(settings_paths.user_settings_path(), "w") as f:
            json.dump({ENABLE_ALL_KEY: True}, f)
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"
        # Local disable still beats the user-tier blanket enable.
        record_mcpjson_choice("alpha", "disable", cwd=str(project))
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "rejected"

    def test_non_list_settings_values_ignored_not_mangled(
        self, project
    ) -> None:
        path = project / ".clawcodex" / "settings.local.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({ENABLED_KEY: "alpha"}))
        # A string value must not be iterated as characters.
        assert get_mcpjson_server_status("a", cwd=str(project)) == "pending"
        record_mcpjson_choice("beta", "enable", cwd=str(project))
        assert json.loads(path.read_text())[ENABLED_KEY] == ["beta"]


@pytest.mark.asyncio
async def test_approval_screen_choices() -> None:
    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.tui.screens.mcp_approval import McpApprovalScreen

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    for presses, expected in (
        (("enter",), "enable"),
        (("down", "enter"), "enable_all"),
        (("down", "down", "enter"), "disable"),
        (("escape",), None),
    ):
        app = _App()
        async with app.run_test() as pilot:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            app.push_screen(
                McpApprovalScreen("alpha"),
                callback=lambda r: future.set_result(r),
            )
            await pilot.pause()
            for key in presses:
                await pilot.press(key)
            result = await asyncio.wait_for(future, timeout=5)
        assert result == expected, (presses, result)


class TestAppApprovalChain:
    def _fake(self, tmp_path):
        from src.tui.app import ClawCodexTUI

        rows: list[str] = []
        pushes: list[tuple] = []
        fake = SimpleNamespace(
            workspace_root=tmp_path,
            _repl_screen=SimpleNamespace(
                transcript=SimpleNamespace(
                    append_system=lambda text, style="muted": rows.append(text)
                )
            ),
            push_screen=lambda screen, callback=None: pushes.append(
                (screen, callback)
            ),
        )
        # Wire the real chain methods onto the fake so _on_mcp_approval's
        # tail call exercises production control flow.
        fake._prompt_next_mcp_approval = (
            lambda queue: ClawCodexTUI._prompt_next_mcp_approval(fake, queue)
        )
        fake._on_mcp_approval = (
            lambda name, choice, queue: ClawCodexTUI._on_mcp_approval(
                fake, name, choice, queue
            )
        )
        return fake, rows, pushes

    def test_enable_persists_and_chains_to_next(self, project) -> None:
        fake, rows, pushes = self._fake(project)
        fake._on_mcp_approval("alpha", "enable", ["beta"])
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "approved"
        assert len(pushes) == 1  # beta prompted next
        assert any("'alpha' enabled" in r for r in rows)

    def test_enable_all_clears_queue(self, project) -> None:
        fake, rows, pushes = self._fake(project)
        queue = ["beta", "gamma"]
        fake._on_mcp_approval("alpha", "enable_all", queue)
        assert queue == []
        assert len(pushes) == 0  # nothing further prompted
        assert _settings(project)[ENABLE_ALL_KEY] is True
        assert any("All .mcp.json servers" in r for r in rows)

    def test_pending_row_on_escape(self, project) -> None:
        fake, rows, pushes = self._fake(project)
        fake._on_mcp_approval("alpha", None, [])
        assert get_mcpjson_server_status("alpha", cwd=str(project)) == "pending"
        assert any("left pending" in r for r in rows)

    def test_already_decided_queue_entry_skipped(self, project) -> None:
        fake, rows, pushes = self._fake(project)
        record_mcpjson_choice("beta", "enable", cwd=str(project))
        fake._prompt_next_mcp_approval(["beta", "alpha"])
        # beta was decided out-of-band — only alpha gets a dialog.
        assert len(pushes) == 1
        assert pushes[0][0]._server_name == "alpha"

    def test_write_failure_reports_honestly(self, project, monkeypatch) -> None:
        import src.services.mcp_approval as approval_mod

        monkeypatch.setattr(
            approval_mod, "_write_local_settings", lambda *a, **k: False
        )
        fake, rows, pushes = self._fake(project)
        fake._on_mcp_approval("alpha", "enable_all", ["beta"])
        assert not any("enabled" in r for r in rows)
        assert any("Could not save" in r for r in rows)
        # The failed enable_all must NOT clear the queue — beta still asked.
        assert len(pushes) == 1

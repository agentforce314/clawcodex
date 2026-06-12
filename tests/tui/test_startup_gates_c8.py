"""C8 tests: trust / external-includes / bypass startup gates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.startup_gates import (
    INCLUDES_APPROVED_KEY,
    INCLUDES_WARNING_SHOWN_KEY,
    SKIP_DANGEROUS_PROMPT_KEY,
    TRUST_KEY,
    check_trust_accepted,
    collect_trust_warnings,
    get_external_includes_state,
    has_skip_dangerous_mode_permission_prompt,
    list_external_includes,
    record_bypass_accepted,
    record_external_includes_choice,
    record_trust_accepted,
    reset_session_trust_for_testing,
)


@pytest.fixture(autouse=True)
def _fresh_session_trust():
    reset_session_trust_for_testing()
    yield
    reset_session_trust_for_testing()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


def _global_config(tmp_path) -> dict:
    import src.config as config_mod

    path = Path(config_mod.GLOBAL_CONFIG_DIR) / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


class TestTrustGate:
    def test_untrusted_by_default_then_round_trip(self, proj, tmp_path) -> None:
        assert not check_trust_accepted(proj)
        assert record_trust_accepted(proj)
        assert check_trust_accepted(proj)
        entry = _global_config(tmp_path)["projects"][str(proj.resolve())]
        assert entry[TRUST_KEY] is True

    def test_parent_trust_covers_children(self, proj, tmp_path) -> None:
        import src.config as config_mod

        child = proj / "nested" / "deep"
        child.mkdir(parents=True)
        config_mod.update_project_entry(proj, {TRUST_KEY: True})
        reset_session_trust_for_testing()
        assert check_trust_accepted(child)

    def test_home_directory_trust_is_session_only(
        self, tmp_path, monkeypatch
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.chdir(fake_home)
        assert record_trust_accepted(fake_home)
        assert check_trust_accepted(fake_home)  # session flag
        assert "projects" not in _global_config(tmp_path)  # nothing persisted
        reset_session_trust_for_testing()
        assert not check_trust_accepted(fake_home)  # asked again next launch

    def test_trust_warnings_enumerate_modeled_subsystems(self, proj) -> None:
        assert collect_trust_warnings(proj) == []
        settings_dir = proj / ".clawcodex"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(ls:*)"]},
                    "statusLine": {"type": "command", "command": "echo hi"},
                }
            )
        )
        warnings = collect_trust_warnings(proj)
        assert any("pre-allows Bash" in w for w in warnings)
        assert any("status-line command" in w for w in warnings)


class TestExternalIncludesGate:
    def test_state_round_trips(self, proj, tmp_path) -> None:
        assert get_external_includes_state(proj) == "unset"
        assert record_external_includes_choice(False, proj)
        assert get_external_includes_state(proj) == "declined"
        assert record_external_includes_choice(True, proj)
        assert get_external_includes_state(proj) == "approved"
        entry = _global_config(tmp_path)["projects"][str(proj.resolve())]
        assert entry[INCLUDES_APPROVED_KEY] is True
        assert entry[INCLUDES_WARNING_SHOWN_KEY] is True

    @pytest.mark.asyncio
    async def test_loader_gates_external_includes(
        self, proj, tmp_path, monkeypatch
    ) -> None:
        """End-to-end through the REAL loader: external @includes load
        only after this project approved them."""

        from src.context_system.claude_md import (
            clear_memory_file_caches,
            get_memory_files,
        )

        outside = tmp_path / "elsewhere"
        outside.mkdir()
        ext = outside / "ext-notes.md"
        ext.write_text("external payload")
        (proj / "CLAUDE.md").write_text(f"Main rules.\n@{ext}")
        monkeypatch.setenv("CLAUDE_CODE_ORIGINAL_CWD", str(proj))
        clear_memory_file_caches()
        try:
            files = await get_memory_files(cwd=str(proj))
            assert not any(f.path == str(ext) for f in files)

            externals = await list_external_includes(proj)
            assert str(ext) in externals

            assert record_external_includes_choice(True, proj)
            files_after = await get_memory_files(cwd=str(proj))
            assert any(f.path == str(ext) for f in files_after)
        finally:
            clear_memory_file_caches()

    def test_is_external_predicate(self, proj, tmp_path, monkeypatch) -> None:
        from src.context_system.claude_md import is_external_memory_file
        from src.context_system.models import MemoryFileInfo

        monkeypatch.setenv("CLAUDE_CODE_ORIGINAL_CWD", str(proj))
        outside = str(tmp_path / "elsewhere" / "x.md")
        inside = str(proj / "x.md")
        make = lambda path, parent, mem_type="Project": MemoryFileInfo(
            path=path, type=mem_type, content="c", parent=parent
        )
        assert is_external_memory_file(make(outside, str(proj / "CLAUDE.md")))
        assert not is_external_memory_file(make(inside, str(proj / "CLAUDE.md")))
        # A ROOT file outside cwd (e.g. the user's ~/CLAUDE.md) is not
        # an "external include" — only included files count.
        assert not is_external_memory_file(make(outside, None))
        # TS claudemd.ts:1432 excludes the User tier entirely: the
        # user's own ~/CLAUDE.md may @include anything without
        # triggering this project's gate.
        assert not is_external_memory_file(
            make(outside, "~/CLAUDE.md", mem_type="User")
        )


class TestBypassGate:
    def test_accept_round_trip_user_settings(self, proj) -> None:
        from src.permissions import settings_paths

        assert not has_skip_dangerous_mode_permission_prompt(proj)
        assert record_bypass_accepted()
        assert has_skip_dangerous_mode_permission_prompt(proj)
        data = json.loads(Path(settings_paths.user_settings_path()).read_text())
        assert data[SKIP_DANGEROUS_PROMPT_KEY] is True

    def test_local_tier_flag_honored(self, proj) -> None:
        settings_dir = proj / ".clawcodex"
        settings_dir.mkdir()
        (settings_dir / "settings.local.json").write_text(
            json.dumps({SKIP_DANGEROUS_PROMPT_KEY: True})
        )
        assert has_skip_dangerous_mode_permission_prompt(proj)


@pytest.mark.asyncio
async def test_gate_screens_outcomes() -> None:
    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.tui.screens.startup_gates import (
        BypassPermissionsScreen,
        ExternalIncludesScreen,
        TrustFolderScreen,
    )

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    cases = [
        (lambda: TrustFolderScreen("/tmp/x", ["warn"]), ("enter",), "trust"),
        (lambda: TrustFolderScreen("/tmp/x"), ("down", "enter"), "exit"),
        (lambda: TrustFolderScreen("/tmp/x"), ("escape",), "exit"),
        (lambda: ExternalIncludesScreen(["/e/a.md"]), ("enter",), "yes"),
        (lambda: ExternalIncludesScreen([]), ("down", "enter"), "no"),
        (lambda: ExternalIncludesScreen(["/e/a.md"]), ("escape",), "no"),
        (lambda: BypassPermissionsScreen(), ("enter",), "decline"),
        (lambda: BypassPermissionsScreen(), ("down", "enter"), "accept"),
        (lambda: BypassPermissionsScreen(), ("escape",), "escape"),
    ]
    for factory, presses, expected in cases:
        app = _App()
        async with app.run_test() as pilot:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            app.push_screen(factory(), callback=lambda r: future.set_result(r))
            await pilot.pause()
            for key in presses:
                await pilot.press(key)
            result = await asyncio.wait_for(future, timeout=5)
        assert result == expected, (presses, result)


def _capture_real_chain_methods():
    """Bind the chain methods at import time — tests/conftest.py patches
    ``_run_startup_chain`` on the class to skip the gates in full-app
    tests, and these tests must exercise the REAL chain."""

    from src.tui.app import ClawCodexTUI

    return {
        name: getattr(ClawCodexTUI, name)
        for name in (
            "_run_startup_chain",
            "_startup_gate_trust",
            "_on_trust_choice",
            "_startup_gate_includes",
            "_detect_external_includes",
            "_on_includes_choice",
            "_startup_gate_bypass",
            "_on_bypass_choice",
            "_finish_startup_gates",
        )
    }


_REAL_CHAIN = _capture_real_chain_methods()


class TestAppGateChain:
    def _fake(self, proj):
        rows: list[str] = []
        pushes: list[tuple] = []
        exits: list[int] = []
        finished: list[str] = []
        fake = SimpleNamespace(
            workspace_root=proj,
            tool_context=SimpleNamespace(
                permission_context=SimpleNamespace(mode="default")
            ),
            _repl_screen=SimpleNamespace(
                transcript=SimpleNamespace(
                    append_system=lambda text, style="muted": rows.append(text)
                )
            ),
            push_screen=lambda screen, callback=None: pushes.append(
                (screen, callback)
            ),
            exit=lambda return_code=0: exits.append(return_code),
            _show_config_warnings=lambda: finished.append("warnings"),
            _run_mcp_approvals=lambda: finished.append("mcp"),
        )
        for name, method in _REAL_CHAIN.items():
            setattr(
                fake, name, (lambda m: lambda *a, **k: m(fake, *a, **k))(method)
            )
        # The includes detection step spawns an async worker; for the
        # sync chain tests, pre-decide includes so it short-circuits.
        return fake, rows, pushes, exits, finished

    def test_all_satisfied_chain_reaches_c6_and_c7(self, proj) -> None:
        record_trust_accepted(proj)
        record_external_includes_choice(False, proj)
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._run_startup_chain()
        assert finished == ["warnings", "mcp"]
        assert pushes == [] and exits == []

    def test_untrusted_prompts_and_decline_exits_1(self, proj) -> None:
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._startup_gate_trust()
        assert len(pushes) == 1  # trust dialog shown
        fake._on_trust_choice("exit")
        assert exits == [1]
        assert finished == []

    def test_trust_accept_persists_and_continues(self, proj) -> None:
        record_external_includes_choice(False, proj)
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._on_trust_choice("trust")
        assert check_trust_accepted(proj)
        # #275: acceptance propagates to the already-built tool context so
        # hooks stop being trust-skipped mid-session.
        assert fake.tool_context.workspace_trusted is True
        assert finished == ["warnings", "mcp"]

    def test_bypass_gate_prompts_only_in_bypass_mode(self, proj) -> None:
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake.tool_context.permission_context.mode = "bypassPermissions"
        fake._startup_gate_bypass()
        assert len(pushes) == 1
        # Decline exits 1; Esc exits 0 (TS gracefulShutdownSync codes).
        fake._on_bypass_choice("decline")
        fake._on_bypass_choice("escape")
        assert exits == [1, 0]

    def test_bypass_accept_persists_and_continues(self, proj) -> None:
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._on_bypass_choice("accept")
        assert has_skip_dangerous_mode_permission_prompt(proj)
        assert finished == ["warnings", "mcp"]
        assert exits == []

    def test_bypass_already_accepted_skips_prompt(self, proj) -> None:
        record_bypass_accepted()
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake.tool_context.permission_context.mode = "bypassPermissions"
        fake._startup_gate_bypass()
        assert pushes == []
        assert finished == ["warnings", "mcp"]

    def test_includes_choice_rows_and_persistence(self, proj) -> None:
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._on_includes_choice("yes")
        assert get_external_includes_state(proj) == "approved"
        assert any("imports enabled" in r for r in rows)
        fake._on_includes_choice("no")
        assert get_external_includes_state(proj) == "declined"
        assert any("imports disabled" in r for r in rows)

    def test_write_failure_reports_honestly(self, proj, monkeypatch) -> None:
        import src.services.startup_gates as gates_mod

        monkeypatch.setattr(
            gates_mod, "record_bypass_accepted", lambda: False
        )
        fake, rows, pushes, exits, finished = self._fake(proj)
        fake._on_bypass_choice("accept")
        assert any("Could not persist" in r for r in rows)
        assert finished == ["warnings", "mcp"]  # accepted interactively

    def test_unset_includes_schedules_detection_worker(
        self, proj, monkeypatch
    ) -> None:
        import src.services.startup_gates as gates_mod

        monkeypatch.setattr(
            gates_mod, "get_external_includes_state", lambda cwd=None: "unset"
        )
        fake, rows, pushes, exits, finished = self._fake(proj)
        workers: list = []
        fake.run_worker = lambda coro, **kw: workers.append(coro)
        fake._startup_gate_includes()
        assert len(workers) == 1
        workers[0].close()  # avoid un-awaited coroutine warning
        assert finished == []  # chain paused until the worker decides

    def test_detect_worker_pushes_dialog_when_externals(
        self, proj, monkeypatch
    ) -> None:
        import asyncio

        import src.services.startup_gates as gates_mod

        async def fake_list(cwd=None):
            return ["/elsewhere/a.md"]

        monkeypatch.setattr(gates_mod, "list_external_includes", fake_list)
        fake, rows, pushes, exits, finished = self._fake(proj)
        asyncio.run(fake._detect_external_includes())
        assert len(pushes) == 1  # includes dialog shown
        assert finished == []

    def test_detect_worker_continues_when_no_externals(
        self, proj, monkeypatch
    ) -> None:
        import asyncio

        import src.services.startup_gates as gates_mod

        async def fake_list(cwd=None):
            return []

        monkeypatch.setattr(gates_mod, "list_external_includes", fake_list)
        fake, rows, pushes, exits, finished = self._fake(proj)
        asyncio.run(fake._detect_external_includes())
        assert pushes == []
        assert finished == ["warnings", "mcp"]  # chain continued

"""C6 tests: config health checks, /doctor wiring, startup warnings."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.config_health import collect_config_warnings


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Point every checked config path into tmp.

    ``_find_git_root`` is pinned to tmp so the loader-faithful
    git-root-anchored project paths (review M1) resolve inside the
    fixture rather than walking up into the real repo.
    """

    import src.config as config_mod
    from src.permissions import settings_paths

    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_DIR", tmp_path / "global")
    monkeypatch.setattr(
        config_mod, "_find_git_root", lambda cwd=None: tmp_path
    )
    monkeypatch.setattr(
        settings_paths,
        "user_settings_path",
        lambda: str(tmp_path / "user-settings.json"),
    )
    return tmp_path


class TestConfigHealth:
    def test_clean_tree_no_warnings(self, isolated_paths) -> None:
        assert collect_config_warnings(str(isolated_paths)) == []

    def test_malformed_json_warns(self, isolated_paths) -> None:
        cfg_dir = isolated_paths / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("{not json")
        warnings = collect_config_warnings(str(isolated_paths))
        assert len(warnings) == 1
        assert "invalid JSON" in warnings[0].problem
        assert "config.json" in warnings[0].path
        assert "file ignored" in warnings[0].message()

    def test_non_object_top_level_warns(self, isolated_paths) -> None:
        clawdir = isolated_paths / ".clawcodex"
        clawdir.mkdir()
        (clawdir / "settings.json").write_text("[1, 2, 3]")
        warnings = collect_config_warnings(str(isolated_paths))
        assert len(warnings) == 1
        assert "JSON object" in warnings[0].problem

    def test_valid_files_pass(self, isolated_paths) -> None:
        clawdir = isolated_paths / ".clawcodex"
        clawdir.mkdir()
        (clawdir / "settings.local.json").write_text(
            '{"permissions": {"allow": []}}'
        )
        assert collect_config_warnings(str(isolated_paths)) == []

    def test_bad_encoding_warns(self, isolated_paths) -> None:
        cfg_dir = isolated_paths / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "config.local.json").write_bytes(b'{"a": "\xff\xfe"}')
        warnings = collect_config_warnings(str(isolated_paths))
        assert len(warnings) == 1
        assert "encoding" in warnings[0].problem

    def test_unreadable_file_warns(self, isolated_paths) -> None:
        import os
        import stat

        if os.geteuid() == 0:  # pragma: no cover — root ignores modes
            pytest.skip("permission bits ignored as root")
        clawdir = isolated_paths / ".clawcodex"
        clawdir.mkdir()
        target = clawdir / "settings.json"
        target.write_text("{}")
        target.chmod(0)
        try:
            warnings = collect_config_warnings(str(isolated_paths))
            assert len(warnings) == 1
            assert "unreadable" in warnings[0].problem
        finally:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_directory_named_config_is_skipped(self, isolated_paths) -> None:
        cfg_dir = isolated_paths / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").mkdir()  # directory, not a file
        assert collect_config_warnings(str(isolated_paths)) == []


class TestLoaderHardening:
    """Review M2: every detected problem class must actually be IGNORED
    by the loaders (not crash startup) so 'file ignored' is true."""

    def test_settings_loader_survives_array_and_encoding(self, tmp_path) -> None:
        from src.permissions.setup import _load_settings_file

        arr = tmp_path / "arr.json"
        arr.write_text("[1, 2]")
        assert _load_settings_file(str(arr)) is None
        bad = tmp_path / "bad.json"
        bad.write_bytes(b'{"a": "\xff\xfe"}')
        assert _load_settings_file(str(bad)) is None

    def test_setup_permissions_survives_malformed_settings(self, tmp_path) -> None:
        from src.permissions.setup import setup_permissions

        local = tmp_path / "settings.local.json"
        local.write_text("[not an object]")
        result = setup_permissions(
            cwd=str(tmp_path), local_settings_path=str(local)
        )
        assert result.context is not None

    def test_config_reader_ignores_non_object(self, tmp_path) -> None:
        from src.config import _read_json

        path = tmp_path / "cfg.json"
        path.write_text("[1, 2, 3]")
        assert _read_json(path) == {}


class TestRuleWarnings:
    def test_dangerous_rule_surfaces(self, isolated_paths) -> None:
        import json

        from src.services.config_health import collect_rule_warnings

        clawdir = isolated_paths / ".clawcodex"
        clawdir.mkdir()
        (clawdir / "settings.local.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash"]}})
        )
        warnings = collect_rule_warnings(str(isolated_paths))
        assert any("dangerous permission rule Bash" in w for w in warnings)

    def test_clean_rules_no_warnings(self, isolated_paths) -> None:
        from src.services.config_health import collect_rule_warnings

        assert collect_rule_warnings(str(isolated_paths)) == []


class TestDoctorCommand:
    @pytest.mark.asyncio
    async def test_headless_report_with_real_context(self, isolated_paths) -> None:
        # REAL CommandContext (review m9): pins the `cwd` field name a
        # MagicMock would silently mask.
        from src.command_system.doctor_command import DOCTOR_COMMAND
        from src.command_system.types import CommandContext

        ctx = CommandContext(
            workspace_root=isolated_paths,
            cwd=isolated_paths,
            conversation=MagicMock(),
            cost_tracker=MagicMock(),
            history=MagicMock(),
        )
        outcome = await DOCTOR_COMMAND.run("", ctx)
        assert "Diagnostics:" in outcome.message
        assert "python" in outcome.message
        assert "config files: OK" in outcome.message

    @pytest.mark.asyncio
    async def test_report_lists_problems(self, isolated_paths) -> None:
        from src.command_system.doctor_command import DOCTOR_COMMAND

        (isolated_paths / ".claude").mkdir()
        (isolated_paths / ".claude" / "config.json").write_text("{oops")
        ctx = MagicMock()
        ctx.cwd = str(isolated_paths)
        outcome = await DOCTOR_COMMAND.run("", ctx)
        assert "Config problems:" in outcome.message
        assert "invalid JSON" in outcome.message

    def test_headless_import_is_textual_free(self) -> None:
        import subprocess
        import sys

        code = (
            "import sys; import asyncio; "
            "from unittest.mock import MagicMock; "
            "from src.command_system.doctor_command import DOCTOR_COMMAND; "
            "asyncio.run(DOCTOR_COMMAND.run('', MagicMock())); "
            "sys.exit(1 if any(m.startswith('textual') for m in sys.modules) else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr


class TestDispatchAndStartupRows:
    def test_doctor_dispatch(self) -> None:
        from src.tui.commands import dispatch_local_command

        result = dispatch_local_command(
            "/doctor", session=None, workspace_root=Path("."), tool_registry=None
        )
        assert result.handled and result.open_dialog == "doctor"

    def test_startup_rows_emitted(self, isolated_paths) -> None:
        from src.tui.app import ClawCodexTUI

        (isolated_paths / ".claude").mkdir()
        (isolated_paths / ".claude" / "config.json").write_text("{oops")
        rows: list[tuple[str, str]] = []
        fake = SimpleNamespace(
            workspace_root=isolated_paths,
            _repl_screen=SimpleNamespace(
                transcript=SimpleNamespace(
                    append_system=lambda text, style="muted": rows.append(
                        (style, text)
                    )
                )
            ),
        )
        ClawCodexTUI._show_config_warnings(fake)
        assert any("invalid JSON" in text for _s, text in rows)
        assert any("/doctor" in text for _s, text in rows)

    def test_startup_rows_silent_when_clean(self, isolated_paths) -> None:
        from src.tui.app import ClawCodexTUI

        rows: list[str] = []
        fake = SimpleNamespace(
            workspace_root=isolated_paths,
            _repl_screen=SimpleNamespace(
                transcript=SimpleNamespace(
                    append_system=lambda text, style="muted": rows.append(text)
                )
            ),
        )
        ClawCodexTUI._show_config_warnings(fake)
        assert rows == []

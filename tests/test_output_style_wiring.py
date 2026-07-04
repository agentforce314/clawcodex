"""OS-1 — output-style feature wiring end-to-end.

Plan: my-docs/get-parity-by-folder/outputStyles-refactoring-plan.md.
G1 startup producer (settings → tool_context.output_style_name),
G3 persistence (set_output_style → local settings tier),
W3 availability (get_settings + validation against the loader's truth —
the old fixed VALID_OUTPUT_STYLES list rejected the real builtin
"explanatory" and accepted three nonexistent styles),
G4 loader canon dir (GLOBAL_CONFIG_DIR primary, ~/.claude legacy fallback).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# G1 — startup producer
# ---------------------------------------------------------------------------


class TestOutputStyleFromSettings:
    def test_reads_configured_style(self, tmp_path, monkeypatch):
        import src.settings.settings as settings_mod
        from src.outputStyles import output_style_from_settings

        monkeypatch.setattr(
            settings_mod, "load_settings",
            lambda **kw: type("S", (), {"output_style": type("O", (), {"style": "explanatory"})()})(),
        )
        assert output_style_from_settings() == "explanatory"

    def test_default_returns_none(self, monkeypatch):
        import src.settings.settings as settings_mod
        from src.outputStyles import output_style_from_settings

        monkeypatch.setattr(
            settings_mod, "load_settings",
            lambda **kw: type("S", (), {"output_style": type("O", (), {"style": "default"})()})(),
        )
        assert output_style_from_settings() is None

    def test_broken_settings_never_raise(self, monkeypatch):
        import src.settings.settings as settings_mod
        from src.outputStyles import output_style_from_settings

        def _boom(**kw):
            raise RuntimeError("corrupt settings")

        monkeypatch.setattr(settings_mod, "load_settings", _boom)
        assert output_style_from_settings() is None


# ---------------------------------------------------------------------------
# G3 — persistence (the localSettings analog)
# ---------------------------------------------------------------------------


class TestUpdateLocalSettings:
    def test_roundtrip_in_git_root(self, tmp_path, monkeypatch):
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        from src.settings.settings import load_settings, update_local_settings
        from src.config import get_local_config_path

        assert update_local_settings(
            {"output_style": {"style": "explanatory"}}, cwd=tmp_path,
        )
        path = get_local_config_path(tmp_path)
        assert path is not None and path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["settings"]["output_style"]["style"] == "explanatory"
        assert load_settings(cwd=tmp_path).output_style.style == "explanatory"

    def test_merge_preserves_other_settings(self, tmp_path):
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        from src.settings.settings import update_local_settings
        from src.config import get_local_config_path

        update_local_settings({"output_style": {"style": "explanatory"}}, cwd=tmp_path)
        update_local_settings({"output_style": {"max_width": 100}}, cwd=tmp_path)
        data = json.loads(get_local_config_path(tmp_path).read_text(encoding="utf-8"))
        # deep-merged, not clobbered
        assert data["settings"]["output_style"]["style"] == "explanatory"
        assert data["settings"]["output_style"]["max_width"] == 100

    def test_global_fallback_outside_git(self, tmp_path, monkeypatch):
        import src.config as config
        from src.settings.settings import update_local_settings

        monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", tmp_path / "home" / "config.json")
        assert update_local_settings(
            {"output_style": {"style": "explanatory"}}, cwd=tmp_path / "no-git",
        )
        data = json.loads((tmp_path / "home" / "config.json").read_text(encoding="utf-8"))
        assert data["settings"]["output_style"]["style"] == "explanatory"


# ---------------------------------------------------------------------------
# W3 — availability + validation truth
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_builtins_present(self):
        from src.outputStyles import available_output_styles

        names = available_output_styles()
        assert "default" in names
        assert "explanatory" in names  # rejected by the OLD fixed list

    def test_user_style_listed_and_resolvable(self, tmp_path):
        from src.outputStyles import available_output_styles, resolve_output_style

        (tmp_path / "pirate.md").write_text(
            "---\nname: pirate\ndescription: yar\n---\nSpeak like a pirate.",
            encoding="utf-8",
        )
        names = available_output_styles(tmp_path)
        assert "pirate" in names
        assert resolve_output_style("pirate", tmp_path).prompt.startswith("Speak like")

    def test_listing_matches_resolution(self, tmp_path):
        """The set_output_style validation invariant: every listed name
        resolves to itself (no invented names, no rejected builtins)."""
        from src.outputStyles import available_output_styles, resolve_output_style

        for name in available_output_styles(tmp_path):
            assert resolve_output_style(name, tmp_path).name == name


# ---------------------------------------------------------------------------
# G4 — canon dir + legacy fallback
# ---------------------------------------------------------------------------


class TestUserDirCanon:
    def test_canon_dir_primary(self, tmp_path, monkeypatch):
        import src.config as config
        from src.outputStyles import available_output_styles

        canon = tmp_path / "clawhome"
        (canon / "outputStyles").mkdir(parents=True)
        (canon / "outputStyles" / "canonstyle.md").write_text(
            "---\nname: canonstyle\n---\nCanon.", encoding="utf-8",
        )
        monkeypatch.setattr(config, "GLOBAL_CONFIG_DIR", canon)
        monkeypatch.setenv("HOME", str(tmp_path / "emptyhome"))
        assert "canonstyle" in available_output_styles()

    def test_legacy_dir_fallback_and_canon_wins(self, tmp_path, monkeypatch):
        import src.config as config
        from src.outputStyles import available_output_styles, resolve_output_style

        canon = tmp_path / "clawhome"
        legacy_home = tmp_path / "home"
        (canon / "outputStyles").mkdir(parents=True)
        (legacy_home / ".claude" / "outputStyles").mkdir(parents=True)
        (legacy_home / ".claude" / "outputStyles" / "legacystyle.md").write_text(
            "---\nname: legacystyle\n---\nLegacy.", encoding="utf-8",
        )
        (legacy_home / ".claude" / "outputStyles" / "shared.md").write_text(
            "---\nname: shared\n---\nFrom legacy.", encoding="utf-8",
        )
        (canon / "outputStyles" / "shared.md").write_text(
            "---\nname: shared\n---\nFrom canon.", encoding="utf-8",
        )
        monkeypatch.setattr(config, "GLOBAL_CONFIG_DIR", canon)
        monkeypatch.setenv("HOME", str(legacy_home))
        names = available_output_styles()
        assert "legacystyle" in names  # legacy dir still readable
        assert resolve_output_style("shared").prompt == "From canon."  # canon wins


# ---------------------------------------------------------------------------
# Server handler (G1 startup + G3 persist + W3 reply) — direct handler tests
# ---------------------------------------------------------------------------


class TestSetOutputStyleHandler:
    def _server(self, tmp_path):
        from src.server.agent_server import _AgentSession
        from src.tool_system.context import ToolContext

        srv = _AgentSession.__new__(_AgentSession)
        import threading

        srv._lock = threading.Lock()
        srv._current_abort = None
        srv.cwd = str(tmp_path)
        srv.tool_context = ToolContext(workspace_root=tmp_path)
        srv.provider = None
        srv.replies = []
        srv._reply = lambda rid, payload: srv.replies.append((rid, payload))
        return srv

    def test_explanatory_accepted_and_persisted(self, tmp_path, monkeypatch):
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        srv = self._server(tmp_path)
        # prompt rebuild path needs provider bits; stub it to no-op
        monkeypatch.setattr(
            "src.query.agent_loop_compat.build_effective_system_prompt",
            lambda *a, **k: "PROMPT",
        )
        srv._compose_with_plan = lambda p: p
        srv._do_set_output_style("r1", "explanatory")
        rid, payload = srv.replies[-1]
        assert payload["ok"] is True, payload
        assert payload["style"] == "explanatory"
        assert "explanatory" in payload["available_styles"]
        assert srv.tool_context.output_style_name == "explanatory"
        from src.config import get_local_config_path

        data = json.loads(get_local_config_path(tmp_path).read_text(encoding="utf-8"))
        assert data["settings"]["output_style"]["style"] == "explanatory"

    def test_unknown_style_rejected_with_availability(self, tmp_path):
        srv = self._server(tmp_path)
        srv._do_set_output_style("r1", "concise")  # in the OLD invented list
        rid, payload = srv.replies[-1]
        assert payload["ok"] is False
        assert "available_styles" in payload
        assert "explanatory" in payload["available_styles"]
        assert "concise" not in payload["available_styles"]

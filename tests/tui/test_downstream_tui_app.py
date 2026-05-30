from __future__ import annotations

from unittest.mock import Mock


from src.entrypoints.tui import TUIOptions


def test_downstream_tui_app_subclasses_upstream_app():
    from clawcodex_ext.tui.app import ClawCodexExtTUI
    from src.tui.app import ClawCodexTUI

    assert issubclass(ClawCodexExtTUI, ClawCodexTUI)


def test_downstream_tui_entrypoint_uses_downstream_app(monkeypatch):
    from clawcodex_ext.tui import entrypoint
    from clawcodex_ext.tui.app import ClawCodexExtTUI

    runner = Mock(return_value=7)
    monkeypatch.setattr(entrypoint, "_run_tui_with_app", runner)

    options = TUIOptions()

    assert entrypoint.run_tui(options) == 7
    runner.assert_called_once_with(options, app_cls=ClawCodexExtTUI)


def test_upstream_tui_entrypoint_uses_upstream_app(monkeypatch):
    import src.entrypoints.tui as tui_entrypoint
    from src.tui.app import ClawCodexTUI

    runner = Mock(return_value=9)
    monkeypatch.setattr(tui_entrypoint, "_run_tui_with_app", runner)

    options = TUIOptions()

    assert tui_entrypoint.run_tui(options) == 9
    runner.assert_called_once_with(options, app_cls=ClawCodexTUI)

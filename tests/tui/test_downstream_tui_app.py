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
    runner.assert_called_once()
    call_args = runner.call_args
    assert call_args[0][0] is options
    assert call_args[1].get("app_cls") is ClawCodexExtTUI


def test_upstream_tui_entrypoint_uses_upstream_app(monkeypatch):
    """Verify upstream run_tui constructs ClawCodexTUI, not ClawCodexExtTUI."""
    import src.entrypoints.tui as tui_entrypoint
    from src.tui.app import ClawCodexTUI

    # Monkey-patch _textual_available to True so run_tui proceeds.
    monkeypatch.setattr(tui_entrypoint, "_textual_available", lambda: True)

    # Allow the provider-building path to succeed without real config.
    # run_tui needs the provider factory path — use that.
    fake_provider = Mock()
    fake_provider.provider_name = "anthropic"

    monkeypatch.setattr(tui_entrypoint, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(tui_entrypoint, "get_provider_config", lambda _: {"api_key": "test-key", "default_model": "claude-3-sonnet-20240229", "base_url": "https://api.anthropic.com"})
    monkeypatch.setattr(tui_entrypoint, "get_provider_class", lambda _: Mock(return_value=fake_provider))

    called = False
    def mock_init(self, **kwargs):
        nonlocal called
        called = True
        assert type(self) is ClawCodexTUI
        monkeypatch.setattr(self, "run", Mock(return_value=None))
        object.__setattr__(self, '_exit_code', 0)

    monkeypatch.setattr(ClawCodexTUI, "__init__", mock_init)

    options = TUIOptions()
    result = tui_entrypoint.run_tui(options)
    assert called, "ClawCodexTUI was never instantiated"

"""ENTRY-2 — startup provider validation tests.

Port of ``validateProviderEnvForStartupOrExit`` (cli.tsx:149,
providerValidation.ts:479-528) at this port's provider-registry altitude;
plan: my-docs/get-parity-by-folder/entrypoints-refactoring-plan.md §ENTRY-2.

The load-bearing behaviors: ONE shared implementation across all three entry
paths (bare interactive, ``clawcodex tui``, headless), and the TS exit
split — non-interactive exits, an interactive TTY warns and continues.
"""
from __future__ import annotations

import sys

import pytest

from src.entrypoints.provider_validation import (
    get_provider_validation_error,
    validate_provider_at_startup,
)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_keyless_provider_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local providers (ollama) need no key — no error."""
    assert get_provider_validation_error("ollama") is None


def test_unknown_provider_errors() -> None:
    err = get_provider_validation_error("definitely-not-a-provider")
    assert err is not None
    assert "unable to load provider config" in err
    assert "definitely-not-a-provider" in err


def test_key_requiring_provider_without_key_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message names the provider and the repair path — byte-compatible
    with the text headless printed inline pre-ENTRY-2."""
    # Force the key-resolution to come back empty regardless of the
    # developer machine's env/config.
    monkeypatch.setattr("src.providers.resolve_api_key", lambda *a, **k: "")
    err = get_provider_validation_error("deepseek")
    assert err == (
        "error: API key for provider 'deepseek' is not configured. "
        "Run `clawcodex login` to set it up."
    )


def test_key_requiring_provider_with_key_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.providers.resolve_api_key", lambda *a, **k: "sk-x")
    assert get_provider_validation_error("deepseek") is None


def test_non_interactive_exits_with_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("src.providers.resolve_api_key", lambda *a, **k: "")
    with pytest.raises(SystemExit) as exc_info:
        validate_provider_at_startup("deepseek", interactive=False, exit_code=2)
    assert exc_info.value.code == 2
    assert "API key for provider 'deepseek'" in capsys.readouterr().err


def test_interactive_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The TS split (providerValidation.ts:508-528): an interactive TTY is
    NOT kicked out — warn and let the TUI surface the repair path."""
    monkeypatch.setattr("src.providers.resolve_api_key", lambda *a, **k: "")
    validate_provider_at_startup("deepseek", interactive=True)  # must not raise
    err_out = capsys.readouterr().err
    assert "Warning: provider configuration is incomplete." in err_out
    assert "API key for provider 'deepseek'" in err_out


def test_valid_provider_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    validate_provider_at_startup("ollama", interactive=False)
    validate_provider_at_startup("ollama", interactive=True)
    out = capsys.readouterr()
    assert out.err == ""


# ---------------------------------------------------------------------------
# Call-site coverage — all three entry paths share the ONE helper
# ---------------------------------------------------------------------------


def _install_validation_marker(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def _marker(provider_name, *, interactive, exit_code=2):
        calls.append({"provider": provider_name, "interactive": interactive})

    monkeypatch.setattr(
        "src.entrypoints.provider_validation.validate_provider_at_startup",
        _marker,
    )
    return calls


def test_headless_path_uses_the_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_headless routes through the helper (single implementation —
    critic P6). Marker replaces it; the run then fails later on the bogus
    provider, which is fine — the assertion is the routing."""
    calls = _install_validation_marker(monkeypatch)
    from src.entrypoints.headless import HeadlessOptions, run_headless

    opts = HeadlessOptions(prompt="hi", provider_name="definitely-not-a-provider")
    with pytest.raises(BaseException):  # noqa: PT011 — bogus provider fails downstream
        run_headless(opts)
    assert calls and calls[0]["provider"] == "definitely-not-a-provider"
    assert calls[0]["interactive"] is False


def test_tui_subcommand_invokes_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`clawcodex tui` never reaches main()'s dispatch region (critic P3) —
    it must run the helper itself, with the eager-parsed --provider."""
    calls = _install_validation_marker(monkeypatch)
    import src.cli as cli

    monkeypatch.setattr("src.init.run_pre_action", lambda args: None)
    monkeypatch.setattr(cli, "_gate_folder_trust", lambda: False)  # stop after validation
    rc = cli._run_tui_subcommand(["--provider", "ollama"])
    assert rc == 1  # stopped by the stubbed trust gate
    assert calls and calls[0]["provider"] == "ollama"

    calls.clear()
    rc = cli._run_tui_subcommand(["--provider=deepseek"])
    assert rc == 1
    assert calls and calls[0]["provider"] == "deepseek"


def test_main_dispatch_region_invokes_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare `-p` route: main() validates after permission resolution."""
    calls = _install_validation_marker(monkeypatch)
    import src.cli as cli

    monkeypatch.setattr("src.init.run_pre_action", lambda args: None)
    monkeypatch.setattr(cli, "_resolve_permission_state", lambda args: None)
    monkeypatch.setattr(cli, "_run_print_mode", lambda args: 0)
    monkeypatch.setattr(cli, "get_or_start_keychain_prefetch", lambda: None)
    monkeypatch.setattr(cli, "get_or_start_mdm_raw_read", lambda: None)
    monkeypatch.setattr(sys, "argv", ["clawcodex", "-p", "hello", "--provider", "ollama"])
    rc = cli.main()
    assert rc == 0
    assert calls and calls[0]["provider"] == "ollama"
    assert calls[0]["interactive"] is False  # -p → non-interactive


def test_fast_paths_never_invoke_validation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_validation_marker(monkeypatch)
    import src.cli as cli

    monkeypatch.setattr(sys, "argv", ["clawcodex", "--version"])
    cli.main()
    monkeypatch.setattr(sys, "argv", ["clawcodex", "mcp", "list"])
    cli.main()
    capsys.readouterr()
    assert calls == []

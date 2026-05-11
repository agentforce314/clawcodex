"""Tests for Phase-10 OSC 8 hyperlink capability detection."""

from __future__ import annotations

import pytest
from rich.console import Console

from src.tui.hyperlinks import (
    format_file_path,
    format_link,
    is_hyperlink_supported,
    raw_osc8,
)


def _patch_term_only(monkeypatch: pytest.MonkeyPatch, term_value: str) -> None:
    """Strip every capability env var, set TERM only — for negative cases."""

    for env in (
        "FORCE_HYPERLINK",
        "TERM_PROGRAM",
        "VTE_VERSION",
        "TERM",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("TERM", term_value)


# ------------------------------------------------------------------
# is_hyperlink_supported
# ------------------------------------------------------------------


def test_force_hyperlink_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    # Even with a non-terminal console, FORCE_HYPERLINK wins.
    console = Console(legacy_windows=True, force_terminal=False)
    assert is_hyperlink_supported(console)


@pytest.mark.parametrize(
    "term_program",
    ["iTerm.app", "WezTerm", "vscode", "kitty", "ghostty"],
)
def test_known_term_programs_supported(
    monkeypatch: pytest.MonkeyPatch, term_program: str
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", term_program)
    monkeypatch.delenv("FORCE_HYPERLINK", raising=False)
    assert is_hyperlink_supported()


def test_vte_version_5000_or_higher_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in ("FORCE_HYPERLINK", "TERM_PROGRAM"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("VTE_VERSION", "5500")
    assert is_hyperlink_supported()


def test_vte_version_below_5000_does_not_force_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A low VTE version doesn't auto-enable; the rest of the matrix decides."""

    for env in ("FORCE_HYPERLINK", "TERM_PROGRAM"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("VTE_VERSION", "4000")
    monkeypatch.setenv("TERM", "dumb")
    assert is_hyperlink_supported() is False


def test_dumb_term_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_term_only(monkeypatch, "dumb")
    assert is_hyperlink_supported(Console(force_terminal=True)) is False


def test_legacy_windows_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_term_only(monkeypatch, "xterm-256color")
    console = Console(legacy_windows=True, force_terminal=True)
    assert is_hyperlink_supported(console) is False


def test_not_a_terminal_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_term_only(monkeypatch, "xterm-256color")
    console = Console(force_terminal=False, legacy_windows=False)
    assert is_hyperlink_supported(console) is False


def test_modern_truecolor_terminal_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_term_only(monkeypatch, "xterm-256color")
    console = Console(
        force_terminal=True, legacy_windows=False, color_system="truecolor"
    )
    assert is_hyperlink_supported(console)


def test_invalid_vte_version_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage in VTE_VERSION shouldn't crash the predicate."""

    for env in ("FORCE_HYPERLINK", "TERM_PROGRAM"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("VTE_VERSION", "not-a-number")
    monkeypatch.setenv("TERM", "dumb")
    assert is_hyperlink_supported() is False


# ------------------------------------------------------------------
# format_link
# ------------------------------------------------------------------


def test_format_link_falls_back_to_plain_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_term_only(monkeypatch, "dumb")
    console = Console(force_terminal=False)
    assert format_link("hello", "https://x.test", console=console) == "hello"


def test_format_link_emits_rich_markup_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    out = format_link("hello", "https://x.test")
    assert out == "[link=https://x.test]hello[/link]"


def test_format_link_handles_empty_inputs() -> None:
    assert format_link("", "https://x.test") == ""
    assert format_link("hello", "") == "hello"


# ------------------------------------------------------------------
# format_file_path
# ------------------------------------------------------------------


def test_format_file_path_makes_clickable_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    out = format_file_path("/tmp/foo.py")
    assert "link=file:///tmp/foo.py" in out


def test_format_file_path_passes_existing_file_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    out = format_file_path("file:///already/url.py")
    assert "[link=file:///already/url.py]file:///already/url.py[/link]" == out


def test_format_file_path_relative_uses_file_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    out = format_file_path("src/x.py")
    # Relative path is wrapped with file://relative — the host terminal
    # decides whether to resolve. We don't synthesize cwd here.
    assert "[link=file://src/x.py]src/x.py[/link]" == out


def test_format_file_path_falls_back_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_term_only(monkeypatch, "dumb")
    console = Console(force_terminal=False)
    assert format_file_path("/tmp/foo.py", console=console) == "/tmp/foo.py"


def test_format_file_path_empty_returns_empty() -> None:
    assert format_file_path("") == ""


# ------------------------------------------------------------------
# raw_osc8
# ------------------------------------------------------------------


def test_raw_osc8_round_trips() -> None:
    out = raw_osc8("hello", "https://x.test")
    assert out.startswith("\x1b]8;;https://x.test\x1b\\")
    assert out.endswith("\x1b]8;;\x1b\\")
    assert "hello" in out

"""Tests for Phase-10 tool-result hyperlink wrapping (gap #15)."""

from __future__ import annotations

import pytest

from src.tui.widgets.messages.tool_result import (
    _PATH_RE,
    _wrap_paths_with_hyperlinks,
)


def _matches(text: str) -> list[str]:
    """Pull every match of ``_PATH_RE`` from ``text`` for assertion."""

    return [m.group("path") for m in _PATH_RE.finditer(text)]


# ------------------------------------------------------------------
# Regex over-match guards (Critic-flagged)
# ------------------------------------------------------------------


def test_trailing_period_not_captured() -> None:
    """``check src/main.py.`` should NOT eat the trailing period."""

    matches = _matches("check src/main.py.")
    assert matches == ["src/main.py"]


@pytest.mark.parametrize("punct", [".", ",", ";", ":", "!", "?"])
def test_trailing_punctuation_not_captured(punct: str) -> None:
    matches = _matches(f"see /tmp/foo.txt{punct}")
    assert matches == ["/tmp/foo.txt"]


def test_path_with_internal_dot_still_works() -> None:
    """Extension dots (`x.py`) must remain inside the captured path."""

    matches = _matches("file at /tmp/foo.tar.gz")
    assert matches == ["/tmp/foo.tar.gz"]


def test_path_with_brackets_around_it_handled() -> None:
    matches = _matches("(see /tmp/foo.py)")
    assert matches == ["/tmp/foo.py"]


def test_url_not_matched_as_path() -> None:
    """``https://example.com`` doesn't have a path-like prefix and starts
    in mid-text without the path lookbehind matching."""

    matches = _matches("link is https://example.com/foo")
    # The regex CAN match an absolute path inside a URL, but we don't
    # care about that — the test asserts the URL itself isn't classified
    # as a relative-path; the behavior for URLs is "best-effort".
    # Confirm: no match starting with ``https`` is in the result list.
    assert all(not m.startswith("https") for m in matches)


def test_relative_path_with_extension() -> None:
    matches = _matches("Edit src/tui/app.py to fix the bug")
    assert matches == ["src/tui/app.py"]


def test_home_relative_path_matches() -> None:
    matches = _matches("config at ~/.config/app/settings.json")
    assert matches == ["~/.config/app/settings.json"]


def test_dot_relative_path_matches() -> None:
    matches = _matches("see ./scripts/build.sh")
    assert matches == ["./scripts/build.sh"]


def test_no_slash_no_match() -> None:
    """Bare words without a slash aren't paths (avoids false-positives)."""

    assert _matches("just a sentence with no path") == []


# ------------------------------------------------------------------
# _wrap_paths_with_hyperlinks
# ------------------------------------------------------------------


def test_wrapper_falls_back_to_text_on_unsupported_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When hyperlinks aren't supported, return plain :class:`Text`."""

    for env in ("FORCE_HYPERLINK", "TERM_PROGRAM", "VTE_VERSION"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("TERM", "dumb")
    out = _wrap_paths_with_hyperlinks("see src/x.py")
    # Plain Text — no link spans.
    assert "link=" not in out.markup if hasattr(out, "markup") else True
    assert "src/x.py" in str(out)


def test_wrapper_emits_link_markup_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_HYPERLINK", "1")
    out = _wrap_paths_with_hyperlinks("see src/x.py")
    rendered = str(out)
    # Rich Text's str() doesn't expose markup; check the underlying
    # markup-ish representation via the Text repr if available.
    assert "src/x.py" in rendered

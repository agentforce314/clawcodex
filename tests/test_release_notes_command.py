"""Tests for the ``/release-notes`` command (Phase 12 — port of TS ``type:'local'``).

Reads the project's local ``CHANGELOG.md`` (the TS stored-changelog fallback path; the
GitHub fetch/cache is dropped). First ``local``-type port of the batch → maps to
``LocalCommand`` + ``set_call``; NOTE it is in ``BRIDGE_SAFE_COMMANDS`` (mirroring TS),
so — unlike the interactive ports — ``is_bridge_safe_command`` is **True**.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import __version__
from src.command_system import (
    RELEASE_NOTES_COMMAND,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType
from src.utils.release_notes import (
    RELEASES_URL,
    format_release_notes_for_display,
    get_release_notes_for_version,
    get_release_tag_url,
    normalize_public_version,
    parse_changelog,
    read_local_changelog,
)

_CHANGELOG = """# Changelog

Preamble text with a list:
- this preamble bullet must be skipped

## [0.5.0] - 2026-06-01

### Added
- new thing one
* new thing two
- 

### Fixed
- a bug fix

## v0.4.0

No bullets here, just prose.

## [0.1.0] - 2026-04-19

- initial release
"""


# --------------------------------------------------------------------------- #
# A. normalize_public_version (the semver-coerce mirror)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[0.1.0] - 2026-04-19", "0.1.0"),  # FIRST match wins (not the date)
        ("v0.5.0", "0.5.0"),
        ("v2.3", "2.3.0"),  # padded like semver coerce
        ("1", "1.0.0"),
        ("  0.5.0  ", "0.5.0"),
        ("2026-04-19", "2026.0.0"),  # coerce behavior on a bare date
        ("vNext", "Next"),  # no digits -> strip leading v
        ("VNext", "Next"),  # /^v/i — case-insensitive strip
        ("01.02.03", "01.02.03"),  # leading zeros kept VERBATIM (coerce rejects -> TS fallback)
    ],
)
def test_normalize_public_version(raw, expected):
    assert normalize_public_version(raw) == expected


# --------------------------------------------------------------------------- #
# B. parse_changelog / get_release_notes_for_version
# --------------------------------------------------------------------------- #
def test_parse_changelog():
    parsed = parse_changelog(_CHANGELOG)
    # Preamble (before the first ## heading) is skipped — its bullet must not leak.
    assert all("preamble" not in n for notes in parsed.values() for n in notes)
    # Keep-a-Changelog heading normalizes to the bare version key.
    assert parsed["0.5.0"] == ["new thing one", "new thing two", "a bug fix"]
    # ### sub-headers are ignored (bullets collected flat); '* ' bullets accepted.
    # Bullet-less sections are dropped entirely.
    assert "0.4.0" not in parsed
    assert parsed["0.1.0"] == ["initial release"]
    assert parse_changelog("") == {}


def test_get_release_notes_for_version():
    assert get_release_notes_for_version("v0.5.0", _CHANGELOG) == [
        "new thing one",
        "new thing two",
        "a bug fix",
    ]
    assert get_release_notes_for_version("9.9.9", _CHANGELOG) == []


# --------------------------------------------------------------------------- #
# C. format_release_notes_for_display
# --------------------------------------------------------------------------- #
def test_format_plain_notes():
    assert format_release_notes_for_display(["one", "two"]) == "- one\n- two"


def test_format_with_encoded_section_headers():
    notes = ["__section__:Added", "one", "__section__:Fixed", "two"]
    # Header first -> no leading blank; later headers preceded by a blank line.
    assert (
        format_release_notes_for_display(notes)
        == "Added:\n- one\n\nFixed:\n- two"
    )


def test_release_tag_url():
    assert get_release_tag_url("0.5.0") == f"{RELEASES_URL}/tag/v0.5.0"
    assert get_release_tag_url("v0.5.0") == f"{RELEASES_URL}/tag/v0.5.0"


def test_read_local_changelog_reads_repo_file():
    # Dev checkouts have CHANGELOG.md at the package root.
    content = read_local_changelog()
    assert content.startswith("# Changelog")


# --------------------------------------------------------------------------- #
# D. Command behavior (changelog source monkeypatched)
# --------------------------------------------------------------------------- #
def _ctx(tmp_path: Path):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path)


async def test_command_with_notes(tmp_path, monkeypatch):
    changelog = f"# Changelog\n\n## [{__version__}] - 2026-06-09\n\n- ported things\n"
    monkeypatch.setattr(
        "src.utils.release_notes.read_local_changelog", lambda: changelog
    )
    result = await RELEASE_NOTES_COMMAND.call("", _ctx(tmp_path))
    assert result.type == "text"
    assert result.value == (
        f"Release notes for {__version__}:\n- ported things\n\n"
        f"Full release page: {RELEASES_URL}/tag/v{__version__}"
    )


async def test_command_url_fallback_when_version_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.release_notes.read_local_changelog",
        lambda: "# Changelog\n\n## [0.0.1]\n\n- ancient\n",
    )
    result = await RELEASE_NOTES_COMMAND.call("", _ctx(tmp_path))
    assert result.value == f"Release notes: {RELEASES_URL}/tag/v{__version__}"


async def test_command_url_fallback_when_changelog_absent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.release_notes.read_local_changelog", lambda: "")
    result = await RELEASE_NOTES_COMMAND.call("ignored args", _ctx(tmp_path))
    assert result.value == f"Release notes: {RELEASES_URL}/tag/v{__version__}"


# --------------------------------------------------------------------------- #
# E. Registration + type + bridge-safety + dispatch fall-through
# --------------------------------------------------------------------------- #
def test_registered():
    assert "release-notes" in {c.name for c in get_builtin_commands()}
    assert "release-notes" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_metadata_mirrors_ts():
    assert RELEASE_NOTES_COMMAND.name == "release-notes"
    assert RELEASE_NOTES_COMMAND.description == "View release notes"
    assert RELEASE_NOTES_COMMAND.command_type == CommandType.LOCAL
    assert RELEASE_NOTES_COMMAND.supports_non_interactive is True


def test_bridge_safe_by_allowlist():
    # UNLIKE the interactive ports: release-notes is a LOCAL command listed in
    # BRIDGE_SAFE_COMMANDS (mirroring TS commands.ts) -> bridge-safe.
    assert is_bridge_safe_command(RELEASE_NOTES_COMMAND) is True


def test_dispatch_falls_through():
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/release-notes", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is False
    assert res.open_dialog is None


# --------------------------------------------------------------------------- #
# F. Engine end-to-end (headless)
# --------------------------------------------------------------------------- #
async def test_engine_succeeds_headless(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.release_notes.read_local_changelog", lambda: "")
    reg = CommandRegistry()
    reg.register(RELEASE_NOTES_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/release-notes")

    assert result.success is True
    assert result.text.startswith("Release notes")

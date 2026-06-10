"""Release-notes parsing for the ``/release-notes`` command.

Port of the *stored-changelog* half of ``typescript/src/utils/releaseNotes.ts`` (+ the
URL/version helpers from ``utils/version.ts``). The TS GitHub-releases **fetch/cache**
path (``fetchAndStoreChangelog`` etc.) is dropped — Python reads the project's local
``CHANGELOG.md`` instead, which is exactly the content TS's stored-changelog fallback
parses. The startup "what's new" banner consumers (``getRecentReleaseNotes`` /
``sliceReleaseNotesForDisplay``) are out of scope.
"""
from __future__ import annotations

import re
from pathlib import Path

# The Python project's analog of TS OPENCLAUDE_RELEASES_URL (version.ts:6).
RELEASES_URL = "https://github.com/agentforce314/clawcodex/releases"

# Encoded section-header marker — verbatim TS SECTION_HEADER_PREFIX. Only the
# (dropped) GitHub-release parser *produces* these; the formatter still renders them
# for parity.
_SECTION_HEADER_PREFIX = "__section__:"  # verbatim TS (releaseNotes.ts:21)

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def normalize_public_version(version: str) -> str:
    """Mirror of TS ``normalizePublicVersion`` (version.ts:9-16): semver-``coerce`` the
    string (the FIRST version-ish number found, padded to ``maj.min.patch``) — handles
    ``[0.1.0] - 2026-04-19``, ``v0.5.0``, and release-please link headings — else strip
    a leading ``v``."""
    trimmed = version.strip()
    m = _VERSION_RE.search(trimmed)
    if m:
        # Matched digits kept VERBATIM (no int-normalization): semver coerce rejects
        # leading-zero components (-> TS falls back to the strip-^v path, returning
        # them unchanged), so "01.02.03" must stay "01.02.03" here too.
        major, minor, patch = m.group(1), m.group(2) or "0", m.group(3) or "0"
        return f"{major}.{minor}.{patch}"
    return re.sub(r"^v", "", trimmed, flags=re.IGNORECASE)  # /^v/i — case-insensitive like TS


def parse_changelog(content: str) -> dict[str, list[str]]:
    """Faithful port of TS ``parseChangelog`` (releaseNotes.ts:316-358): split on
    ``^## `` headings (the first chunk — the preamble — is skipped), key each section by
    the normalized version from its heading line, collect only ``- ``/``* `` bullet
    lines (``###``/``####`` sub-headers are ignored), and keep sections with ≥1 bullet."""
    if not content:
        return {}
    release_notes: dict[str, list[str]] = {}
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue
        version_line = lines[0]
        if not version_line:
            continue
        version = normalize_public_version(version_line)
        if not version:
            continue
        notes = []
        for line in lines[1:]:
            t = line.strip()
            if t.startswith("- ") or t.startswith("* "):
                note = t[2:].strip()
                if note:
                    notes.append(note)
        if notes:
            release_notes[version] = notes
    return release_notes


def get_release_notes_for_version(version: str, content: str) -> list[str]:
    """TS ``getReleaseNotesForVersion`` (releaseNotes.ts:415-426)."""
    try:
        return parse_changelog(content).get(normalize_public_version(version), [])
    except Exception:
        return []


def is_release_section_header(note: str) -> bool:
    return note.startswith(_SECTION_HEADER_PREFIX)


def get_release_section_header_title(note: str) -> str:
    return note[len(_SECTION_HEADER_PREFIX):] if is_release_section_header(note) else note


def format_release_notes_for_display(notes: list[str]) -> str:
    """TS ``formatReleaseNotesForDisplay`` (releaseNotes.ts:428-444): encoded section
    headers render as ``"{Title}:"`` preceded by a blank line when not first; plain
    notes render as ``"- {note}"``."""
    lines: list[str] = []
    for note in notes:
        if is_release_section_header(note):
            if lines:
                lines.append("")
            lines.append(f"{get_release_section_header_title(note)}:")
            continue
        lines.append(f"- {note}")
    return "\n".join(lines)


def get_release_tag_url(version: str) -> str:
    """TS ``getReleaseTagUrl`` (version.ts:54-56)."""
    return f"{RELEASES_URL}/tag/v{normalize_public_version(version)}"


def read_local_changelog() -> str:
    """Best-effort read of the project's ``CHANGELOG.md`` (the stored-changelog analog).
    Resolved relative to the ``src`` package (repo root in dev checkouts); absent (e.g.
    pip installs) or unreadable → ``""`` (the command then falls back to the URL line,
    matching TS's notes-empty case)."""
    try:
        import src

        path = Path(src.__file__).resolve().parent.parent / "CHANGELOG.md"
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


__all__ = [
    "RELEASES_URL",
    "normalize_public_version",
    "parse_changelog",
    "get_release_notes_for_version",
    "is_release_section_header",
    "get_release_section_header_title",
    "format_release_notes_for_display",
    "get_release_tag_url",
    "read_local_changelog",
]

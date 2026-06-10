"""release-notes — ``/release-notes`` changelog viewer (port of TS ``type:'local'``).

Port of ``typescript/src/commands/release-notes/``. TS tries a GitHub-releases fetch and
falls back to the **stored changelog**; Python reads the project's local ``CHANGELOG.md``
(exactly the stored-changelog path) and **drops the network fetch/cache** (the same
unported-subsystem boundary as ``/mcp``/``/model``). When the current version has no
section (or the changelog is absent, e.g. pip installs), the output degrades to the
release-page URL — faithful to TS's notes-empty case.

Maps to :class:`LocalCommand` (the first ``type:'local'`` single-command port of this
batch — prior ports were ``local-jsx`` → ``InteractiveCommand``), using the established
``set_call`` pattern from builtins (help/clear/…). Sync impl: the only async part of TS
was the dropped fetch.
"""
from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult


def release_notes_call(args: str, context: CommandContext) -> LocalCommandResult:
    # Lazy imports keep `import src.command_system` light (the established discipline).
    from src import __version__
    from src.utils.release_notes import (
        format_release_notes_for_display,
        get_release_notes_for_version,
        get_release_tag_url,
        normalize_public_version,
        read_local_changelog,
    )

    # Normalize once and reuse for header + URL (TS interpolates the already-public
    # build version; getReleaseTagUrl normalizes internally — identical result).
    version = normalize_public_version(__version__)
    notes = get_release_notes_for_version(version, read_local_changelog())
    url = get_release_tag_url(version)
    if notes:
        return LocalCommandResult(
            type="text",
            value=(
                f"Release notes for {version}:\n"
                f"{format_release_notes_for_display(notes)}\n\n"
                f"Full release page: {url}"
            ),
        )
    return LocalCommandResult(type="text", value=f"Release notes: {url}")


RELEASE_NOTES_COMMAND = LocalCommand(
    name="release-notes",
    description="View release notes",  # verbatim TS index.ts
    supports_non_interactive=True,  # verbatim TS index.ts
)
RELEASE_NOTES_COMMAND.set_call(release_notes_call)


__all__ = ["RELEASE_NOTES_COMMAND", "release_notes_call"]

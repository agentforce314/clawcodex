"""Team-memory path resolution and three-layer path-traversal defense.

Ports `typescript/src/memdir/teamMemPaths.ts`. Team memory is a
``team/`` subdirectory under the per-project auto-memory directory, so
disabling auto-memory transitively disables team memory.

The chapter's "Defense in Depth" section motivates three layers:

1. **Input sanitization** (`_sanitize_path_key`) — rejects null bytes,
   URL-encoded traversals (``%2e%2e%2f``), NFKC normalization attacks
   (fullwidth ``．．／`` normalizes to ASCII ``../``), backslashes, and
   absolute paths.
2. **String-level containment** (`os.path.abspath`/`os.path.normpath`)
   — normalizes ``..`` segments and checks the candidate is inside
   ``teamDir + sep`` so ``team-evil/`` cannot match ``team/``.
3. **Symlink resolution** (`_realpath_deepest_existing`) — resolves
   symlinks on the deepest existing ancestor and re-joins the
   non-existing tail, catching attacks where a symlink inside
   ``team/`` points outside.

All validation failures raise :class:`PathTraversalError`. No partial
successes, no fallbacks — fail closed.

Sync rather than async (TS is async because Node's ``fs.promises`` is).
Python's ``os.path.realpath``/``os.path.islink`` are sync and the
``load_memory_prompt()`` call site is sync.
"""

from __future__ import annotations

import errno
import os
import unicodedata
import urllib.parse

from .paths import (
    _is_env_truthy,
    get_auto_mem_path,
    is_auto_memory_enabled,
)

__all__ = [
    "PathTraversalError",
    "is_team_memory_enabled",
    "get_team_mem_path",
    "get_team_mem_entrypoint",
    "is_team_mem_path",
    "is_team_mem_file",
    "validate_team_mem_write_path",
    "validate_team_mem_key",
]


class PathTraversalError(Exception):
    """Raised when path validation detects a traversal or injection attempt.

    All three defense layers raise this single exception type so that
    callers can ``except PathTraversalError`` once and skip a malicious
    entry without aborting an entire batch.
    """


def _sanitize_path_key(key: str) -> str:
    """Reject dangerous patterns in a server-supplied relative key.

    Mirrors TS ``sanitizePathKey``. Raises :class:`PathTraversalError`
    on any of: null byte, URL-encoded traversal, NFKC-normalization
    attack, backslash, absolute path.
    """
    # Null bytes truncate paths in C-based syscalls.
    if "\0" in key:
        raise PathTraversalError(f'Null byte in path key: "{key}"')

    # URL-encoded traversals (e.g. %2e%2e%2f → ../).
    try:
        decoded = urllib.parse.unquote(key, errors="strict")
    except (UnicodeDecodeError, ValueError):
        # Malformed percent-encoding — not valid URL-encoding, so no
        # URL-encoded traversal is possible. Fall through.
        decoded = key
    if decoded != key and (".." in decoded or "/" in decoded):
        raise PathTraversalError(f'URL-encoded traversal in path key: "{key}"')

    # NFKC normalization attack: fullwidth ．．／ (U+FF0E U+FF0F) normalizes
    # to ASCII ../ under NFKC. While Python's os.path treats these as
    # literal bytes, downstream layers or filesystems may normalize —
    # reject for defense in depth.
    normalized = unicodedata.normalize("NFKC", key)
    if normalized != key and (
        ".." in normalized
        or "/" in normalized
        or "\\" in normalized
        or "\0" in normalized
    ):
        raise PathTraversalError(
            f'Unicode-normalized traversal in path key: "{key}"'
        )

    # Backslash (Windows separator used as traversal vector).
    if "\\" in key:
        raise PathTraversalError(f'Backslash in path key: "{key}"')

    # Absolute paths.
    if key.startswith("/"):
        raise PathTraversalError(f'Absolute path key: "{key}"')

    return key


def is_team_memory_enabled() -> bool:
    """Whether team-memory features are active.

    Team memory is a subdirectory of auto-memory, so it requires
    auto-memory to be enabled. Behind that, gated on
    ``CLAUDE_CODE_TEAM_MEMORY`` env var (defaults to off).

    The TS gate is a GrowthBook feature flag (``tengu_herring_clock``).
    Python's analytics surface is not yet ported (ch16 scope), so we
    expose the same shape via env var.
    """
    if not is_auto_memory_enabled():
        return False
    return _is_env_truthy(os.environ.get("CLAUDE_CODE_TEAM_MEMORY"))


def get_team_mem_path() -> str:
    """Team-memory directory: ``<auto_mem>/team/`` with trailing sep.

    NFC-normalized to match the auto-memory contract (see
    :func:`src.memdir.paths.get_auto_mem_path`). The trailing separator
    is load-bearing for the prefix-attack check
    (``team-evil/`` must not match ``team/``).
    """
    return unicodedata.normalize(
        "NFC", os.path.join(get_auto_mem_path(), "team") + os.sep
    )


def get_team_mem_entrypoint() -> str:
    """``MEMORY.md`` inside the team-memory directory."""
    return os.path.join(get_auto_mem_path(), "team", "MEMORY.md")


def _realpath_deepest_existing(absolute_path: str) -> str:
    """Resolve symlinks on the deepest existing ancestor of *absolute_path*.

    The target file may not exist yet (we may be about to create it),
    so we walk up the directory tree until ``realpath(strict=True)``
    succeeds, then rejoin the non-existing tail onto the resolved
    ancestor.

    SECURITY: ``os.path.normpath`` does NOT resolve symlinks. An
    attacker who places a symlink inside ``teamDir`` pointing outside
    (e.g. to ``~/.ssh/authorized_keys``) would pass a normpath-only
    containment check. Using ``realpath()`` on the deepest existing
    ancestor compares actual filesystem locations.

    Raises :class:`PathTraversalError` on:
      * A dangling symlink in *absolute_path* itself (the link entry
        exists, but the target does not — writing would still follow
        it and create the target outside teamDir).
      * Symlink loop (``ELOOP``).
      * Unverifiable containment (``EACCES``, ``EIO``).
    """
    tail: list[str] = []
    current = absolute_path
    while True:
        parent = os.path.dirname(current)
        if current == parent:
            # Reached filesystem root without finding an existing ancestor.
            # Fall back to the input — the caller's containment check
            # will reject if appropriate.
            return absolute_path

        try:
            real_current = os.path.realpath(current, strict=True)
            if not tail:
                return real_current
            # Rejoin the non-existing tail in reverse (deepest popped first).
            return os.path.join(real_current, *reversed(tail))
        except OSError as exc:
            code = exc.errno
            if code in (errno.ENOENT, errno.ENOTDIR):
                # ENOENT could be truly non-existent OR a dangling
                # symlink whose target does not exist. ``os.path.islink``
                # distinguishes: true for the dangling-symlink case.
                if os.path.islink(current):
                    raise PathTraversalError(
                        f'Dangling symlink detected (target does not exist): "{current}"'
                    ) from exc
                # Otherwise: walk up and try the parent.
            elif code == errno.ELOOP:
                raise PathTraversalError(
                    f'Symlink loop detected in path: "{current}"'
                ) from exc
            elif code == errno.ENAMETOOLONG:
                # Treat like ENOENT — walk up to find an ancestor.
                pass
            else:
                # EACCES, EIO, EPERM, etc. — cannot verify containment.
                # Wrap as PathTraversalError so callers can skip this entry.
                raise PathTraversalError(
                    f'Cannot verify path containment ({code}): "{current}"'
                ) from exc

        # Walk up. Pop the deepest non-existing segment onto the tail.
        tail.append(current[len(parent) + len(os.sep):])
        current = parent


def _is_real_path_within_team_dir(real_candidate: str) -> bool:
    """Check whether *real_candidate* is inside the real team directory.

    Both sides are realpath'd so the comparison is between canonical
    filesystem locations. If the team directory does not exist yet,
    returns ``True``: a symlink-escape requires a pre-existing symlink
    inside ``teamDir``, which itself requires ``teamDir`` to exist.

    Returns ``False`` on permission or other I/O errors (fail closed).
    """
    team_dir = get_team_mem_path().rstrip("/\\")
    try:
        real_team_dir = os.path.realpath(team_dir, strict=True)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            # Team dir doesn't exist — symlink escape impossible.
            return True
        # EACCES, EIO, etc. — fail closed.
        return False
    if real_candidate == real_team_dir:
        return True
    # Trailing-sep prefix check: ``/foo/team-evil`` must not match
    # ``/foo/team``.
    return real_candidate.startswith(real_team_dir + os.sep)


def is_team_mem_path(file_path: str) -> bool:
    """String-level prefix check: is *file_path* under the team dir?

    ``os.path.normpath`` eliminates ``..`` segments so ``team/../etc``
    is rejected. Does NOT resolve symlinks — use
    :func:`validate_team_mem_write_path` or :func:`validate_team_mem_key`
    for write-side validation, which add symlink resolution.
    """
    if not file_path:
        return False
    resolved = os.path.normpath(file_path)
    team_dir = get_team_mem_path()
    # team_dir already ends with os.sep (from get_team_mem_path).
    # Also accept exact match (resolved + sep == team_dir).
    return resolved.startswith(team_dir) or resolved + os.sep == team_dir


def is_team_mem_file(file_path: str) -> bool:
    """Whether *file_path* is a team-memory file and team-memory is on."""
    return is_team_memory_enabled() and is_team_mem_path(file_path)


def validate_team_mem_write_path(file_path: str) -> str:
    """Validate that *file_path* is safe for writing to the team dir.

    Returns the resolved absolute path. Raises
    :class:`PathTraversalError` on null bytes, ``..`` escape, or
    symlink-based escape.
    """
    if "\0" in file_path:
        raise PathTraversalError(f'Null byte in path: "{file_path}"')

    # First pass: normalize .. segments and check string-level containment.
    resolved = os.path.abspath(file_path)
    team_dir = get_team_mem_path()
    if not resolved.startswith(team_dir):
        raise PathTraversalError(
            f'Path escapes team memory directory: "{file_path}"'
        )

    # Second pass: resolve symlinks on the deepest existing ancestor
    # and verify real-path containment.
    real_path = _realpath_deepest_existing(resolved)
    if not _is_real_path_within_team_dir(real_path):
        raise PathTraversalError(
            f'Path escapes team memory directory via symlink: "{file_path}"'
        )
    return resolved


def validate_team_mem_key(relative_key: str) -> str:
    """Validate a relative path key (e.g. from a team-sync server).

    Sanitizes the key, joins it with the team directory, then runs the
    same two-pass validation as :func:`validate_team_mem_write_path`.
    Returns the resolved absolute path.
    """
    _sanitize_path_key(relative_key)
    team_dir = get_team_mem_path()
    full_path = os.path.join(team_dir, relative_key)

    resolved = os.path.abspath(full_path)
    if not resolved.startswith(team_dir):
        raise PathTraversalError(
            f'Key escapes team memory directory: "{relative_key}"'
        )

    real_path = _realpath_deepest_existing(resolved)
    if not _is_real_path_within_team_dir(real_path):
        raise PathTraversalError(
            f'Key escapes team memory directory via symlink: "{relative_key}"'
        )
    return resolved

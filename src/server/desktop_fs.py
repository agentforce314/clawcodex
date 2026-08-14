"""Directory browsing for the GUI clients' workspace picker.

A browser has no OS file chooser it can trust with a real path: the File System
Access API hands back an opaque handle, not something the agent can `cd` into.
So the picker browses the *server's* filesystem through this module, one level
at a time.

Shape mirrors the reference implementation's `DirectoryListing`
(`packages/host/directory-picker`), because two properties in it are worth
copying exactly:

- **Every path is absolute and comes from the server.** Clients never join path
  segments themselves, which is what keeps a Windows path from being assembled
  with the wrong separator by a client that has never seen one.
- **`crumbs` is the whole ancestor chain**, so the picker's breadcrumb is a row
  of jump targets rather than a string the client has to re-split.

Scope: read-only, directories only. This adds no reach the agent does not
already have — it runs in this process, whose tools can read the filesystem —
and the transport is the same loopback, token-gated socket. What it must not do
is *hide* a failure: an unreadable directory raises rather than returning an
empty listing that looks like an empty folder.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

# Entries returned for one level before the tail is cut. A home directory with
# a few thousand children should not stall the picker, and nobody scrolls that
# far — the client shows `truncated` and the user narrows with the filter.
DEFAULT_LIMIT = 500


def _is_hidden(path: Path, name: str) -> bool:
    """Hidden by the platform's own convention.

    POSIX is the dot prefix. Windows carries a real attribute, and the dot
    convention is not used there — a `.config` directory copied onto Windows is
    an ordinary folder, while `AppData` is genuinely hidden and has no dot.
    """
    if name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = path.stat().st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


def _entry(path: Path, name: str | None = None) -> dict[str, Any]:
    return {
        "name": name if name is not None else (path.name or str(path)),
        "path": str(path),
        "hidden": _is_hidden(path, name if name is not None else path.name),
    }


def _crumbs(path: Path) -> list[dict[str, Any]]:
    """Ancestor chain from the filesystem root to ``path``, inclusive.

    The root crumb carries its full path as its name (``/`` on POSIX, ``C:\\``
    on Windows) — a base name would be empty and unclickable.
    """
    chain = [path, *path.parents]
    crumbs: list[dict[str, Any]] = []
    for ancestor in reversed(chain):
        name = ancestor.name or str(ancestor)
        crumbs.append({"name": name, "path": str(ancestor), "hidden": False})
    return crumbs


def home_directory() -> Path:
    """The account's home directory, or the filesystem root if it has none."""
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return Path(os.sep)


def list_directory(
    path: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """One directory level: its children, its ancestry, and the home root.

    ``path`` absent (or empty) lists the home directory — the picker's opening
    view. Children are directories only, including symlinks that resolve to
    one; a file is not a workspace.

    Raises ``ValueError`` when the target does not exist, is not a directory,
    or cannot be read. That is deliberate: an empty ``entries`` list must mean
    "this directory has no subdirectories", never "we could not look".
    """
    home = home_directory()
    target = Path(path).expanduser() if path else home

    try:
        resolved = target.resolve()
    except OSError as exc:  # pragma: no cover - platform-specific
        raise ValueError(f"cannot resolve {target}: {exc}") from exc

    if not resolved.exists():
        raise ValueError(f"no such directory: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"not a directory: {resolved}")

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(resolved) as scan:
            for item in scan:
                try:
                    # follow_symlinks: a symlink INTO a project is a normal way
                    # to reach one, and refusing it would be surprising.
                    if not item.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    # A broken link or a mount we cannot stat: skip the row
                    # rather than fail the whole listing.
                    continue
                entries.append(_entry(Path(item.path), item.name))
    except PermissionError as exc:
        raise ValueError(f"permission denied: {resolved}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read {resolved}: {exc}") from exc

    # Case-insensitive so `Downloads` and `apps` interleave the way a person
    # reading the list expects, rather than by byte value.
    entries.sort(key=lambda entry: str(entry["name"]).lower())

    if limit > 0 and len(entries) > limit:
        entries = entries[:limit]
        truncated = True

    return {
        "path": str(resolved),
        "home": str(home),
        "crumbs": _crumbs(resolved),
        "entries": entries,
        "truncated": truncated,
    }


__all__ = ["DEFAULT_LIMIT", "home_directory", "list_directory"]

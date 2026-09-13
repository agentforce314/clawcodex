"""Reading files and directories *inside a session's workspace*, for the web client.

`desktop_fs.py` browses the whole filesystem so the workspace picker can find a
project. This module is the opposite: once a session has a workspace, it is the
only tree the right-hand sidebar reads, one page and one directory level at a
time.

Two rules shape the whole module.

**Everything is confined to the workspace root.** A path is resolved (symlinks
and all) and must land inside the resolved root; anything else is refused.

That is honesty, not a security boundary, and it stays honesty under every
binding. One token gates this whole socket, so anyone who can call these two
methods can equally call `session.create` and `prompt.submit` and have the agent
read the disk with its own tools — including through
`clawcodex web --allow-remote`, which changes *who can reach the socket* and not
that conclusion. What the check buys is that a column claiming to show the
workspace cannot be walked out of through `..` or a symlink pointing elsewhere.

Containment is nonetheless decided **before** the path is inspected, where the
reference service inspects first so a caller learns whether an outside path
exists and what kind it is before being refused. That is a simplicity choice
rather than a leak-prevention one — the reference's own justification, that the
caller can already read the host through the agent, holds here too — and it
costs exactly one sentence: a missing file outside the root reads "outside the
workspace" rather than "gone".

A deliberate divergence from the reference service, which refuses a symlink
outright *wherever it points, including back inside the workspace*: a link into
a project is an ordinary way to reach a file, and refusing one that resolves
inside the tree would surprise the reader for no gain the containment check does
not already give. A link that resolves outside is refused, and the tree types it
`other` rather than offering a click that would fail.

**Failure is part of the answer, not an exception.** The gateway's error
envelope carries a message and nothing else (`desktop_gateway.py::_dispatch`),
and a reader needs the *code* to say why in terms of the file — "that file is
gone" reads differently from "that page is too large". So every function returns
either ``{"ok": True, ...}`` or ``{"ok": False, "error": {"code", "message"}}``,
with codes borrowed from the reference client's ``workspace-file/*`` vocabulary
so the two front ends can say the same sentences.

Text reads are paged by line, never whole: a file has no bound, and a page does.
The byte reads behind the image, PDF and HTML previews are whole — a picture has
no page — and bounded by ``MAX_FILE_BYTES`` instead.
"""

from __future__ import annotations

import base64
import os
import re
import stat as stat_module
from pathlib import Path
from typing import Any

# One page of lines — the reference service's own default. The reader asks for
# the next page when it reaches the end of the loaded text, so this bounds a
# single response, not the file; the byte cap below is what actually binds on a
# page of long lines.
#
# It also multiplies with the client's jump-to-line bound (`WALK_PAGE_LIMIT`):
# five pages of 2000 put a `read` row's line 12,000 out of reach, where five of
# 5000 reach 25,000 for the same number of round trips.
MAX_LINES = 5000
# ...and one page's bytes, independently: 2000 lines of minified JavaScript is
# not a page anybody wants delivered over a socket that also carries the turn.
MAX_BYTES = 2 * 1024 * 1024
# The whole-file cap of the byte reads: what an image, a PDF or an HTML
# document may weigh before the sidebar refuses to fetch it entire. The
# reference service's own full-file default.
MAX_FILE_BYTES = 32 * 1024 * 1024
# Children returned for one directory level before the tail is cut. The
# reference service's own default; the tail it cuts is the alphabetical tail,
# because the level is ordered before it is cut (see `list_dir`).
MAX_ENTRIES = 2000


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": f"workspace-file/{code}", "message": message}
    if details:
        error["details"] = details
    return {"ok": False, "error": error}


def _order(name: str) -> tuple[tuple[tuple[int, Any], ...], str]:
    """Sort key for one directory entry: case-insensitive, digits as numbers.

    The client collates the level it receives with
    ``Intl.Collator(numeric: true, sensitivity: 'base')``, and over the entry
    cap the *server's* order decides which names survive — so a cut made in
    codepoint order keeps ``file1, file10, file100`` and drops ``file2``
    through ``file9``, which is the arbitrary-looking hole this ordering exists
    to prevent. Non-padded sequential names are exactly what fills a directory
    past the cap.

    Costs no syscall: a name is all it reads. The full name is the tie-break, so
    two entries differing only in case still have a stable order.

    ``isdecimal`` rather than ``isdigit``, and the difference is the whole
    function's totality. ``\d`` splits on Unicode category ``Nd``, which is
    exactly what ``isdecimal`` reports and exactly what ``int`` accepts;
    ``isdigit`` is *wider* — it is true for ``²`` and ``①``, which ``\d`` never
    split out, so they arrive as ordinary text that answers yes and then makes
    ``int`` raise. One file named ``²`` used to take its whole directory out
    that way, with the ``ValueError`` escaping this module entirely. With the
    three sets lined up, ``int`` here can never raise.
    """
    parts = tuple(
        (1, int(part)) if part.isdecimal() else (0, part)
        for part in re.split(r"(\d+)", name.lower())
    )

    return parts, name


def _inside(root: Path, path: Path) -> bool:
    """Whether ``path`` resolves inside ``root``. Both sides fully resolved, so
    a symlink pointing out of the tree is outside it however it was reached."""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - platform-specific
        return False
    return resolved == root or root in resolved.parents


def _resolve(root: str, path: str | None) -> tuple[Path, Path] | dict[str, Any]:
    """The (resolved root, resolved target) pair, or the failure that stops it.

    ``path`` may be absolute or relative to the root; an empty one means the
    root itself. Both sides are fully resolved before they are compared, so a
    symlink that points out of the workspace is refused even though the link
    itself sits inside it.
    """
    if not root:
        return _failure("unknown-workspace", "This session has no workspace directory.")

    try:
        resolved_root = Path(root).expanduser().resolve()
    except OSError as exc:  # pragma: no cover - platform-specific
        return _failure("unavailable", f"cannot resolve {root}: {exc}")

    # Stripped only to decide "no path given": a POSIX filename may legally
    # begin or end with a space, and trimming it would silently read a
    # different file.
    raw = path or ""
    candidate = Path(raw).expanduser() if raw.strip() else resolved_root
    if not candidate.is_absolute():
        candidate = resolved_root / candidate

    try:
        # strict=False: a missing file must reach the reader as `not-found`
        # rather than as an unresolvable path, and its *parents* still resolve,
        # which is what the containment check needs.
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - platform-specific
        return _failure("unavailable", f"cannot resolve {candidate}: {exc}")

    if not (resolved == resolved_root or resolved_root in resolved.parents):
        return _failure(
            "outside-workspace",
            "That path is outside the workspace, so the sidebar will not read it.",
        )

    return resolved_root, resolved


def _version(stat: os.stat_result) -> str:
    """A cheap identity for "the file as it was when we read it".

    Modification time and size together: enough to notice a rewrite between two
    pages, which is all the reader does with it. Not a hash — hashing a file to
    show a page of it would cost more than the page.
    """
    return f"{stat.st_mtime_ns}-{stat.st_size}"


def read_file(
    root: str,
    path: str,
    *,
    offset: int = 1,
    limit: int | None = None,
) -> dict[str, Any]:
    """One page of a text file's lines, starting at the 1-based ``offset``.

    The page is the lines ``[offset, offset+limit)``, joined with ``\\n`` and
    carrying its own ``lines`` count — so "one empty line" and "past the end of
    the file" are different answers (``lines`` 1 against 0) rather than the same
    empty string. ``eof`` says whether the file ends inside this page.

    ``limit`` only ever narrows: a caller asking for more than ``MAX_LINES``
    gets ``MAX_LINES``.
    """
    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    _, target = resolved

    try:
        stat = target.stat()
    except FileNotFoundError:
        return _failure("not-found", "That file is gone. It may have been moved or deleted.")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    # Checked before opening: a FIFO would block `open` itself, and this socket
    # also carries the turn's stream.
    if not stat_module.S_ISREG(stat.st_mode):
        return _failure(
            "not-regular-file", "That is not a regular file, so it has no text to show."
        )

    start = max(1, int(offset or 1))
    count = MAX_LINES if limit is None else max(1, min(int(limit), MAX_LINES))

    try:
        # Read as bytes and decode the page, not the file: a binary file has to
        # fail as `not-text` rather than raise out of the line iterator halfway
        # through, and the byte cap has to be measured on bytes.
        with target.open("rb") as handle:
            # Re-stat the open handle: the version has to describe the bytes
            # this page came from. A write between the stat above and this open
            # would otherwise label new content with the old version, and the
            # client would merge two files into one.
            stat = os.fstat(handle.fileno())
            for _ in range(start - 1):
                if handle.readline() == b"":
                    break
            collected: list[bytes] = []
            size = 0
            eof = True
            for _ in range(count):
                line = handle.readline()
                if line == b"":
                    break
                size += len(line)
                if size > MAX_BYTES:
                    return _failure(
                        "too-large",
                        "That page is too large; the sidebar does not read pages above the limit.",
                        limit=MAX_BYTES,
                    )
                collected.append(line)
            else:
                eof = handle.readline() == b""
    except PermissionError:
        return _failure("unavailable", f"permission denied: {target}")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    try:
        decoded = b"".join(collected).decode("utf-8")
    except UnicodeDecodeError:
        return _failure("not-text", "That is not a text file, so it cannot be shown here.")

    # A NUL byte is valid UTF-8, so decoding alone lets a binary through: a
    # UTF-16 file of ASCII decodes cleanly and would render as text interleaved
    # with invisible NULs. The reference service scans the page for one too.
    if "\x00" in decoded:
        return _failure("not-text", "That is not a text file, so it cannot be shown here.")

    # CRLF is a line ending, not a character in the line: the client splits on
    # "\n" and would otherwise render a stray carriage return at every line end
    # of a file written on Windows. This costs byte fidelity knowingly — `text`
    # is no longer a verbatim slice of the file — and it does nothing for a lone
    # "\r", which `readline` does not split on either, so a classic-Mac file
    # still arrives as one very long line.
    text = decoded.replace("\r\n", "\n")
    # The page's terminator is not part of its last line either: the client
    # renders one block per line and adds the break itself, and keeping the
    # "\n" here would give every page a phantom empty last line.
    lines = len(collected)
    if text.endswith("\n"):
        text = text[:-1]

    return {
        "ok": True,
        "absolute_path": str(target),
        "version": _version(stat),
        "bytes": stat.st_size,
        "offset": start,
        "text": text,
        "lines": lines,
        "eof": eof,
    }


def read_bytes(root: str, path: str) -> dict[str, Any]:
    """A whole file's bytes, base64-encoded, for the viewers that need the file entire.

    An image, a PDF or an HTML document has no page to read it by, so this is
    the one read that returns a file whole — which is why it is the one read
    with a whole-file cap. ``bytes`` is the size the file was stat'd at;
    ``offset`` and ``eof`` are carried so the shape matches the reference
    client's byte window, of which this is always the only one.
    """
    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    _, target = resolved

    try:
        stat = target.stat()
    except FileNotFoundError:
        return _failure("not-found", "That file is gone. It may have been moved or deleted.")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    if not stat_module.S_ISREG(stat.st_mode):
        return _failure(
            "not-regular-file", "That is not a regular file, so it has no bytes to show."
        )

    too_large = _failure(
        "too-large",
        "That file is too large; the sidebar does not read files above the limit.",
        limit=MAX_FILE_BYTES,
    )
    if stat.st_size > MAX_FILE_BYTES:
        return too_large

    try:
        with target.open("rb") as handle:
            # Re-stat the open handle, as `read_file` does: the version has to
            # describe the bytes actually returned.
            stat = os.fstat(handle.fileno())
            # One byte past the cap: a file that grew between the stat and the
            # open is caught by what was read rather than by what was reported.
            data = handle.read(MAX_FILE_BYTES + 1)
    except PermissionError:
        return _failure("unavailable", f"permission denied: {target}")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    if len(data) > MAX_FILE_BYTES:
        return too_large

    return {
        "ok": True,
        "absolute_path": str(target),
        "version": _version(stat),
        "bytes": stat.st_size,
        "offset": 0,
        "data": base64.b64encode(data).decode("ascii"),
        "eof": True,
    }


def read_related(root: str, path: str, relative: str) -> dict[str, Any]:
    """A file named relative to another file's directory, whole.

    What an HTML document's own ``<link href="css/app.css">`` or
    ``<script src="app.js">`` names: the client hands over the document and
    the attribute, and the directory is joined here so the client never
    states an absolute path for the asset. Only a relative filesystem path is
    accepted — not an absolute one, not a URL — and the joined path is then
    read like any other, confined to the workspace like any other. The
    reference service lets a related file live outside the workspace; this
    module's one rule holds for it too.
    """
    normalized = (relative or "").replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[a-z][a-z\d+.-]*:", normalized, re.IGNORECASE)
        or "\x00" in normalized
    ):
        return _failure(
            "bad-relative-path",
            "A related file is named relative to its document, not by an absolute path or a URL.",
        )

    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    _, base = resolved

    return read_bytes(root, str(base.parent / normalized))


def list_dir(root: str, path: str | None = None) -> dict[str, Any]:
    """One directory level under the workspace root: its direct children.

    Every entry is reported, dotfiles included — this tree is the workspace as
    it is, not a filtered view; the client decides what to draw. Entries that
    are neither a file nor a directory are typed ``other`` and shown as
    unopenable rather than hidden, so a directory is described whole.

    **The level is ordered before it is cut.** The client sorts what arrives,
    but sorting a cut made in ``scandir`` order only makes an arbitrary sample
    look deliberate: over the cap you would get *some* N of the children, so
    ``a.txt`` could be missing while ``z.txt`` was present, and Reload — which
    walks the same directory in the same order — would keep hiding it. Cutting
    the alphabetical tail is what ``truncated`` actually claims.

    The order is ``_order``'s: case-insensitive, digits compared as numbers,
    which is the client's collation minus one thing it cannot afford. The client
    also groups **directories first**, and that does need every child's type —
    the stat-per-child this deliberately avoids — so over the cap the cut can
    still fall a few names from where the displayed list ends. (Accent folding
    is the other difference, and is not chased.) Stated in ``ui-web/README.md``.
    """
    resolved = _resolve(root, path)
    if isinstance(resolved, dict):
        return resolved
    resolved_root, target = resolved

    entries: list[dict[str, Any]] = []
    truncated = False
    # No `exists()` / `is_dir()` probe first: both swallow the OSError, so an
    # unreadable directory would be reported as a missing one. `scandir` raises
    # the error that actually happened.
    try:
        with os.scandir(target) as scan:
            # Ordered on the names alone, which `readdir` already handed over,
            # and cut BEFORE anything is stat'd: every branch below costs a
            # syscall per child, so statting the whole level to return 2000 of
            # it would make a directory of 200k children pay 200k syscalls for
            # a 2000-row answer. The cap has to bound the work, not just the
            # payload.
            listing = sorted(scan, key=lambda item: _order(item.name))
            truncated = len(listing) > MAX_ENTRIES

            for item in listing[:MAX_ENTRIES]:
                try:
                    # A link whose target is outside the tree reads as `other`:
                    # the reads below would refuse it, and a row that always
                    # fails when clicked is worse than one that says it cannot
                    # be opened.
                    if item.is_symlink() and not _inside(resolved_root, Path(item.path)):
                        entries.append({"name": item.name, "type": "other"})
                        continue
                    if item.is_dir(follow_symlinks=True):
                        entries.append({"name": item.name, "type": "directory"})
                        continue
                    if item.is_file(follow_symlinks=True):
                        entries.append(
                            {
                                "name": item.name,
                                "type": "file",
                                "size": item.stat(follow_symlinks=True).st_size,
                            }
                        )
                        continue
                except OSError:
                    # A broken link or an unstattable mount: still a row in the
                    # directory, just not one that can be opened.
                    pass
                entries.append({"name": item.name, "type": "other"})
    except FileNotFoundError:
        return _failure("not-found", "That directory is gone. It may have been moved or deleted.")
    except NotADirectoryError:
        return _failure("not-directory", "That is not a directory.")
    except PermissionError:
        return _failure("unavailable", f"permission denied: {target}")
    except OSError as exc:
        return _failure("unavailable", f"cannot read {target}: {exc}")

    return {
        "ok": True,
        "absolute_path": str(target),
        "entries": entries,
        "truncated": truncated,
    }


__all__ = [
    "MAX_BYTES",
    "MAX_ENTRIES",
    "MAX_FILE_BYTES",
    "MAX_LINES",
    "list_dir",
    "read_bytes",
    "read_file",
    "read_related",
]

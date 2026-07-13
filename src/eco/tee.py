"""Raw-output recovery for eco compression (port of RTK tee.rs).

The license to be lossy: before any filter drops content, the full raw output
is written to a per-session file and the compact rendering ends with a
pointer the model can act on:

    [full output: ~/.clawcodex/<ws>/<session>/eco/1707_pytest.log]
    [see remaining: tail -n +61 ~/.clawcodex/.../eco/1707_ls.log]

The engine enforces RTK's hard rule (main.rs:1341): if the tee write fails,
the lossy rendering is *discarded* and the baseline ships instead — an
omission marker may only appear when the omitted content is actually
retrievable.

Directory: callers pass it in (the Bash tool derives it from the session's
tool-results dir, keeping eco artifacts co-located with the existing
``<persisted-output>`` files). Files over 1 MiB are truncated at a UTF-8
boundary with an explicit marker; tiny outputs (< 500 B) are not teed —
their loss is cheaper than the hint.
"""

from __future__ import annotations

import itertools
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Monotonic per-process counter: with time_ns + pid it makes tee filenames
# collision-proof even for identical commands fired in the same instant from
# parallel subagents (critic B1 — a clobbered tee file would make an
# already-emitted recovery hint point at the WRONG command's output).
_counter = itertools.count()

# Below this, recovery isn't worth a file + hint line (RTK MIN_TEE_SIZE).
MIN_TEE_SIZE = 500
# Per-file cap (RTK DEFAULT_MAX_FILE_SIZE).
MAX_TEE_FILE_SIZE = 1_048_576
# Per-directory cap — a runaway loop of huge commands shouldn't fill the disk.
MAX_TEE_FILES = 50
_SLUG_MAX = 40


def sanitize_slug(slug: str) -> str:
    """Filesystem-safe slug: alnum/_/- kept, everything else ``_``, 40 chars."""
    cleaned = "".join(
        c if (c.isascii() and (c.isalnum() or c in "_-")) else "_" for c in slug
    )
    return cleaned[:_SLUG_MAX] or "cmd"


def _truncate_utf8_boundary(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[:max_bytes]
    # Back off to a valid UTF-8 boundary (at most 3 continuation bytes).
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    if cut and cut[-1] >= 0xC0:
        cut = cut[:-1]
    return (
        cut.decode("utf-8", errors="ignore")
        + f"\n\n--- truncated at {max_bytes} bytes ---"
    )


def _rotate(directory: Path) -> None:
    try:
        files = sorted(
            (p for p in directory.iterdir() if p.suffix == ".log"),
            key=lambda p: p.name,
        )
        for old in files[: max(0, len(files) - MAX_TEE_FILES)]:
            old.unlink(missing_ok=True)
    except OSError:
        logger.debug("[eco] tee rotation failed", exc_info=True)


def tee_raw(content: str, slug: str, directory: Path) -> Path | None:
    """Write ``content`` to ``directory/{epoch}_{slug}.log``.

    Returns the path, or None when skipped (tiny content) or the write
    failed — the caller must then fall back to the baseline rendering.
    """
    if len(content.encode("utf-8", errors="ignore")) < MIN_TEE_SIZE:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # time_ns + pid + counter → unique per call, chronologically sortable
        # (rotation sorts by name), and never overwrites an earlier file whose
        # recovery hint is already in the conversation.
        name = (
            f"{time.time_ns()}_{os.getpid()}_{next(_counter)}"
            f"_{sanitize_slug(slug)}.log"
        )
        path = directory / name
        path.write_text(
            _truncate_utf8_boundary(content, MAX_TEE_FILE_SIZE), encoding="utf-8"
        )
        _rotate(directory)
        return path
    except OSError:
        logger.debug("[eco] tee write failed", exc_info=True)
        return None


def _display_path(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def full_hint(path: Path) -> str:
    """``[full output: ~/...]`` — read the file for everything."""
    return f"[full output: {_display_path(path)}]"


def tail_hint(path: Path, line_offset: int) -> str:
    """``[see remaining: tail -n +N ~/...]`` — a directly runnable pointer to
    the first hidden line (RTK force_tee_tail_hint)."""
    return f"[see remaining: tail -n +{line_offset} {_display_path(path)}]"

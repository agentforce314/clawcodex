"""Bracketed-paste classification helpers.

Ports the contract carved out by ``typescript/src/hooks/usePasteHandler.ts``
and the ``isPasted`` discriminator on ``ParsedKey``
(``typescript/src/ink/parse-keypress.ts``). Chapter 14 of
``claude-code-from-source`` calls this flag "critical for security": when
the terminal wraps content between ``ESC [200~`` and ``ESC [201~``, the
parser must keep the bytes inside that envelope from being interpreted
as commands (a paste containing ``\x03`` would otherwise be a free
Ctrl+C, and a paste of ``gg`` in vim mode would fire ``transcript.top``
instead of inserting the literal characters).

In Textual the demarcation is already done for us — the runtime emits a
single ``textual.events.Paste`` carrying the concatenated text. This
module is the pure-Python classification layer that the ``PromptInput``
widget consults to decide:

* whether the paste is empty (macOS Cmd+V image paste sentinel),
* whether it looks like a drag of an image file (so the host can offer
  to attach it instead of inserting the path string), and
* how many lines and characters were pasted (for the "Pasted N chars"
  hint surface that round 3 will wire to the footer).

No Textual import here — the helpers are unit-testable in isolation and
shared by both the prompt widget and any future paste-aware surface
(transcript search, message-selector inline editor).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Mirrors ``usePasteHandler.PASTE_THRESHOLD`` (32 chars in the ink
# reference, raised to 64 here because Textual already framed the event
# as a Paste — the heuristic is only a fallback for terminals without
# bracketed paste support).
PASTE_THRESHOLD: int = 64

# Image file extensions the host can plausibly attach.  Lowercase only;
# ``detect_image_drag`` lowercases candidates before lookup. The set
# matches the recognised media types in
# ``typescript/src/utils/file/isImageFilePath.ts`` plus ``.heic``/``.heif``
# (which iPhone screenshots use natively).
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".svg",
        ".avif",
    }
)

# Split on whitespace that precedes the start of an absolute path. Two
# forms cover macOS/Linux (``/foo``) and Windows (``C:\foo``). The TS
# reference uses the same regex in ``usePasteHandler.wrappedOnInput``.
_PATH_SPLIT_RE = re.compile(r"\s+(?=/|[A-Za-z]:\\)")


@dataclass(frozen=True)
class PasteInfo:
    """Result of classifying a single bracketed-paste payload.

    Attributes:
        text: The literal pasted text (unmodified — escape sequences
            and control characters survive intact).
        length: ``len(text)``. Cached so consumers do not call
            ``len()`` repeatedly on multi-megabyte pastes.
        is_empty: ``True`` when ``text`` is empty. macOS sends an empty
            bracketed-paste envelope when the user Cmd+V's an image
            from the clipboard; host code interprets this as a
            "check clipboard for image" trigger.
        is_image_drag: ``True`` when the paste looks like one or more
            drag-and-drop image file paths.
        line_count: Number of newline-delimited lines (``1`` for a paste
            that contains no ``\n``). Used by the host to surface a
            "Pasted N lines" hint.
    """

    text: str
    length: int
    is_empty: bool
    is_image_drag: bool
    line_count: int


def detect_image_drag(text: str) -> bool:
    """Return ``True`` if ``text`` looks like a drag of one or more image files.

    The heuristic intentionally matches the TS reference so behaviour is
    consistent across the two ports:

    1. Strip leading/trailing whitespace.
    2. Split the payload on whitespace that precedes ``/`` or ``X:\\``
       (the start of an absolute path on Unix/Windows). This catches
       "two files dropped at once" pastes (`"/a/foo.png /b/bar.jpg"`).
    3. Further split each token on newlines (some terminals deliver
       multi-file drops as one path per line).
    4. Strip each candidate and check the extension lowercase against
       :data:`IMAGE_EXTENSIONS`.

    Returns ``True`` if *any* candidate matches, ``False`` otherwise.
    A pure-text paste, a URL, or a code snippet will never return
    ``True`` because none of those have a known image extension at the
    end of an absolute-looking path.
    """

    stripped = text.strip()
    if not stripped:
        return False
    for token in _PATH_SPLIT_RE.split(stripped):
        for raw_line in token.split("\n"):
            candidate = raw_line.strip()
            # Only treat candidates that look absolute. Avoid false-
            # positives on plain words by requiring either a leading
            # ``/`` or a drive-letter prefix on Windows.
            if not candidate or not (
                candidate.startswith("/") or _looks_like_windows_path(candidate)
            ):
                continue
            dot = candidate.rfind(".")
            if dot != -1 and candidate[dot:].lower() in IMAGE_EXTENSIONS:
                return True
    return False


def _looks_like_windows_path(candidate: str) -> bool:
    """Return ``True`` if ``candidate`` starts with a drive letter prefix."""

    return (
        len(candidate) >= 3
        and candidate[0].isalpha()
        and candidate[1] == ":"
        and candidate[2] == "\\"
    )


def classify_paste(text: str) -> PasteInfo:
    """Return a :class:`PasteInfo` describing the bracketed-paste payload.

    The function is total — every string produces a well-formed
    :class:`PasteInfo`, including the empty string (which yields
    ``is_empty=True``). Callers should branch on ``is_empty`` before
    trying to insert ``text`` into a widget; an empty paste means the
    host should query the clipboard for an image instead of writing a
    zero-length string into the input buffer.
    """

    length = len(text)
    is_empty = length == 0
    is_image_drag = False if is_empty else detect_image_drag(text)
    # ``str.count`` is O(n) but a single pass per paste is cheap even
    # for multi-megabyte payloads. Add 1 for the trailing line that
    # never ends with ``\n``.
    line_count = 1 + text.count("\n") if not is_empty else 0
    return PasteInfo(
        text=text,
        length=length,
        is_empty=is_empty,
        is_image_drag=is_image_drag,
        line_count=line_count,
    )


__all__ = [
    "IMAGE_EXTENSIONS",
    "PASTE_THRESHOLD",
    "PasteInfo",
    "classify_paste",
    "detect_image_drag",
]

"""Clipboard / dropped-path image ingestion.

Port of ``typescript/src/utils/imagePaste.ts``. Reads an image out of the system
clipboard (a screenshot, a Cmd+C'd image) or off a path the user dragged into
the terminal, then runs it through the existing Pillow pipeline in
``src/utils/image_processor.py`` so it lands inside the API's size and dimension
envelope.

Deliberately NOT ported:

* The native ``image-processor-napi`` NSPasteboard fast path (~5ms vs ~1.5s for
  osascript) and its GrowthBook kill switch. There is no such native module
  here, and the TS code treats it purely as an optimization with the osascript
  path as its documented fallback -- so the fallback is the whole port.
* ``IMAGE_EXTENSION_REGEX``'s coupling to ``BriefTool/upload.ts``'s MIME table
  (a remote-viewer thumbnail concern with no clawcodex equivalent).

Shell safety: no caller-supplied text is ever interpolated into a shell string.
The darwin and win32 commands run argv-style with no shell at all. Only the
Linux readers need a shell (they are ``||`` fallback chains across
xclip/wl-paste), and the single value interpolated there is the temp path this
module chose itself.
"""

from __future__ import annotations

import base64 as _base64
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .image_processor import (
    ImageDimensions,
    ImageProcessingError,
    detect_image_format_from_buffer,
    estimate_image_tokens_from_base64_length,
    maybe_resize_image,
)

logger = logging.getLogger(__name__)

#: Characters of pasted text above which the original treats a paste as "large"
#: (imagePaste.ts PASTE_THRESHOLD). Exported for parity; the composer owns the
#: policy.
PASTE_THRESHOLD = 800

#: Bounded so a hung/absent clipboard helper cannot wedge the turn. osascript
#: on a large clipboard image is ~1.5s in the original's own measurement, so
#: this leaves generous headroom without being unbounded.
CLIPBOARD_TIMEOUT_S = 20.0

LINUX_CLIPBOARD_IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
)

#: Kept in sync with ``IMAGE_MIME_TYPES`` in ``src/tool_system/tools/read.py``.
#: ``bmp`` is accepted for INPUT (screenshot tools and WSL2 hand us BMP) but is
#: always converted to PNG before it reaches the API, which rejects image/bmp.
IMAGE_EXTENSION_RE = re.compile(r"\.(png|jpe?g|gif|webp|bmp)$", re.IGNORECASE)

_SCREENSHOT_FILENAME = "clawcodex_cli_latest_screenshot.png"

_WIN32_HAS_IMAGE_CMD = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "[System.Windows.Forms.Clipboard]::ContainsImage()"
)


@dataclass(frozen=True)
class PastedImage:
    """An image ready for the wire."""

    base64: str
    media_type: str
    dimensions: ImageDimensions | None = None
    #: Set only when the image came from a path (clipboard images have none).
    source_path: str | None = None

    @property
    def token_estimate(self) -> int:
        return estimate_image_tokens_from_base64_length(len(self.base64))


def _screenshot_path() -> Path:
    """A fresh, private, unpredictable path for the clipboard hand-off.

    The reference uses one fixed filename in the shared temp dir. On Linux that
    is a symlink-follow write primitive (the save command is a ``>`` redirect),
    and a predictable name in a world-writable directory is CWE-377/59. It also
    put an environment-derived value inside an AppleScript string literal and a
    PowerShell single-quoted string, where a quote character would break out.

    ``mkdtemp`` closes all of that at once: 0700, unguessable, and quote-free
    because we generate it.
    """
    base = os.environ.get("CLAWCODEX_TMPDIR") or None
    directory = tempfile.mkdtemp(prefix="clawcodex-clip-", dir=base)
    return Path(directory) / _SCREENSHOT_FILENAME


def build_linux_clipboard_check_command() -> str:
    pattern = "|".join(m.replace("/", r"\/") for m in LINUX_CLIPBOARD_IMAGE_MIME_TYPES)
    return (
        f'xclip -selection clipboard -t TARGETS -o 2>/dev/null | grep -E "{pattern}"'
        f' || wl-paste -l 2>/dev/null | grep -E "{pattern}"'
    )


def build_linux_clipboard_save_command(screenshot_path: str) -> str:
    parts: list[str] = []
    for mime in LINUX_CLIPBOARD_IMAGE_MIME_TYPES:
        parts.append(
            f'xclip -selection clipboard -t {mime} -o > "{screenshot_path}" 2>/dev/null'
        )
        parts.append(f'wl-paste --type {mime} > "{screenshot_path}" 2>/dev/null')
    return " || ".join(parts)


def _run(argv: list[str], *, shell: bool = False) -> subprocess.CompletedProcess | None:
    """Run a clipboard helper, returning None if it is missing or times out."""
    try:
        return subprocess.run(  # noqa: S603 — argv is module-owned, see module docstring
            argv if not shell else argv[0],
            shell=shell,
            capture_output=True,
            timeout=CLIPBOARD_TIMEOUT_S,
        )
    except FileNotFoundError:
        # No osascript / xclip / powershell on this box.
        return None
    except subprocess.TimeoutExpired:
        logger.debug("clipboard helper timed out: %s", argv[:1], exc_info=True)
        return None
    except Exception:  # noqa: BLE001 — a clipboard probe must never kill a turn
        logger.debug("clipboard helper failed: %s", argv[:1], exc_info=True)
        return None


def has_image_in_clipboard() -> bool:
    """True when the clipboard holds an image.

    NOT cheap, despite reading like a probe: on darwin the coercion
    ``the clipboard as «class PNGf»`` prints the entire image as hex, which is
    most of the ~1.5 s that the full read costs. Do NOT wire this into a
    keystroke handler as a pre-check — it buys nothing over just calling
    :func:`get_image_from_clipboard` and treating ``None`` as "no image", which
    is what the paste path does.

    Kept because it is part of the reference's API surface
    (``hasImageInClipboard``) and a caller may yet want it off the hot path.
    """
    if sys.platform == "win32":
        result = _run(["powershell", "-NoProfile", "-Command", _WIN32_HAS_IMAGE_CMD])
        return bool(
            result
            and result.returncode == 0
            and result.stdout.decode("utf-8", "replace").strip() == "True"
        )
    if sys.platform == "darwin":
        result = _run(["osascript", "-e", "the clipboard as «class PNGf»"])
        return bool(result and result.returncode == 0)
    result = _run([build_linux_clipboard_check_command()], shell=True)
    return bool(result and result.returncode == 0 and result.stdout.strip())


def _read_clipboard_bytes() -> bytes | None:
    """Platform clipboard -> raw image bytes, or None."""
    path = _screenshot_path()
    target = str(path)
    if sys.platform == "darwin":
        # argv form, no shell: the path lands inside an AppleScript string
        # literal passed as its own argument.
        saved = _run([
            "osascript",
            "-e", "set png_data to (the clipboard as «class PNGf»)",
            "-e", f'set fp to open for access POSIX file "{target}" with write permission',
            "-e", "write png_data to fp",
            "-e", "close access fp",
        ])
    elif sys.platform == "win32":
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
            "if (-not $img) { exit 1 }; "
            f"$img.Save('{_escape_powershell_single_quoted(target)}',"
            " [System.Drawing.Imaging.ImageFormat]::Png)"
        )
        saved = _run(["powershell", "-NoProfile", "-Command", script])
    else:
        saved = _run([build_linux_clipboard_save_command(target)], shell=True)
    if saved is None or saved.returncode != 0:
        _cleanup(path)
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    finally:
        _cleanup(path)
    return data or None


def _cleanup(path: Path) -> None:
    """Remove the hand-off file AND the private directory mkdtemp made for it."""
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:  # pragma: no cover — best effort
        logger.debug("could not remove %s", path, exc_info=True)


def _escape_powershell_single_quoted(value: str) -> str:
    """Double every ``'`` for a PowerShell single-quoted string.

    Ported from the reference's ``escapePowerShellSingleQuotedString``. Not
    theoretical: ``%TEMP%`` for a user named ``O'Brien`` is
    ``C:\\Users\\O'Brien\\AppData\\Local\\Temp``, and an unescaped apostrophe
    there is a parse error, so clipboard paste would fail for that user with no
    diagnostic.
    """
    return value.replace("'", "''")


#: The only four the API accepts. Anything else must be re-encoded, not relabeled.
#: BMP needs no special case: it simply fails the magic sniff below and takes the
#: same re-encode path as any other non-API container.
_API_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

#: PNG/JPEG/GIF/WebP magic. Deliberately narrower than
#: ``detect_image_format_from_buffer``, which DEFAULTS to ``image/png`` on
#: unrecognized bytes -- fine for its callers, fatal here: a TIFF written to
#: ``x.png`` would be shipped labeled ``image/png`` and 400 at the API.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_api_media_type(buf: bytes) -> str | None:
    """Media type from magic bytes, or None when it is not an API format."""
    for magic, media_type in _MAGIC:
        if buf.startswith(magic):
            return media_type
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    return None


def _as_media_type(hint: str | None) -> str:
    """Normalize a format hint to a media type.

    ``maybe_resize_image`` uses ``format_hint`` as the media-type FALLBACK when
    Pillow's own ``img.format`` isn't in its map (``_pil_format_to_media_type``),
    and then hands that value straight to the encoder. A bare extension like
    ``"png"`` therefore reaches ``_encode_image`` and raises
    "Unsupported image encoding type: png" -- which silently dropped every BMP
    over the size/dimension caps, i.e. exactly the full-screen Windows/WSL2
    screenshot BMP exists to handle. Every other caller in the repo passes a
    media type; so must this one.
    """
    if not hint:
        return "image/png"
    lowered = hint.strip().lower()
    if lowered.startswith("image/"):
        return lowered if lowered in _API_MEDIA_TYPES else "image/png"
    ext = lowered.lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext in ("png", "gif", "webp"):
        return f"image/{ext}"
    # bmp, tif, heic, anything unknown: re-encoded to PNG below.
    return "image/png"


def _reencode_png(buf: bytes) -> bytes | None:
    """Re-encode any Pillow-decodable image as PNG.

    Keeps the source's alpha rather than forcing RGBA on everything: adding an
    alpha channel to a BMP or TIFF that never had one just inflates the PNG.
    """
    try:
        import io

        from PIL import Image  # noqa: PLC0415 — lazy, Pillow is a heavy import

        img = Image.open(io.BytesIO(buf))
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        out = io.BytesIO()
        img.convert("RGBA" if has_alpha else "RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        logger.debug("PNG re-encode failed", exc_info=True)
        return None


def _to_pasted_image(
    buf: bytes,
    *,
    format_hint: str | None = None,
    source_path: str | None = None,
) -> PastedImage | None:
    """Normalize raw bytes into an API-acceptable image.

    Anything whose magic bytes are not one of the four API formats is re-encoded
    as PNG, then everything goes through the shared resize/downsample envelope.
    BMP is not a special case, just the most common instance of that rule
    (Windows/WSL2 hand us BMP for a plain screenshot paste, and the API rejects
    ``image/bmp``).
    """
    if not buf:
        return None

    # Convert to PNG BEFORE the resize, matching the reference (imagePaste.ts
    # runs sharp(...).png() ahead of maybeResizeAndDownsampleImageBuffer). Doing
    # it after would leave the re-encoded bytes unchecked against
    # IMAGE_TARGET_RAW_SIZE -- a photographic TIFF already inside the dimension
    # cap can still re-encode past the byte cap and 400 at the API.
    #
    # Magic bytes are authoritative here, not the file extension or the
    # container's self-report: detect_image_format_from_buffer DEFAULTS to
    # image/png on unknown magic, which would ship a TIFF-in-a-.png as PNG.
    if _sniff_api_media_type(buf) is None:
        converted = _reencode_png(buf)
        if converted is None:
            logger.debug("pasted image is not an API-supported format")
            return None
        buf = converted

    # A media type, never a bare extension -- see _as_media_type.
    hint = _as_media_type(format_hint)
    try:
        resized = maybe_resize_image(buf, len(buf), hint)
        data, dimensions = resized.data, resized.dimensions
    except ImageProcessingError:
        logger.debug("could not decode pasted image", exc_info=True)
        return None

    # Re-sniff: the resize may have re-encoded (e.g. degraded PNG -> JPEG to fit
    # the byte budget), so the pre-resize type is not necessarily still true.
    media_type = _sniff_api_media_type(data)
    if media_type is None:
        logger.debug("resized image is not an API-supported format")
        return None

    return PastedImage(
        base64=_base64.b64encode(data).decode("ascii"),
        media_type=media_type,
        dimensions=dimensions,
        source_path=source_path,
    )


def get_image_from_clipboard() -> PastedImage | None:
    """Read an image out of the system clipboard, resized for the API."""
    buf = _read_clipboard_bytes()
    if buf is None:
        return None
    return _to_pasted_image(buf, format_hint="png")


def get_image_path_from_clipboard() -> str | None:
    """Clipboard as a file path (Finder/Explorer copy of an image file)."""
    if sys.platform == "darwin":
        result = _run([
            "osascript", "-e", "get POSIX path of (the clipboard as «class furl»)"
        ])
    elif sys.platform == "win32":
        result = _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])
    else:
        result = _run([
            "xclip -selection clipboard -t text/plain -o 2>/dev/null || wl-paste 2>/dev/null"
        ], shell=True)
    if result is None or result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    return text or None


def _from_file_uri(text: str) -> str:
    """``file:///a/b%20c.png`` -> ``/a/b c.png``; anything else unchanged.

    Load-bearing, not cosmetic: the client gates a paste with its own
    ``looksLikeDroppedPath`` (useComposerState.ts), which deliberately accepts
    ``file://`` URIs because that is what macOS puts on the clipboard when you
    drag a screenshot out of the Finder or the screenshot HUD. Without decoding
    here, every one of those pastes arrives percent-encoded, misses on disk, and
    silently degrades to pasting the URI as text.

    Only ``file://`` is decoded. An ``http(s)://`` URL is not a local file and
    must not be unquoted into something that looks like one.
    """
    if not text.lower().startswith("file://"):
        return text
    from urllib.parse import unquote, urlparse

    parsed = urlparse(text)
    # A UNC-style host ("file://server/share") is not a local path; only the
    # empty and "localhost" authorities name this machine.
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        return text
    path = unquote(parsed.path)
    # ``file:///C:/Users/a/b.png`` parses to ``/C:/Users/a/b.png``, which
    # ``PureWindowsPath`` reads as RELATIVE (no drive), so it would get joined
    # onto the session cwd as ``D:\C:\Users\...``. Strip the leading slash that
    # only exists to satisfy the URI grammar.
    if path.startswith("/") and _DRIVE_RE.match(path[1:]):
        path = path[1:]
    return path or text


def _remove_outer_quotes(text: str) -> str:
    if len(text) >= 2 and (
        (text.startswith('"') and text.endswith('"'))
        or (text.startswith("'") and text.endswith("'"))
    ):
        return text[1:-1]
    return text


def _strip_backslash_escapes(path: str) -> str:
    r"""Undo shell escaping a terminal adds to a dragged-in path.

    ``name\ \(15\).png`` -> ``name (15).png``. Double backslashes are real
    backslashes in the filename, so they are parked behind a random-salted
    placeholder first -- a fixed sentinel would let a filename containing the
    sentinel steer the result (the injection the TS port calls out).
    """
    if sys.platform == "win32":
        # Backslashes ARE the separator here.
        return path
    salt = _base64.b16encode(os.urandom(8)).decode("ascii")
    placeholder = f"__DOUBLE_BACKSLASH_{salt}__"
    parked = path.replace("\\\\", placeholder)
    unescaped = re.sub(r"\\(.)", r"\1", parked)
    return unescaped.replace(placeholder, "\\")


def _normalize_dropped(text: str) -> str:
    """Quotes off, ``file://`` decoded, shell escapes undone -- in that order.

    Order matters: the URI decode has to see the bare value (quotes stripped),
    and escape-stripping has to run last or it would eat backslashes that
    percent-decoding just produced on Windows paths.
    """
    return _strip_backslash_escapes(_from_file_uri(_remove_outer_quotes(text.strip())))


#: A scheme-bearing URL that is NOT ``file:`` names something remote. Matched
#: before the extension test so ``https://example.com/logo.png`` is declined
#: rather than treated as a local path -- otherwise it reports a read error
#: instead of falling through to an ordinary text paste, which is what pasting a
#: link should do. Mirrors the client's ``looksLikeDroppedPath`` rejecting
#: non-``file://`` URLs (useComposerState.ts).
_REMOTE_URL_RE = re.compile(r"^(?!file:)[a-zA-Z][a-zA-Z0-9+.\-]*://")


def as_image_file_path(text: str) -> str | None:
    """Normalize ``text`` to a local image path, or None if it isn't one."""
    cleaned = _normalize_dropped(text)
    if _REMOTE_URL_RE.match(cleaned):
        return None
    # A surviving ``file://`` prefix means ``_from_file_uri`` DECLINED to decode
    # it -- i.e. a UNC-style ``file://host/share`` naming another machine. Not a
    # path here, so decline rather than hand the raw URI on as one.
    if cleaned.lower().startswith("file://"):
        return None
    return cleaned if IMAGE_EXTENSION_RE.search(cleaned) else None


def is_image_file_path(text: str) -> bool:
    return as_image_file_path(text) is not None


def try_read_image_from_path(text: str, *, cwd: Path | None = None) -> PastedImage | None:
    """Read an image the user dragged/pasted as a path.

    A relative path is resolved against ``cwd`` first, then against the
    clipboard's own path by basename -- VS Code's terminal pastes only the
    filename on Cmd+V, so the clipboard is the only place the directory
    survives (TS tryReadImageFromPath).
    """
    cleaned = as_image_file_path(text)
    if not cleaned:
        return None

    candidate = Path(cleaned).expanduser()
    buf: bytes | None = None
    resolved: str | None = None

    try:
        if candidate.is_absolute() and candidate.is_file():
            buf, resolved = candidate.read_bytes(), str(candidate)
        else:
            local = ((cwd or Path.cwd()) / candidate).resolve()
            if local.is_file():
                buf, resolved = local.read_bytes(), str(local)
            elif " " not in cleaned:
                # Bare filename with no directory: VS Code's terminal pastes only
                # the name on Cmd+V, so the clipboard is the only place the
                # directory survives. Gated on "no spaces" so a prose string that
                # happens to end in ".png" ("what is wrong with logo.png") cannot
                # trigger a clipboard shell-out.
                clip = get_image_path_from_clipboard()
                if clip and candidate.name == Path(clip).name:
                    clip_path = Path(clip)
                    if clip_path.is_file():
                        buf, resolved = clip_path.read_bytes(), str(clip_path)
    except OSError:
        logger.debug("could not read pasted image path", exc_info=True)
        return None

    if not buf:
        if resolved is not None:
            logger.warning("pasted image file is empty: %s", resolved)
        return None

    ext = candidate.suffix.lstrip(".").lower() or "png"
    return _to_pasted_image(buf, format_hint=ext, source_path=resolved)


def dimensions_to_wire(dims: ImageDimensions | None) -> dict[str, int]:
    """Flatten dimensions into the ``width``/``height`` the client renders.

    The client shows the size it will actually send, i.e. the DISPLAY size after
    downsampling, falling back to the original when no resize happened.
    """
    if dims is None:
        return {}
    width = dims.display_width or dims.original_width
    height = dims.display_height or dims.original_height
    out: dict[str, int] = {}
    if width:
        out["width"] = int(width)
    if height:
        out["height"] = int(height)
    return out


#: Windows drive prefix, optionally quoted: ``C:\`` / ``"C:/``.
_DRIVE_RE = re.compile(r"^[\"']?[A-Za-z]:[/\\]")


def looks_like_dropped_path(text: str) -> bool:
    """Does this look like a dragged-in path rather than prose?

    A FAITHFUL port of ``looksLikeDroppedPath`` in
    ``ui-tui/src/app/useComposerState.ts``. The two must agree: the client uses
    it to decide whether to consult the server, and ``useSubmission.ts`` runs
    ``input.detect_drop`` on submitted prompts, so a loose predicate here
    rewrites ordinary prompts.

    That is not hypothetical. The first version of this function only rejected
    tabs and NULs, so ``hello world``, ``fix the bug in main.py`` and
    ``README.md`` all returned True -- and ``README.md`` resolves to a real file,
    so the prompt would have been silently replaced with ``@/abs/path/README.md``.

    The rule is a PREFIX test, deliberately: a path is recognized by how it
    starts, not by containing a dot somewhere. Bare relative names
    (``README.md``) are NOT paths here -- too many prompts look like one.
    """
    stripped = text.strip()
    if not stripped or "\n" in stripped:
        return False
    # Explicit path forms: URI, home-relative, dot-relative, quoted absolute,
    # Windows drive.
    if stripped.startswith((
        "file://", "~/", "./", "../", '"/', "'/", '"~', "'~",
    )):
        return True
    if _DRIVE_RE.match(stripped):
        return True
    # A bare absolute path needs a second separator or a dot, so that "/help"
    # and "/model sonnet" stay slash-commands rather than path probes.
    if stripped.startswith("/"):
        rest = stripped[1:]
        return "/" in rest or "." in rest
    return False


def resolve_dropped_file(text: str, *, cwd: Path | None = None) -> Path | None:
    """Resolve a dropped path to an existing file, image or not."""
    stripped = _normalize_dropped(text)
    if not stripped:
        return None
    candidate = Path(stripped).expanduser()
    try:
        if candidate.is_absolute():
            return candidate if candidate.is_file() else None
        local = ((cwd or Path.cwd()) / candidate).resolve()
        return local if local.is_file() else None
    except OSError:  # pragma: no cover — exotic path errors
        return None


def clipboard_tooling_available() -> bool:
    """Whether this platform has the helper binaries the readers need."""
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    if sys.platform == "win32":
        return shutil.which("powershell") is not None
    return shutil.which("xclip") is not None or shutil.which("wl-paste") is not None

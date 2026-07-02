from __future__ import annotations

import base64 as _base64
import json as _json
import os
from pathlib import Path
from typing import Any

from ..build_tool import SearchOrReadResult, Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..utils.path_utils import suggest_path_under_cwd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _backfill_read_edit_path(tool_input: dict[str, Any]) -> None:
    """ch06 round-4 PR-A GAP B — expand ``file_path`` in-place so hook
    allowlists / permission rules can't be bypassed via ``~`` or a
    relative path (TS FileReadTool.ts:388 / FileEditTool.ts:115, both
    overwrite-only). Shared by Read and Edit."""
    fp = tool_input.get("file_path")
    if isinstance(fp, str):
        tool_input["file_path"] = str(Path(fp).expanduser().resolve())

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier Read "
    "tool_result in this conversation is still current \u2014 refer to that "
    "instead of re-reading."
)

MAX_LINES_TO_READ = 2000

# Default file-size cap (bytes) checked *before* reading content.
# Matches TS MAX_OUTPUT_SIZE = 0.25 MB.
DEFAULT_MAX_SIZE_BYTES = 256 * 1024

# Default max output tokens (rough estimate).  Env var override:
# CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS
DEFAULT_MAX_OUTPUT_TOKENS = 25_000

FILE_NOT_FOUND_CWD_NOTE = "Note: your current working directory is"

# ---------------------------------------------------------------------------
# Blocked device paths (ported from TS FileReadTool.ts lines 97-128)
# ---------------------------------------------------------------------------

BLOCKED_DEVICE_PATHS = frozenset([
    # Infinite output -- never reach EOF
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
    # Blocks waiting for input
    "/dev/stdin",
    "/dev/tty",
    "/dev/console",
    # Nonsensical to read
    "/dev/stdout",
    "/dev/stderr",
    # fd aliases for stdin/stdout/stderr
    "/dev/fd/0",
    "/dev/fd/1",
    "/dev/fd/2",
])


def _is_blocked_device_path(file_path: str) -> bool:
    """Check if a path is a blocked device that would hang or produce infinite output."""
    if file_path in BLOCKED_DEVICE_PATHS:
        return True
    # /proc/self/fd/0-2 and /proc/<pid>/fd/0-2 are Linux aliases for stdio
    if file_path.startswith("/proc/") and (
        file_path.endswith("/fd/0")
        or file_path.endswith("/fd/1")
        or file_path.endswith("/fd/2")
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Binary extension blocking (ported from TS constants/files.ts)
# ---------------------------------------------------------------------------

BINARY_EXTENSIONS = frozenset([
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
    # Videos
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".m4v", ".mpeg", ".mpg",
    # Audio
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".aiff", ".opus",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz", ".z", ".tgz", ".iso",
    # Executables/binaries
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj", ".lib",
    ".app", ".msi", ".deb", ".rpm",
    # Documents (PDF is excluded at the call site -- the tool renders PDFs natively)
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Bytecode / VM artifacts
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear", ".node", ".wasm", ".rlib",
    # Database files
    ".sqlite", ".sqlite3", ".db", ".mdb", ".idx",
    # Design / 3D
    ".psd", ".ai", ".eps", ".sketch", ".fig", ".xd", ".blend", ".3ds", ".max",
    # Flash
    ".swf", ".fla",
    # Lock/profiling data
    ".lockb", ".dat", ".data",
])

# Extensions that are in BINARY_EXTENSIONS but are handled natively by this tool.
_BINARY_EXTENSION_EXEMPTIONS = frozenset([".pdf", ".svg"])

# Image extensions this tool can render natively.
IMAGE_EXTENSIONS = frozenset(["png", "jpg", "jpeg", "gif", "webp"])

# Map image extension -> API media_type. jpg/jpeg both → image/jpeg.
IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

def _has_blocked_binary_extension(file_path: str) -> bool:
    """Return True if file_path has a known binary extension that this tool cannot read.

    PDF, images, and SVG are excluded -- the tool renders those natively.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _BINARY_EXTENSION_EXEMPTIONS:
        return False
    if ext.lstrip(".") in IMAGE_EXTENSIONS:
        return False
    return ext in BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# File size / token limits
# ---------------------------------------------------------------------------

def _get_max_size_bytes() -> int:
    """Return the max file size in bytes (pre-read check).

    Respects CLAUDE_CODE_FILE_READ_MAX_SIZE_BYTES env var override.
    """
    override = os.environ.get("CLAUDE_CODE_FILE_READ_MAX_SIZE_BYTES")
    if override:
        try:
            val = int(override)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_SIZE_BYTES


def _get_max_output_tokens() -> int:
    """Return max output tokens. Respects CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS."""
    override = os.environ.get("CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS")
    if override:
        try:
            val = int(override)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_OUTPUT_TOKENS


def _rough_token_estimate(text: str) -> int:
    """Quick token count estimate: ~4 characters per token for English text."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# File-not-found suggestion helpers
# ---------------------------------------------------------------------------

def _find_similar_file(file_path: str) -> str | None:
    """Find a file with the same base name but different extension in the same directory."""
    try:
        dir_path = os.path.dirname(file_path)
        if not dir_path or not os.path.isdir(dir_path):
            return None
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        if not base_name:
            return None
        for entry in os.listdir(dir_path):
            entry_base = os.path.splitext(entry)[0]
            if entry_base == base_name and entry != os.path.basename(file_path):
                return os.path.join(dir_path, entry)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Prompt (ported from TS prompt.ts)
# ---------------------------------------------------------------------------

def _render_prompt() -> str:
    return f"""Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to {MAX_LINES_TO_READ} lines starting from the beginning of the file
- When you already know which part of the file you need, only read that part. This can be important for larger files.
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.
- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""


# ---------------------------------------------------------------------------
# PDF pages parameter parsing (TS pdfUtils.ts parsePDFPageRange equivalent)
# ---------------------------------------------------------------------------

PDF_MAX_PAGES_PER_READ = 20


def _parse_pdf_pages(pages: str) -> tuple[int | None, int | None]:
    """Parse the ``pages`` parameter (e.g. ``"1-5"``, ``"3"``, ``"10-20"``).

    Returns ``(first_page, last_page)``. Raises ToolInputError on malformed
    input or a range exceeding ``PDF_MAX_PAGES_PER_READ``.
    """
    if not isinstance(pages, str) or not pages.strip():
        raise ToolInputError(
            f'Invalid pages parameter: "{pages}". Use formats like "1-5", "3", or "10-20".'
        )
    s = pages.strip()
    try:
        if "-" in s:
            first_str, last_str = s.split("-", 1)
            first, last = int(first_str), int(last_str)
        else:
            first = last = int(s)
    except ValueError as e:
        raise ToolInputError(
            f'Invalid pages parameter: "{pages}". Use formats like "1-5", "3", or "10-20".'
        ) from e
    if first < 1 or last < first:
        raise ToolInputError(
            f'Invalid pages range "{pages}": pages are 1-indexed and last must be >= first.'
        )
    if last - first + 1 > PDF_MAX_PAGES_PER_READ:
        raise ToolInputError(
            f'Page range "{pages}" exceeds maximum of {PDF_MAX_PAGES_PER_READ} '
            f"pages per request. Please use a smaller range."
        )
    return first, last


# ---------------------------------------------------------------------------
# Range-aware dedup helpers
# ---------------------------------------------------------------------------

def _get_dedup_fingerprint(fp_entry: tuple[int, ...] | tuple[int, int, bool]) -> tuple[int, int]:
    """Extract (mtime, size) from a fingerprint entry regardless of tuple length."""
    return (fp_entry[0], fp_entry[1])


def _is_partial_read(fp_entry: tuple[int, ...] | tuple[int, int, bool]) -> bool:
    """Check the partial flag from a fingerprint entry."""
    if len(fp_entry) >= 3:
        return bool(fp_entry[2])
    return False


# ---------------------------------------------------------------------------
# Core call implementation
# ---------------------------------------------------------------------------

def _read_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    file_path = tool_input["file_path"]
    if not isinstance(file_path, str) or not file_path:
        raise ToolInputError("file_path must be a non-empty string")

    # --- Blocked device paths (pre-IO check) ---
    if _is_blocked_device_path(file_path):
        raise ToolInputError(
            f"Cannot read '{file_path}': this device file would block or produce infinite output."
        )

    # --- Binary extension blocking (string check only, no IO) ---
    if _has_blocked_binary_extension(file_path):
        ext = os.path.splitext(file_path)[1].lower()
        raise ToolInputError(
            f"This tool cannot read binary files. The file appears to be a binary "
            f"{ext} file. Please use appropriate tools for binary file analysis."
        )

    path = context.ensure_readable_path(file_path)

    # --- File not found: provide helpful suggestions ---
    if not path.exists():
        cwd = context.cwd or context.workspace_root
        cwd_suggestion = suggest_path_under_cwd(str(path), str(cwd))
        similar = _find_similar_file(str(path))
        message = f"File does not exist: {path}. {FILE_NOT_FOUND_CWD_NOTE} {cwd}."
        if cwd_suggestion:
            message += f" Did you mean {cwd_suggestion}?"
        elif similar:
            message += f" Did you mean {similar}?"
        raise ToolInputError(message)

    if not path.is_file():
        raise ToolInputError(f"path is not a file: {path}")

    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    if offset is not None and (not isinstance(offset, int) or offset < 1):
        raise ToolInputError("offset must be a positive integer when provided")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ToolInputError("limit must be a positive integer when provided")

    resolved = path.resolve()
    stat = resolved.stat()

    # --- Range-aware dedup ---
    # Only dedup when the same file was previously read with the same range
    # and the file hasn't changed on disk. Partial reads are never deduped
    # to avoid incorrectly claiming the full content is unchanged.
    current_fp = (int(stat.st_mtime), int(stat.st_size))
    prev_fp = context.read_file_fingerprints.get(resolved)
    if prev_fp is not None:
        prev_mtime_size = _get_dedup_fingerprint(prev_fp)
        prev_partial = _is_partial_read(prev_fp)
        if prev_mtime_size == current_fp and not prev_partial:
            return ToolResult(
                name="Read",
                output={
                    "type": "file_unchanged",
                    "file": {"filePath": str(path)},
                },
            )

    suffix = path.suffix.lower()

    # --- Notebook ---
    if suffix == ".ipynb":
        # Check notebook size before parsing
        max_size = _get_max_size_bytes()
        if stat.st_size > max_size:
            raise ToolInputError(
                f"Notebook file ({stat.st_size:,} bytes) exceeds maximum allowed size "
                f"({max_size:,} bytes). Use Bash with jq to read specific portions:\n"
                f'  cat "{file_path}" | jq \'.cells[:20]\'  # First 20 cells\n'
                f'  cat "{file_path}" | jq \'.cells[100:120]\'  # Cells 100-120\n'
                f'  cat "{file_path}" | jq \'.cells | length\'  # Count total cells\n'
                f'  cat "{file_path}" | jq \'.cells[] | select(.cell_type=="code") | .source\'  # All code sources'
            )
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            nb = _json.loads(raw)
        except _json.JSONDecodeError:
            raise ToolInputError(f"invalid notebook JSON: {path}")
        cells = nb.get("cells", [])
        context.mark_file_read(path)
        return ToolResult(
            name="Read",
            output={
                "type": "notebook",
                "file": {"filePath": str(path), "cells": cells},
            },
        )

    # --- PDF ---
    if suffix == ".pdf":
        pages_param = tool_input.get("pages")
        if pages_param:
            # User asked for specific pages -> extract them via poppler and
            # return as image blocks. Mirrors TS FileReadTool.ts:898-948 PDF
            # page-extraction flow.
            first_page, last_page = _parse_pdf_pages(pages_param)
            from src.utils.pdf_extraction import (
                PdfExtractionFailed as _PdfErr,
                PdfExtractionUnavailable as _PdfMissing,
                extract_pdf_pages as _extract_pages,
            )
            try:
                ext_result = _extract_pages(resolved, first_page, last_page)
            except _PdfMissing as e:
                raise ToolInputError(str(e)) from e
            except _PdfErr as e:
                raise ToolInputError(f"PDF page extraction failed: {e}") from e
            # Read each page through the image pipeline so dimensions /
            # downscaling apply uniformly. The tempdir is always cleaned
            # up after we've base64-encoded the bytes -- they're in memory
            # now, the JPEGs on disk are no longer needed.
            from src.utils.image_processor import (
                ImageProcessingError as _ImgErr,
                ResizeResult as _ResizeResult,
                detect_image_format_from_buffer as _sniff_format,
                maybe_resize_image as _maybe_resize,
            )
            import shutil as _shutil
            image_blocks: list[dict[str, Any]] = []
            try:
                for page_path in ext_result.image_paths:
                    try:
                        page_bytes = page_path.read_bytes()
                    except OSError:
                        # Truncated/corrupted page write — skip this page rather
                        # than fail the whole extraction.
                        continue
                    if not page_bytes:
                        continue
                    detected = _sniff_format(page_bytes)
                    try:
                        page_result = _maybe_resize(page_bytes, len(page_bytes), format_hint=detected)
                    except _ImgErr:
                        page_result = _ResizeResult(data=page_bytes, media_type=detected, dimensions=None)
                    image_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": page_result.media_type,
                            "data": _base64.b64encode(page_result.data).decode("ascii"),
                        },
                    })
            finally:
                # Always clean up the tempdir, even on exception. The image
                # bytes are already in image_blocks, so the on-disk JPEGs
                # are garbage at this point.
                _shutil.rmtree(ext_result.output_dir, ignore_errors=True)
            # Emit a separate user message carrying the extracted pages,
            # mirroring TS FileReadTool.ts:941-948.
            new_messages = None
            if image_blocks:
                from src.types.messages import create_user_message
                new_messages = [create_user_message(image_blocks, isMeta=True)]
            return ToolResult(
                name="Read",
                output={
                    "type": "pdf_pages",
                    "file": {
                        "filePath": str(path),
                        "originalSize": ext_result.file_size,
                        "pageCount": len(image_blocks),
                    },
                },
                new_messages=new_messages,
            )
        # No pages param: TS behavior is to return raw PDF for first-party
        # models. Python doesn't differentiate, so we keep today's stub.
        context.mark_file_read(path)
        return ToolResult(
            name="Read",
            output={
                "type": "pdf",
                "file": {
                    "filePath": str(path),
                    "originalSize": stat.st_size,
                },
            },
        )

    # --- Image ---
    # Mirrors TS FileReadTool.ts readImageWithTokenBudget (lines 1100-1186):
    # bounded read -> magic-byte format detection -> resize-to-envelope ->
    # token-budget compression fallback. Oversized images get downscaled
    # rather than rejected outright.
    #
    # NOTE: deliberately does NOT call context.mark_file_read(). The dedup
    # path at lines 293-305 would otherwise replay subsequent image reads as
    # a text "file_unchanged" stub, silently dropping the image content from
    # the model's view. TS excludes images and PDFs from readFileState for
    # the same reason -- see FileReadTool.ts:528-529.
    ext_no_dot = suffix.lstrip(".")
    if ext_no_dot in IMAGE_EXTENSIONS:
        if stat.st_size == 0:
            raise ToolInputError(f"Image file is empty: {path}")
        # Lazy import keeps the read.py import cheap for non-image paths.
        from src.utils.image_processor import (
            IMAGE_READ_SAFETY_CAP as _IMG_READ_CAP,
            ImageProcessingError as _ImgErr,
            ResizeResult as _ResizeResult,
            compress_image_to_token_budget as _compress_to_tokens,
            create_image_metadata_text as _make_meta_text,
            detect_image_format_from_buffer as _sniff_format,
            estimate_image_tokens_from_base64_length as _est_tokens,
            maybe_resize_image as _maybe_resize,
            read_file_bytes as _bounded_read,
        )
        # Bounded read at the safety cap (50 MB): protects against symlinked
        # /dev/zero / TOCTOU-grown files without truncating real images, which
        # Pillow then resizes down to IMAGE_TARGET_RAW_SIZE in memory.
        img_bytes = _bounded_read(path, _IMG_READ_CAP)
        detected_media = _sniff_format(img_bytes)
        # Trust magic bytes over extension; matches TS detectImageFormatFromBuffer.
        try:
            result = _maybe_resize(img_bytes, stat.st_size, format_hint=detected_media)
        except _ImgErr:
            # Pillow couldn't decode (corrupt / non-image bytes). Fall back to
            # sending raw bytes so the model at least sees something. Matches
            # TS FileReadTool.ts:1133-1137 logError-and-continue path.
            result = _ResizeResult(
                data=img_bytes,
                media_type=detected_media,
                dimensions=None,
            )
        # Token-budget fallback (TS FileReadTool.ts:1140-1183). Compress from
        # the ORIGINAL buffer, not the already-resized one — re-encoding a
        # lossy JPEG twice produces worse quality than going back to source.
        # The compressor will rediscover the original dims on its own.
        max_tokens = _get_max_output_tokens()
        base64_len = ((len(result.data) + 2) // 3) * 4
        if _est_tokens(base64_len) > max_tokens:
            try:
                compressed = _compress_to_tokens(img_bytes, max_tokens, detected_media)
                result = compressed
            except _ImgErr:
                # Already-resized version is the best we have.
                pass
        b64 = _base64.b64encode(result.data).decode("ascii")
        dims = result.dimensions
        dims_dict = (
            {
                "originalWidth": dims.original_width,
                "originalHeight": dims.original_height,
                "displayWidth": dims.display_width,
                "displayHeight": dims.display_height,
            }
            if dims is not None
            else None
        )
        metadata_text = _make_meta_text(dims, str(path))
        new_messages = None
        if metadata_text:
            from src.types.messages import create_user_message
            new_messages = [create_user_message(metadata_text, isMeta=True)]
        return ToolResult(
            name="Read",
            output={
                "type": "image",
                "file": {
                    "filePath": str(path),
                    "base64": b64,
                    "type": result.media_type,
                    "originalSize": stat.st_size,
                    "dimensions": dims_dict,
                },
            },
            new_messages=new_messages,
        )

    # --- File size pre-check (prevents reading multi-GB files into memory) ---
    max_size = _get_max_size_bytes()
    # Only enforce size cap when no explicit offset/limit (reading the whole file).
    if offset is None and limit is None and stat.st_size > max_size:
        raise ToolInputError(
            f"File size ({stat.st_size:,} bytes) exceeds maximum allowed size "
            f"({max_size:,} bytes). Use offset and limit parameters to read "
            f"specific portions of the file, or use Grep to search for specific content."
        )

    # --- Text file reading ---
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines(keepends=True)
    total_lines = len(lines)

    start = (offset or 1) - 1
    end = start + (limit or MAX_LINES_TO_READ)
    selected = lines[start:end]

    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i}\t{line.rstrip()}")
    content = "\n".join(numbered)

    # --- Token estimate check (post-read) ---
    max_tokens = _get_max_output_tokens()
    token_est = _rough_token_estimate(content)
    if token_est > max_tokens:
        raise ToolInputError(
            f"File content (~{token_est:,} tokens) exceeds maximum allowed tokens "
            f"({max_tokens:,}). Use offset and limit parameters to read specific "
            f"portions of the file, or search for specific content instead of "
            f"reading the whole file."
        )

    is_partial = len(selected) < total_lines
    context.mark_file_read(path, partial=is_partial)

    return ToolResult(
        name="Read",
        output={
            "type": "text",
            "file": {
                "filePath": str(path),
                "content": content,
                "numLines": len(selected),
                "startLine": start + 1,
                "totalLines": total_lines,
            },
        },
    )


# ---------------------------------------------------------------------------
# mapResultToApi (improved formatting from TS)
# ---------------------------------------------------------------------------

def _read_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    if isinstance(output, dict):
        result_type = output.get("type")

        if result_type == "file_unchanged":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": FILE_UNCHANGED_STUB,
            }

        if result_type == "text":
            file_data = output.get("file", {})
            content_text = file_data.get("content", "")
            total_lines = file_data.get("totalLines", 0)
            start_line = file_data.get("startLine", 1)

            if not content_text:
                # Empty file or offset beyond file length
                if total_lines == 0:
                    content_text = (
                        "<system-reminder>Warning: the file exists but the "
                        "contents are empty.</system-reminder>"
                    )
                else:
                    content_text = (
                        f"<system-reminder>Warning: the file exists but is shorter "
                        f"than the provided offset ({start_line}). The file has "
                        f"{total_lines} lines.</system-reminder>"
                    )

            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content_text,
            }

        if result_type == "pdf":
            file_data = output.get("file", {})
            file_path = file_data.get("filePath", "")
            size = file_data.get("originalSize", 0)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"PDF file read: {file_path} ({size:,} bytes)",
            }

        if result_type == "pdf_pages":
            file_data = output.get("file", {})
            file_path = file_data.get("filePath", "")
            size = file_data.get("originalSize", 0)
            page_count = file_data.get("pageCount", 0)
            # Image blocks are carried out-of-band via new_messages; this
            # tool_result is just the human-readable acknowledgement.
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"PDF pages extracted: {page_count} page(s) from {file_path} ({size:,} bytes)",
            }

        if result_type == "image":
            # _read_call always populates base64+type from IMAGE_MIME_TYPES; if
            # they're missing here the producer is buggy and we want a loud
            # KeyError rather than a silently mislabeled image (e.g. JPEG bytes
            # tagged as PNG would be rejected by the API with a confusing error).
            file_data = output["file"]
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "data": file_data["base64"],
                            "media_type": file_data["type"],
                        },
                    },
                ],
            }

    # Fallback: serialize anything else
    if isinstance(output, str):
        content: str = output
    elif isinstance(output, dict):
        content = _json.dumps(output, ensure_ascii=False)
    else:
        content = str(output)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


def _read_check_permissions(tool_input: dict[str, Any], context: ToolContext):
    """Path-based read permission, mirroring TS ``FileReadTool.checkPermissions``.

    Working-dir and harness-internal reads are allowed silently; reads outside
    the workspace return ``passthrough`` (→ the read ``ask``). Tool-level
    ``Read`` deny/ask rules are still honored upstream, before this runs.
    """
    from src.permissions.filesystem import check_read_permission_for_tool

    file_path = (tool_input or {}).get("file_path", "")
    return check_read_permission_for_tool(file_path, context)


ReadTool: Tool = build_tool(
    name="Read",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read (must be absolute, not relative)",
            },
            "offset": {
                "type": "integer",
                "description": (
                    "The line number to start reading from. Only provide if "
                    "the file is too large to read at once"
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "The number of lines to read. Only provide if the file "
                    "is too large to read at once."
                ),
            },
            "pages": {
                "type": "string",
                "description": (
                    'Page range for PDF files (e.g., "1-5", "3", "10-20"). '
                    "Only applicable to PDF files. Maximum 20 pages per request."
                ),
            },
        },
        "required": ["file_path"],
    },
    call=_read_call,
    check_permissions=_read_check_permissions,
    # ch06 round-4 PR-A GAP B — expand file_path before permission checks +
    # PreToolUse hooks see it, so ~/relative paths can't dodge allowlists
    # (TS FileReadTool.ts:388). Overwrite-only: the model-original path
    # still reaches call() (call-input discipline) and the yielded message.
    backfill_observable_input=_backfill_read_edit_path,
    prompt=_render_prompt(),
    description="Read a file from the local filesystem.",
    map_result_to_api=_read_map_result_to_api,
    # Hard opt-out from result-persistence. Persisting Read output to
    # disk would create a circular loop (the model would Read the
    # persisted file, hitting the same size cap, persisting again).
    # The Read tool self-bounds via MAX_LINES_TO_READ + token
    # estimation; the persistence module special-cases ``math.inf``.
    # Mirrors TS ``maxResultSizeChars: Infinity`` in
    # ``FileReadTool.ts``. See ch06-tools.md "FileReadTool: The
    # Versatile Reader".
    max_result_size_chars=float("inf"),
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    get_path=lambda input_data: input_data.get("file_path", ""),
    user_facing_name=lambda input_data: f"Read: {(input_data or {}).get('file_path', '')}" if input_data else "Read",
    search_hint="read file cat view open",
    is_search_or_read_command=lambda _input: SearchOrReadResult(is_read=True),
    get_activity_description=lambda input_data: f"Reading {(input_data or {}).get('file_path', '')}" if input_data else None,
    # Mirrors TS FileReadTool.toAutoClassifierInput -- returns the
    # file_path verbatim. The auto-mode classifier sees the path
    # being read, which is sufficient context for the read-vs-leak
    # decision. See ch06-tools.md "Apply This".
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("file_path", ""),
)

"""Binary tool-output persistence: side-channel for oversized blobs.

Phase 8 WI-8.5 (gap #21 cousin). Mirrors typescript/src/utils/
mcpOutputStorage.ts. A misbehaving MCP server may return a multi-MB
image / file / blob that we can't pipe back through the model's
context window. This module writes the blob to a tempfile and returns
a model-facing placeholder containing the path.

Caller flow:
  1. ``persist_binary_content(server_name, tool_name, blob_bytes, content_type)``
     → returns Path of the saved file.
  2. ``get_binary_blob_saved_message(path, original_size)`` → returns
     a short, model-readable summary the tool wrapper can include in
     the textual result. The model then sees a stable summary
     ("binary content saved to /tmp/...") instead of raw bytes.

The blob directory is process-temp; operators wanting to inspect or
share it can read the path. No automatic cleanup — tempdir conventions
delete stale files on reboot.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_BLOB_DIR = Path(tempfile.gettempdir()) / "claude-mcp-blobs"

# Naive Content-Type → file extension map. Server-supplied content_type
# can be arbitrary; we just want a reasonable suffix for human-readable
# paths.
_EXTENSION_FOR_TYPE: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "text/plain": ".txt",
    "text/html": ".html",
    "application/octet-stream": ".bin",
}


def _ext_for_content_type(content_type: str) -> str:
    if not content_type:
        return ".bin"
    base = content_type.split(";", 1)[0].strip().lower()
    return _EXTENSION_FOR_TYPE.get(base, ".bin")


def persist_binary_content(
    server_name: str,
    tool_name: str,
    content_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> Path:
    """Write ``content_bytes`` to a unique tempfile and return its path.

    Safe naming: ``<server>-<tool>-<uuid8><ext>`` with non-filename-safe
    characters in the prefix replaced by underscore. UUID prefix
    guarantees uniqueness; non-monotonic so multiple writes from the
    same (server, tool) don't race.
    """
    _BLOB_DIR.mkdir(parents=True, exist_ok=True)
    safe_server = _safe_filename_chunk(server_name)
    safe_tool = _safe_filename_chunk(tool_name)
    blob_id = uuid.uuid4().hex[:12]
    ext = _ext_for_content_type(content_type)
    path = _BLOB_DIR / f"{safe_server}-{safe_tool}-{blob_id}{ext}"
    try:
        path.write_bytes(content_bytes)
    except OSError as exc:
        logger.warning(
            "MCP output_storage: failed to write blob for %s/%s: %s",
            server_name, tool_name, exc,
        )
        raise
    return path


def get_binary_blob_saved_message(path: Path, original_size: int) -> str:
    """Return a one-line, model-facing summary pointing at the saved file."""
    return (
        f"[binary content saved to {path}; {original_size} bytes; "
        f"access via the file at that path]"
    )


def _safe_filename_chunk(s: str) -> str:
    """Restrict to filename-safe chars; cap length so paths stay short."""
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    return out[:32] or "x"

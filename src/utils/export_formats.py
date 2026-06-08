"""Export format types and helpers for multi-format conversation export.

Faithful port of ``typescript/src/utils/exportFormats.ts``. The TS code maps
filesystem paths with node's ``path`` module (``extname``/``isAbsolute``/
``join``); the Python port uses :mod:`os.path`, whose ``splitext``/``isabs``/
``join`` behave identically on the cases these helpers care about (verified
against the ported test fixtures, including ``'conversation.'`` -> ``'.'`` and
dotfiles like ``'.env'`` having no extension).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, NamedTuple, Optional

try:  # Python 3.8+ Literal
    from typing import Literal
except ImportError:  # pragma: no cover - fallback for very old runtimes
    from typing_extensions import Literal  # type: ignore

ExportFormat = Literal["text", "markdown", "json"]


def normalize_export_format(value: str) -> Optional[ExportFormat]:
    """Normalize a user-provided format string to an :data:`ExportFormat`.

    Returns ``None`` for unrecognized values.
    """
    lower = value.lower().strip()
    if lower in ("text", "txt"):
        return "text"
    if lower in ("markdown", "md"):
        return "markdown"
    if lower == "json":
        return "json"
    return None


def infer_export_format_from_filename(filename: str) -> Optional[ExportFormat]:
    """Infer export format from a filename's extension.

    Returns ``None`` if the extension doesn't map to a known format.
    """
    ext = os.path.splitext(filename)[1]
    if not ext or ext == ".":
        return None
    return normalize_export_format(ext[1:])


def extension_for_export_format(format: ExportFormat) -> str:
    """Get the canonical file extension for an export format."""
    if format == "text":
        return ".txt"
    if format == "markdown":
        return ".md"
    return ".json"


def ensure_export_filename_extension(
    filename: str,
    format: ExportFormat,
    *,
    preserve_markdown_extension: bool = False,
) -> str:
    """Ensure a filename has the correct extension for the export format.

    Strips any existing extension and appends the canonical one.
    """
    ext = extension_for_export_format(format)
    current_ext = os.path.splitext(filename)[1]
    if (
        format == "markdown"
        and preserve_markdown_extension
        and current_ext.lower() == ".markdown"
    ):
        return filename
    if current_ext:
        base = filename[:-1] if current_ext == "." else filename[: -len(current_ext)]
    else:
        base = filename
    return base + ext


def resolve_export_filepath(cwd: str, filename: str) -> str:
    return filename if os.path.isabs(filename) else os.path.join(cwd, filename)


SUPPORTED_FORMATS = "Supported formats: text, markdown, json."


class _Token(NamedTuple):
    value: str
    quoted: bool


def _tokenize_export_args(args: str) -> "tuple[List[_Token], Optional[str]]":
    tokens: List[_Token] = []
    current = ""
    quote: Optional[str] = None
    token_started = False
    token_quoted = False

    i = 0
    n = len(args)
    while i < n:
        ch = args[i]

        if quote is not None:
            if ch == quote:
                quote = None
                i += 1
                continue
            if quote == '"' and ch == "\\" and i + 1 < n:
                nxt = args[i + 1]
                if nxt == '"' or nxt == "\\":
                    current += nxt
                    i += 2
                    continue
            current += ch
            i += 1
            continue

        if ch == '"' or ch == "'":
            quote = ch
            token_started = True
            token_quoted = True
            i += 1
            continue

        if ch.isspace():
            if token_started:
                tokens.append(_Token(current, token_quoted))
                current = ""
                token_started = False
                token_quoted = False
            i += 1
            continue

        current += ch
        token_started = True
        i += 1

    if quote is not None:
        return tokens, "Unterminated quoted string in /export arguments."

    if token_started:
        tokens.append(_Token(current, token_quoted))

    return tokens, None


@dataclass(frozen=True)
class ParsedExportArgs:
    filename: Optional[str] = None
    format: Optional[ExportFormat] = None
    error: Optional[str] = None


def parse_export_args(args: str) -> ParsedExportArgs:
    """Parse ``/export`` command arguments.

    Supported flags: ``--format <value>`` or ``-f <value>``.

    Returns parsed filename, format, and optional error string.
    """
    tokens, tok_error = _tokenize_export_args(args)
    if tok_error:
        return ParsedExportArgs(error=tok_error)

    if len(tokens) == 0:
        return ParsedExportArgs()

    fmt: Optional[ExportFormat] = None
    error: Optional[str] = None
    filename_tokens: List[str] = []

    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if not token.quoted and token.value == "--":
            filename_tokens.extend(t.value for t in tokens[i + 1 :])
            break
        if not token.quoted and token.value in ("--format", "-f"):
            i += 1
            value = tokens[i].value if i < n else None
            if not value:
                error = f"Missing value for {token.value}. {SUPPORTED_FORMATS}"
                break
            normalized = normalize_export_format(value)
            if not normalized:
                error = f"Unsupported export format: {value}. {SUPPORTED_FORMATS}"
                break
            fmt = normalized
        elif (
            not token.quoted
            and token.value.startswith("-")
            and token.value != "-"
        ):
            error = (
                f"Unsupported export option: {token.value}. "
                "Supported options: --format, -f."
            )
            break
        else:
            filename_tokens.append(token.value)
        i += 1

    filename = " ".join(filename_tokens) if filename_tokens else None

    if error:
        return ParsedExportArgs(filename=filename, format=fmt, error=error)

    return ParsedExportArgs(filename=filename, format=fmt)


__all__ = [
    "ExportFormat",
    "ParsedExportArgs",
    "SUPPORTED_FORMATS",
    "ensure_export_filename_extension",
    "extension_for_export_format",
    "infer_export_format_from_filename",
    "normalize_export_format",
    "parse_export_args",
    "resolve_export_filepath",
]

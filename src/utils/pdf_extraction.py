"""PDF page extraction via poppler's ``pdftoppm``.

Port of ``typescript/src/utils/pdf.ts:179-290`` ``extractPDFPages``. Shells out
to ``pdftoppm`` (from poppler-utils) to convert PDF pages into JPEG images,
which the Read tool then processes through the same image pipeline as direct
image reads.

Requires ``pdftoppm`` on ``PATH``. If absent, ``extract_pdf_pages`` raises
``PdfExtractionUnavailable`` with an install hint.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 100 DPI matches TS pdf.ts (``pdftoppm -r 100``).
_PDF_DPI = 100
# Conservative timeout for pdftoppm; 30 pages of 100 DPI JPEGs is fast.
_PDFTOPPM_TIMEOUT_S = 120
# Reject PDFs larger than this before invoking pdftoppm. Matches TS
# PDF_MAX_EXTRACT_SIZE at typescript/src/constants/apiLimits.ts:72.
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # 100 MB


class PdfExtractionUnavailable(Exception):
    """Raised when ``pdftoppm`` is not installed."""


class PdfExtractionFailed(Exception):
    """Raised when ``pdftoppm`` ran but failed."""


@dataclass(frozen=True)
class PdfPageExtractionResult:
    """Result of extracting PDF pages to JPEG images."""
    output_dir: Path
    page_count: int
    file_size: int
    image_paths: list[Path]


def _have_pdftoppm() -> bool:
    return shutil.which("pdftoppm") is not None


def _log_pdf_event(success: bool, **fields: Any) -> None:
    try:
        from src.services.analytics.events import EventType, log_event
        log_event(
            EventType.IMAGE_PROCESSING,
            subtype="pdf_page_extraction",
            success=success,
            **fields,
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


def extract_pdf_pages(
    pdf_path: Path,
    first_page: int | None = None,
    last_page: int | None = None,
) -> PdfPageExtractionResult:
    """Run ``pdftoppm`` to extract pages of ``pdf_path`` as JPEGs.

    When ``first_page``/``last_page`` are None, all pages are extracted.
    Pages are numbered from 1. The output directory is a fresh tempdir; the
    caller is responsible for cleanup (or letting the OS clean ``/tmp``).
    """
    # Validate the input *before* checking for pdftoppm so the user sees
    # actionable feedback (empty/oversize/missing PDF) even on hosts that
    # happen to be missing poppler.
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise PdfExtractionFailed(f"PDF does not exist: {pdf_path}")

    file_size = pdf_path.stat().st_size
    if file_size == 0:
        raise PdfExtractionFailed(f"PDF file is empty: {pdf_path}")
    if file_size > PDF_MAX_EXTRACT_SIZE:
        raise PdfExtractionFailed(
            f"PDF size ({file_size:,} bytes) exceeds maximum allowed size "
            f"({PDF_MAX_EXTRACT_SIZE:,} bytes). Use a smaller PDF or extract "
            f"a specific page range with the `pages` parameter."
        )

    if not _have_pdftoppm():
        raise PdfExtractionUnavailable(
            "pdftoppm not found on PATH. Install poppler-utils: "
            "`brew install poppler` on macOS or "
            "`apt-get install poppler-utils` on Debian/Ubuntu."
        )

    out_dir = Path(tempfile.mkdtemp(prefix="clawcodex-pdf-"))
    out_prefix = out_dir / "page"

    argv = ["pdftoppm", "-jpeg", "-r", str(_PDF_DPI)]
    if first_page is not None:
        argv += ["-f", str(first_page)]
    if last_page is not None:
        argv += ["-l", str(last_page)]
    argv += [str(pdf_path), str(out_prefix)]

    try:
        subprocess.run(
            argv,
            check=True,
            timeout=_PDFTOPPM_TIMEOUT_S,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        _log_pdf_event(False, error="exit_code", code=e.returncode, file_size=file_size)
        # Clean up the tempdir on failure to avoid leaks
        shutil.rmtree(out_dir, ignore_errors=True)
        stderr = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        raise PdfExtractionFailed(
            f"pdftoppm failed (exit {e.returncode}): {stderr or '<no stderr>'}"
        ) from e
    except subprocess.TimeoutExpired as e:
        _log_pdf_event(False, error="timeout", file_size=file_size)
        shutil.rmtree(out_dir, ignore_errors=True)
        raise PdfExtractionFailed(
            f"pdftoppm timed out after {_PDFTOPPM_TIMEOUT_S}s"
        ) from e

    image_paths = sorted(out_dir.glob("page-*.jpg"))
    _log_pdf_event(
        True,
        page_count=len(image_paths),
        file_size=file_size,
        first_page=first_page,
        last_page=last_page,
    )
    return PdfPageExtractionResult(
        output_dir=out_dir,
        page_count=len(image_paths),
        file_size=file_size,
        image_paths=image_paths,
    )

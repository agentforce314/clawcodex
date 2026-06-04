"""Tests for src.utils.pdf_extraction.

Most tests are skipped when ``pdftoppm`` is not installed; the unavailable
path is always testable since it's a pure ``shutil.which`` check.
"""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from src.utils.pdf_extraction import (
    PdfExtractionFailed,
    PdfExtractionUnavailable,
    extract_pdf_pages,
)


_HAS_PDFTOPPM = shutil.which("pdftoppm") is not None


class TestPdfExtractionUnavailableHandling(unittest.TestCase):
    @unittest.skipIf(_HAS_PDFTOPPM, "this case requires pdftoppm to be missing")
    def test_raises_unavailable_when_pdftoppm_missing(self) -> None:
        """When the input PDF exists and is valid, but pdftoppm is missing,
        the user sees the install hint. Input validation runs first now
        (PDFs that don't exist / are empty / oversize give those errors
        regardless of pdftoppm availability), so we need a real PDF on disk."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Minimal valid PDF stub (header + EOF marker is enough to pass
            # the existence/size guards; pdftoppm would fail later but we
            # don't get there in this test).
            f.write(b"%PDF-1.4\n%%EOF\n")
            pdf_path = Path(f.name)
        try:
            with self.assertRaises(PdfExtractionUnavailable) as cm:
                extract_pdf_pages(pdf_path)
            msg = str(cm.exception)
            self.assertIn("poppler", msg.lower())
        finally:
            pdf_path.unlink(missing_ok=True)

    def test_validates_input_before_pdftoppm_check(self) -> None:
        """Empty/missing/oversize PDFs report actionable errors even on
        hosts without pdftoppm. Regression for the critic-flagged ordering bug."""
        from src.utils.pdf_extraction import PdfExtractionFailed
        with self.assertRaises(PdfExtractionFailed) as cm:
            extract_pdf_pages(Path("/tmp/__definitely_does_not_exist__.pdf"))
        self.assertIn("does not exist", str(cm.exception))


@unittest.skipUnless(_HAS_PDFTOPPM, "pdftoppm not installed; skipping PDF extraction tests")
class TestPdfExtractionWithPdftoppm(unittest.TestCase):
    """These tests run only when poppler-utils is installed."""

    def test_missing_pdf_raises_failed(self) -> None:
        with self.assertRaises(PdfExtractionFailed):
            extract_pdf_pages(Path("/tmp/__definitely_not_a_pdf__.pdf"))


if __name__ == "__main__":
    unittest.main()

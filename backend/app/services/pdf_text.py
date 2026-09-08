"""PDF inspection and text extraction.

pdfplumber is the primary text reader (it preserves the visual line/column order
that the deterministic parser relies on). pypdf is used only for the cheap
structural checks — encryption and page count — before the heavier read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdf

PRIMARY_TEXT_READER = "pdfplumber"

#: A page with fewer than this many alphanumeric characters is treated as
#: image-only. Real text invoice pages in this corpus carry several hundred;
#: a rasterised page yields zero or a handful of stray glyphs.
TEXT_LAYER_MIN_ALNUM = 40

_ALNUM_RE = re.compile(r"[0-9A-Za-z]")


class PdfError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PageText:
    page_number: int
    text: str
    needs_ocr: bool
    alnum_count: int
    provenance: str = "pdf_text"


@dataclass
class DocumentText:
    pages: list[PageText] = field(default_factory=list)
    reader: str = PRIMARY_TEXT_READER

    @property
    def page_texts(self) -> list[str]:
        return [page.text for page in self.pages]

    @property
    def ocr_pages(self) -> list[int]:
        return [page.page_number for page in self.pages if page.provenance == "ocr"]


def inspect_pdf(path: Path, max_pages: int) -> int:
    """Validate that the file really is a readable, unencrypted PDF.

    Returns the page count. Raises :class:`PdfError` with a specific code so the
    API can distinguish an encrypted file from a corrupt one.
    """
    try:
        reader = pypdf.PdfReader(str(path), strict=False)
    except Exception as exc:  # pypdf raises several unrelated exception types
        raise PdfError("pdf_unreadable", "The file could not be parsed as a PDF.") from exc

    if reader.is_encrypted:
        # An empty user password is common and harmless; anything else is a real
        # password-protected document we will not attempt to open.
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            raise PdfError(
                "pdf_encrypted",
                "The PDF is password protected. Upload an unprotected copy.",
            )

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise PdfError("pdf_unreadable", "The PDF page tree is damaged.") from exc

    if page_count == 0:
        raise PdfError("pdf_unreadable", "The PDF contains no pages.")
    if page_count > max_pages:
        raise PdfError(
            "pdf_too_many_pages",
            f"The PDF has {page_count} pages; the limit is {max_pages}.",
        )
    return page_count


def extract_text(path: Path) -> DocumentText:
    """Read every page's text layer and flag the pages that will need OCR."""
    pages: list[PageText] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                raw = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                alnum = len(_ALNUM_RE.findall(raw))
                pages.append(
                    PageText(
                        page_number=index,
                        text=raw,
                        needs_ocr=alnum < TEXT_LAYER_MIN_ALNUM,
                        alnum_count=alnum,
                    )
                )
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError("pdf_unreadable", "The PDF text layer could not be read.") from exc
    return DocumentText(pages=pages)


def normalize_for_evidence(text: str) -> str:
    """Whitespace-insensitive form used when checking a quoted excerpt."""
    return re.sub(r"\s+", " ", text or "").strip().casefold()

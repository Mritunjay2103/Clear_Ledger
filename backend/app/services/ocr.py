"""Bounded OCR wrapper around the Tesseract executable.

Tesseract is invoked with an argument list and ``shell=False``; no part of a
user-supplied filename ever reaches a shell. Rasterisation is capped by DPI and
total pixels, and each page run is capped by a timeout, so a hostile or merely
enormous PDF cannot occupy the worker indefinitely.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from app.config import settings

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


class OcrError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class OcrStatus:
    available: bool
    executable: str | None
    version: str | None
    languages: list[str]
    detail: str


def find_tesseract() -> str | None:
    explicit = os.environ.get("TESSERACT_CMD")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _WINDOWS_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def probe() -> OcrStatus:
    """Report real availability. Never claim OCR works when the binary is absent."""
    executable = find_tesseract()
    if not executable:
        return OcrStatus(
            available=False,
            executable=None,
            version=None,
            languages=[],
            detail=(
                "Tesseract was not found on PATH. Install it (Windows: "
                "winget install --id UB-Mannheim.TesseractOCR; macOS: brew install tesseract; "
                "Debian/Ubuntu: apt-get install tesseract-ocr tesseract-ocr-eng) or set "
                "TESSERACT_CMD. The Docker image already contains it."
            ),
        )
    version = None
    languages: list[str] = []
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=15, shell=False
        )
        version = (result.stdout or result.stderr or "").splitlines()[0].strip() or None
    except Exception:
        version = None
    try:
        listing = subprocess.run(
            [executable, "--list-langs"], capture_output=True, text=True, timeout=15, shell=False
        )
        lines = (listing.stdout or "").splitlines()[1:]
        languages = [line.strip() for line in lines if line.strip()]
    except Exception:
        languages = []

    if languages and "eng" not in languages:
        return OcrStatus(
            available=False,
            executable=executable,
            version=version,
            languages=languages,
            detail="Tesseract is installed but the English language data (eng) is missing.",
        )
    return OcrStatus(
        available=True,
        executable=executable,
        version=version,
        languages=languages,
        detail="Tesseract is available.",
    )


def render_page_to_png(pdf_path: Path, page_number: int, out_dir: Path) -> Path:
    """Rasterise one page at the configured DPI, enforcing a pixel ceiling."""
    scale = settings.ocr_dpi / 72.0
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        width_pt, height_pt = page.get_size()
        megapixels = (width_pt * scale) * (height_pt * scale) / 1_000_000
        if megapixels > settings.ocr_max_megapixels:
            raise OcrError(
                "ocr_page_too_large",
                f"Page {page_number} would rasterise to {megapixels:.1f} MP, above the "
                f"{settings.ocr_max_megapixels:.0f} MP limit.",
            )
        bitmap = page.render(scale=scale, grayscale=True)
        image = bitmap.to_pil()
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"page-{page_number}.png"
        image.save(target, format="PNG")
        return target
    finally:
        document.close()


def ocr_page(pdf_path: Path, page_number: int) -> str:
    """Return OCR text for one page. Raises OcrError with a specific code."""
    status = probe()
    if not status.available or not status.executable:
        raise OcrError("ocr_unavailable", status.detail)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(settings.tmp_dir)) as tmp:
        tmp_dir = Path(tmp)
        image_path = render_page_to_png(pdf_path, page_number, tmp_dir)
        output_base = tmp_dir / f"page-{page_number}-text"
        command = [
            status.executable,
            str(image_path),
            str(output_base),
            "-l",
            "eng",
            "--oem",
            "1",
            "--psm",
            "6",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.ocr_timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError(
                "ocr_timeout",
                f"OCR of page {page_number} exceeded {settings.ocr_timeout_seconds}s.",
            ) from exc
        if completed.returncode != 0:
            raise OcrError(
                "ocr_failed",
                f"Tesseract exited with status {completed.returncode} on page {page_number}.",
            )
        text_file = output_base.with_suffix(".txt")
        if not text_file.exists():
            raise OcrError("ocr_failed", f"Tesseract produced no output for page {page_number}.")
        return text_file.read_text(encoding="utf-8", errors="replace")

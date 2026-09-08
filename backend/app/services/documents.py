"""Upload intake and document storage.

Storage keys are server-generated UUIDs. A filename supplied by a client is kept
only as a display label after sanitisation, and is never used to build a path,
so a crafted name cannot escape the data directory.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import InvoiceDocument
from app.services.pdf_text import PdfError, inspect_pdf

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")
_CHUNK = 64 * 1024
PDF_MAGIC = b"%PDF-"


class IntakeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class StoredUpload:
    storage_key: str
    path: Path
    sha256: str
    byte_count: int
    page_count: int
    safe_filename: str


def sanitize_filename(raw: str | None) -> str:
    name = Path(raw or "invoice.pdf").name
    name = _SAFE_NAME_RE.sub("_", name).strip() or "invoice.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name[:200]


def storage_path(storage_key: str) -> Path:
    return settings.uploads_dir / f"{storage_key}.pdf"


def store_upload(stream: BinaryIO, original_filename: str | None) -> StoredUpload:
    """Stream an upload to disk, enforcing the size limit before it is accepted.

    Bytes are counted as they arrive and the partial file is removed the moment
    the limit is passed, so an oversized upload cannot fill the disk first.
    """
    settings.ensure_directories()
    storage_key = str(uuid.uuid4())
    target = storage_path(storage_key)
    digest = hashlib.sha256()
    total = 0
    first_chunk = True

    try:
        with target.open("wb") as handle:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                if first_chunk:
                    if not chunk.startswith(PDF_MAGIC):
                        raise IntakeError(
                            "unsupported_media_type",
                            "Only PDF files are accepted; this file is not a PDF.",
                            status_code=415,
                        )
                    first_chunk = False
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise IntakeError(
                        "file_too_large",
                        f"The file exceeds the {settings.max_upload_mb} MiB limit.",
                        status_code=413,
                    )
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise IntakeError("empty_file", "The uploaded file is empty.")
        page_count = inspect_pdf(target, settings.max_pdf_pages)
    except IntakeError:
        target.unlink(missing_ok=True)
        raise
    except PdfError as exc:
        target.unlink(missing_ok=True)
        status = 413 if exc.code == "pdf_too_many_pages" else 422
        raise IntakeError(exc.code, exc.message, status_code=status) from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return StoredUpload(
        storage_key=storage_key,
        path=target,
        sha256=digest.hexdigest(),
        byte_count=total,
        page_count=page_count,
        safe_filename=sanitize_filename(original_filename),
    )


def persist_document(session: Session, upload: StoredUpload) -> InvoiceDocument:
    document = InvoiceDocument(
        safe_original_filename=upload.safe_filename,
        storage_key=upload.storage_key,
        sha256=upload.sha256,
        byte_count=upload.byte_count,
        page_count=upload.page_count,
    )
    session.add(document)
    session.flush()
    return document


def find_documents_by_hash(session: Session, sha256: str) -> list[InvoiceDocument]:
    return list(
        session.scalars(
            select(InvoiceDocument)
            .where(InvoiceDocument.sha256 == sha256)
            .order_by(InvoiceDocument.created_at)
        )
    )


def delete_stored_file(storage_key: str) -> None:
    storage_path(storage_key).unlink(missing_ok=True)

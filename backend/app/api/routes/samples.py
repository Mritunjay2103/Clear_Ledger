"""The synthetic sample catalog.

The catalog deliberately excludes expected decisions. Those live in
``data/samples/expected_results.json``, which only tests and documentation read,
so no runtime path can consult an answer key.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.errors import ApiError
from app.config import PROJECT_ROOT

router = APIRouter(prefix="/samples", tags=["samples"])

SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
CATALOG_PATH = SAMPLES_DIR / "catalog.json"
PDF_DIR = SAMPLES_DIR / "pdf"


@lru_cache
def _catalog() -> dict:
    if not CATALOG_PATH.exists():
        return {"samples": [], "disclaimer": "Sample pack has not been generated yet."}
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def sample_path(sample_id: str) -> Path:
    """Resolve a sample id to its PDF, refusing anything outside the pack."""
    for entry in _catalog().get("samples", []):
        if entry.get("id") == sample_id:
            candidate = (PDF_DIR / entry["filename"]).resolve()
            if candidate.parent != PDF_DIR.resolve() or not candidate.exists():
                raise ApiError(
                    "sample_missing", "The sample file is not available.", status_code=404
                )
            return candidate
    raise ApiError("sample_not_found", f"No sample named {sample_id!r}.", status_code=404)


def sample_entry(sample_id: str) -> dict | None:
    for entry in _catalog().get("samples", []):
        if entry.get("id") == sample_id:
            return entry
    return None


@router.get("", summary="Synthetic sample catalog")
def list_samples() -> dict:
    catalog = _catalog()
    samples = sorted(catalog.get("samples", []), key=lambda entry: entry.get("order", 0))
    scenarios: list[dict] = []
    for entry in samples:
        group = next((item for item in scenarios if item["scenario"] == entry["scenario"]), None)
        if group is None:
            group = {"scenario": entry["scenario"], "samples": []}
            scenarios.append(group)
        group["samples"].append(entry)
    return {
        "disclaimer": catalog.get("disclaimer"),
        "generated_from_reference_date": catalog.get("generated_from_reference_date"),
        "scenarios": scenarios,
        "samples": samples,
    }


@router.get("/{sample_id}/file", summary="Download a sample PDF")
def download_sample(sample_id: str) -> FileResponse:
    path = sample_path(sample_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )

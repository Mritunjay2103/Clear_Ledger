"""Liveness, readiness and capability reporting."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Vendor
from app.db.session import get_db
from app.domain.policy import DEFAULT_POLICY
from app.services import ocr as ocr_service
from app.services.extraction import provider_availability
from app.services.queue import run_queue

router = APIRouter(tags=["system"])

APP_VERSION = "1.0.0"


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok", "app": "clearledger", "version": APP_VERSION}


@router.get("/readiness", summary="Readiness")
def readiness(session: Session = Depends(get_db)) -> dict:
    checks: dict[str, str] = {}
    ready = True
    try:
        session.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        ready = False
    try:
        settings.ensure_directories()
        checks["data_dir"] = "writable"
    except Exception:
        checks["data_dir"] = "not writable"
        ready = False
    try:
        seeded = session.scalar(select(Vendor.id).limit(1)) is not None
        checks["reference_data"] = "seeded" if seeded else "empty"
        ready = ready and seeded
    except Exception:
        checks["reference_data"] = "unknown"
        ready = False
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/capabilities", summary="Extraction, OCR and runtime capabilities")
def capabilities() -> dict:
    ocr_status = ocr_service.probe()
    configured = settings.extraction_provider
    providers = {}
    for name in ("rules", "ollama", "openai_compatible"):
        availability = provider_availability(name)
        providers[name] = {
            "available": availability.available,
            "detail": availability.detail,
            "model": availability.model,
            "setup_command": availability.setup_command,
            "active": name == configured,
        }
    return {
        "app_version": APP_VERSION,
        "environment": settings.app_env,
        "demo_mode": True,
        "demo_notice": (
            "Synthetic demonstration data. An APPROVED decision reserves a commitment against a "
            "purchase order; it never initiates a payment."
        ),
        "extraction": {
            "configured_provider": configured,
            "configured_provider_label": {
                "rules": "Rules-based extraction",
                "ollama": "Local model extraction (Ollama)",
                "openai_compatible": "Hosted model extraction",
            }.get(configured, configured),
            "allow_rules_fallback": settings.allow_rules_fallback,
            "providers": providers,
        },
        "ocr": {
            "available": ocr_status.available,
            "engine": ocr_status.version,
            "languages": ocr_status.languages,
            "detail": ocr_status.detail,
            "render_dpi": settings.ocr_dpi,
            "timeout_seconds": settings.ocr_timeout_seconds,
        },
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "max_queue_depth": settings.max_queue_depth,
            "queue_depth": run_queue.depth,
        },
        "auth": {"reviewer_login_required": settings.auth_enabled},
    }


@router.get("/policy", summary="Active policy version in plain language")
def policy() -> dict:
    config = DEFAULT_POLICY
    return {
        "version": config.version,
        "values": config.as_snapshot(),
        "plain_language": [
            {
                "title": "Automatic evaluation is INR only",
                "detail": (
                    "Invoices in other currencies are routed to review instead of being "
                    "converted. No exchange rate is applied anywhere."
                ),
            },
            {
                "title": "Gross is compared with gross",
                "detail": (
                    "The invoice total including tax is compared with the approved gross value of "
                    "the purchase order, in the same currency."
                ),
            },
            {
                "title": "Overage tolerance is the smaller of 1% and INR 500.00",
                "detail": (
                    "tolerance = min(purchase order total x 0.01, INR 500.00). For a "
                    "INR 100,000.00 purchase order that is INR 500.00, giving an allowed "
                    "cumulative total of INR 100,500.00."
                ),
            },
            {
                "title": "Partial invoices are normal",
                "detail": (
                    "An invoice smaller than the purchase order is not rejected. Commitments "
                    "accumulate across invoices and are checked against the tolerated limit."
                ),
            },
            {
                "title": "A missing purchase-order reference always needs review",
                "detail": (
                    "Even when exactly one open purchase order looks plausible, version 1 asks a "
                    "person to choose it and record a reason."
                ),
            },
            {
                "title": "Blocked vendors and duplicate identities are blocked, not reviewable",
                "detail": (
                    "A correction cannot override a blocked vendor, an already-approved invoice "
                    "identity, or the cumulative limit. There is no 'approve anyway' action."
                ),
            },
            {
                "title": "APPROVED reserves a commitment, it does not pay",
                "detail": (
                    "Approval writes a reservation against the purchase order so later invoices "
                    "see the committed balance. This prototype never moves money."
                ),
            },
        ],
        "boundaries": [
            "One fictional company and a single demonstration workspace.",
            "English-language PDFs; INR for automatic monetary evaluation.",
            "Invoice-to-purchase-order comparison only. There is no goods-receipt data, so this "
            "is not three-way matching.",
            "Reference data is read-only in the interface; the CSV files are the editable source.",
            "No tax compliance, vendor legitimacy, or bank-detail verification is performed.",
        ],
    }

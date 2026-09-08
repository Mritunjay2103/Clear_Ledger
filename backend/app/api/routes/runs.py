"""Run creation, history, live events, retries, corrections and exports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.routes.samples import sample_entry, sample_path
from app.api.serializers import serialize_event, serialize_run_detail, serialize_run_summary
from app.db.models import (
    IdempotencyRecord,
    InvoiceCase,
    InvoiceDocument,
    PurchaseOrder,
    ReviewAction,
    Vendor,
    WorkflowEvent,
    WorkflowRun,
)
from app.db.session import get_db
from app.domain.policy import DEFAULT_POLICY
from app.services import documents as document_service
from app.services import exports, ledger
from app.services.documents import IntakeError
from app.services.queue import QueueFull, run_queue
from app.services.workflow import CORRECTABLE_FIELDS

router = APIRouter(prefix="/runs", tags=["runs"])

MAX_PAGE_SIZE = 100


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_label: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=8, max_length=1000)
    expected_version: int = Field(ge=1)
    fields: dict[str, str] = Field(default_factory=dict)
    selected_po_id: str | None = None
    selected_vendor_id: str | None = None

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(CORRECTABLE_FIELDS))
        if unknown:
            raise ValueError(
                f"{', '.join(unknown)} cannot be corrected. "
                f"Allowed: {', '.join(CORRECTABLE_FIELDS)}"
            )
        cleaned = {key: str(item).strip() for key, item in value.items() if str(item).strip()}
        return cleaned


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\u241f".join(parts).encode("utf-8")).hexdigest()


def _check_idempotency(
    session: Session, key: str | None, scope: str, fingerprint: str
) -> WorkflowRun | None:
    """Return the original run for a repeated key, or raise 409 on a changed payload."""
    if not key:
        return None
    record = session.get(IdempotencyRecord, {"key": key, "scope": scope})
    if record is None:
        return None
    if record.request_fingerprint != fingerprint:
        raise ApiError(
            "idempotency_key_reused",
            "This Idempotency-Key was already used for a different request.",
            status_code=409,
            detail="Use a new key for a different document or action.",
        )
    return session.get(WorkflowRun, record.run_id)


def _record_idempotency(
    session: Session, key: str | None, scope: str, fingerprint: str, run: WorkflowRun
) -> None:
    if not key:
        return
    session.add(
        IdempotencyRecord(
            key=key,
            scope=scope,
            request_fingerprint=fingerprint,
            run_id=run.id,
            case_id=run.case_id,
        )
    )
    session.commit()


def _queue(run: WorkflowRun) -> None:
    try:
        run_queue.enqueue(run.id)
    except QueueFull as exc:
        raise ApiError(
            "queue_full",
            str(exc),
            status_code=503,
            retryable=True,
        ) from exc


@router.post("", status_code=202, summary="Upload a PDF and start a run")
async def create_run(
    request: Request,
    file: UploadFile | None = File(default=None),
    sample_id: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> JSONResponse:
    """Accept the document, commit intake, and return 202 with the queued run.

    A sample is submitted through this same path: its real bytes are read from
    disk and pushed through the identical ingestion service, so nothing about a
    sample run is special-cased downstream.
    """
    if file is None and not sample_id:
        raise ApiError(
            "no_document", "Attach a PDF file or name a sample to process.", status_code=422
        )

    trigger: Literal["upload", "sample"] = "upload"
    try:
        if file is not None:
            stored = document_service.store_upload(file.file, file.filename)
        else:
            entry = sample_entry(sample_id or "")
            if entry is None:
                raise ApiError(
                    "sample_not_found", f"No sample named {sample_id!r}.", status_code=404
                )
            path = sample_path(sample_id or "")
            trigger = "sample"
            with path.open("rb") as handle:
                stored = document_service.store_upload(handle, path.name)
    except IntakeError as exc:
        raise ApiError(exc.code, exc.message, status_code=exc.status_code) from exc

    fingerprint = _fingerprint("runs.create", stored.sha256, trigger)
    existing = _check_idempotency(session, idempotency_key, "runs.create", fingerprint)
    if existing is not None:
        document_service.delete_stored_file(stored.storage_key)
        return JSONResponse(
            status_code=200,
            content={
                "run": serialize_run_summary(existing),
                "idempotent_replay": True,
                "message": "This Idempotency-Key already created a run; the original is returned.",
            },
        )

    document = document_service.persist_document(session, stored)
    case = InvoiceCase(original_document_id=document.id)
    session.add(case)
    session.flush()
    run = WorkflowRun(
        case_id=case.id,
        document_id=document.id,
        trigger=trigger,
        policy_version=DEFAULT_POLICY.version,
        execution_status="QUEUED",
    )
    session.add(run)
    session.flush()
    case.current_run_id = run.id
    session.commit()

    _record_idempotency(session, idempotency_key, "runs.create", fingerprint, run)
    _queue(run)
    del request
    return JSONResponse(
        status_code=202,
        content={"run": serialize_run_summary(run), "idempotent_replay": False},
    )


@router.get("", summary="Search run history")
def list_runs(
    session: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    decision: str | None = Query(None),
    execution_status: str | None = Query(None),
    vendor: str | None = Query(None),
    search: str | None = Query(None, max_length=120),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    has_correction: bool = Query(False),
    sort: str = Query("created_at"),
    direction: str = Query("desc"),
    only_latest_per_case: bool = Query(False),
) -> dict:
    statement = select(WorkflowRun)
    filters = []
    if decision:
        filters.append(WorkflowRun.decision.in_([value.strip() for value in decision.split(",")]))
    if execution_status:
        filters.append(
            WorkflowRun.execution_status.in_(
                [value.strip() for value in execution_status.split(",")]
            )
        )
    if vendor:
        filters.append(WorkflowRun.vendor_display_name == vendor)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                WorkflowRun.invoice_number.ilike(pattern),
                WorkflowRun.vendor_display_name.ilike(pattern),
                WorkflowRun.matched_po_number.ilike(pattern),
                WorkflowRun.id.ilike(pattern),
            )
        )
    if has_correction:
        filters.append(WorkflowRun.has_human_correction.is_(True))
    if date_from:
        filters.append(WorkflowRun.created_at >= _parse_date_boundary(date_from, start=True))
    if date_to:
        filters.append(WorkflowRun.created_at <= _parse_date_boundary(date_to, start=False))
    if filters:
        statement = statement.where(*filters)

    if only_latest_per_case:
        newest = (
            select(WorkflowRun.case_id, func.max(WorkflowRun.created_at).label("newest"))
            .group_by(WorkflowRun.case_id)
            .subquery()
        )
        statement = statement.join(
            newest,
            (WorkflowRun.case_id == newest.c.case_id) & (WorkflowRun.created_at == newest.c.newest),
        )

    sort_columns = {
        "created_at": WorkflowRun.created_at,
        "completed_at": WorkflowRun.completed_at,
        "gross_total": WorkflowRun.gross_total_minor,
        "vendor": WorkflowRun.vendor_display_name,
        "invoice_number": WorkflowRun.invoice_number,
        "duration": WorkflowRun.processing_duration_ms,
    }
    column = sort_columns.get(sort, WorkflowRun.created_at)
    statement = statement.order_by(column.desc() if direction == "desc" else column.asc())

    total = (
        session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    )
    rows = list(session.scalars(statement.limit(page_size).offset((page - 1) * page_size)))
    return {
        "items": [serialize_run_summary(run) for run in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


def _parse_date_boundary(raw: str, *, start: bool) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError("invalid_date", f"{raw!r} is not an ISO date.", status_code=422) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if len(raw) == 10:
        parsed = parsed.replace(
            hour=0 if start else 23,
            minute=0 if start else 59,
            second=0 if start else 59,
            microsecond=0 if start else 999999,
        )
    return parsed


def _get_run(session: Session, run_id: str) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise ApiError("run_not_found", "No run with that identifier.", status_code=404)
    return run


@router.get("/{run_id}", summary="Durable run snapshot")
def get_run(run_id: str, session: Session = Depends(get_db)) -> dict:
    return serialize_run_detail(session, _get_run(session, run_id))


@router.get("/{run_id}/events", summary="Incremental stage events")
def get_events(
    run_id: str,
    after: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> dict:
    run = _get_run(session, run_id)
    events = list(
        session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == run_id, WorkflowEvent.sequence > after)
            .order_by(WorkflowEvent.sequence)
        )
    )
    last = session.scalar(
        select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id)
    )
    return {
        "run_id": run_id,
        "execution_status": run.execution_status,
        "decision": run.decision,
        "is_terminal": run.execution_status in {"COMPLETED", "FAILED", "INTERRUPTED"},
        "events": [serialize_event(event) for event in events],
        "last_sequence": last or 0,
    }


@router.get("/{run_id}/document", summary="Original PDF for this run")
def get_document(run_id: str, session: Session = Depends(get_db)) -> FileResponse:
    run = _get_run(session, run_id)
    document = session.get(InvoiceDocument, run.document_id)
    if document is None:
        raise ApiError("document_not_found", "The document record is missing.", status_code=404)
    path = document_service.storage_path(document.storage_key)
    if not path.exists():
        raise ApiError(
            "document_missing", "The stored PDF is no longer available.", status_code=404
        )
    # Served inline so the run page can preview it, but always as a separate
    # document: no PDF-provided markup is ever injected into our page. The app
    # default is X-Frame-Options: DENY, which also forbids same-origin framing,
    # so this one response relaxes it to SAMEORIGIN.
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Disposition": f'inline; filename="{document.safe_original_filename}"',
        },
    )


@router.get("/{run_id}/export.json", summary="Structured decision and provenance")
def export_json(run_id: str, session: Session = Depends(get_db)) -> JSONResponse:
    run = _get_run(session, run_id)
    payload = exports.build_export_json(session, run)
    return JSONResponse(
        content=json.loads(json.dumps(payload, default=str)),
        headers={
            "Content-Disposition": f'attachment; filename="clearledger-run-{run.id}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{run_id}/export.csv", summary="Flattened decision summary")
def export_csv(run_id: str, session: Session = Depends(get_db)) -> PlainTextResponse:
    run = _get_run(session, run_id)
    return PlainTextResponse(
        exports.build_export_csv(session, run),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="clearledger-run-{run.id}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{run_id}/retry", status_code=202, summary="Retry a failed or interrupted run")
def retry_run(
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> dict:
    run = _get_run(session, run_id)
    if run.execution_status not in {"FAILED", "INTERRUPTED"}:
        raise ApiError(
            "retry_not_allowed",
            f"Only failed or interrupted runs can be retried; this run is {run.execution_status}.",
            status_code=409,
        )
    fingerprint = _fingerprint("runs.retry", run.id, str(run.version))
    existing = _check_idempotency(session, idempotency_key, "runs.retry", fingerprint)
    if existing is not None:
        return {"run": serialize_run_summary(existing), "idempotent_replay": True}

    child = WorkflowRun(
        case_id=run.case_id,
        document_id=run.document_id,
        parent_run_id=run.id,
        trigger="retry",
        policy_version=DEFAULT_POLICY.version,
        execution_status="QUEUED",
        overrides=run.overrides,
    )
    session.add(child)
    session.flush()
    case = session.get(InvoiceCase, run.case_id)
    if case is not None:
        case.current_run_id = child.id
    session.commit()
    _record_idempotency(session, idempotency_key, "runs.retry", fingerprint, child)
    _queue(child)
    return {"run": serialize_run_summary(child), "idempotent_replay": False}


@router.post("/{run_id}/corrections", status_code=202, summary="Record a review action")
def create_correction(
    run_id: str,
    payload: CorrectionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> dict:
    """Create a linked child run from a reviewer's correction.

    The original run is never modified. Corrections cannot bypass a block: the
    child run re-executes every rule, so a blocked vendor, an already-approved
    identity or the cumulative limit still applies.
    """
    run = _get_run(session, run_id)
    if run.execution_status != "COMPLETED" or run.decision != "REVIEW":
        raise ApiError(
            "correction_not_allowed",
            "Only a completed run that needs review can be corrected.",
            status_code=409,
            detail=f"This run is {run.execution_status} / {run.decision}.",
        )
    if run.version != payload.expected_version:
        raise ApiError(
            "stale_run_version",
            "This run changed while the correction form was open.",
            status_code=409,
            detail=(
                f"Expected version {payload.expected_version}, current version is {run.version}."
            ),
        )
    if ledger.case_has_active_reservation(session, run.case_id):
        raise ApiError(
            "case_already_approved",
            "This case already holds an approved commitment and cannot be edited in version 1.",
            status_code=409,
            detail=(
                "Editing it could silently replace or double count an existing reservation. A "
                "revoke-and-reapprove workflow is a documented limitation, not a half-built "
                "feature."
            ),
        )
    if not payload.fields and not payload.selected_po_id and not payload.selected_vendor_id:
        raise ApiError(
            "empty_correction",
            "Supply at least one corrected field, a purchase order, or a vendor.",
            status_code=422,
        )
    if payload.selected_po_id and session.get(PurchaseOrder, payload.selected_po_id) is None:
        raise ApiError(
            "po_not_found", "The selected purchase order does not exist.", status_code=422
        )
    if payload.selected_vendor_id and session.get(Vendor, payload.selected_vendor_id) is None:
        raise ApiError("vendor_not_found", "The selected vendor does not exist.", status_code=422)
    _validate_correction_values(payload.fields)

    fingerprint = _fingerprint(
        "runs.correction",
        run.id,
        str(run.version),
        json.dumps(payload.model_dump(), sort_keys=True, default=str),
    )
    existing = _check_idempotency(session, idempotency_key, "runs.correction", fingerprint)
    if existing is not None:
        return {"run": serialize_run_summary(existing), "idempotent_replay": True}

    extraction = (run.structured_extraction or {}).get("invoice") or {}
    old_values = {name: extraction.get(name) for name in payload.fields}
    if payload.selected_po_id:
        old_values["purchase_order"] = run.matched_po_number
    if payload.selected_vendor_id:
        old_values["vendor"] = run.vendor_display_name

    child = WorkflowRun(
        case_id=run.case_id,
        document_id=run.document_id,
        parent_run_id=run.id,
        trigger="correction",
        policy_version=DEFAULT_POLICY.version,
        execution_status="QUEUED",
        overrides={
            "fields": payload.fields,
            "selected_po_id": payload.selected_po_id,
            "selected_vendor_id": payload.selected_vendor_id,
            "actor_label": payload.actor_label,
            "reason": payload.reason,
            "source_run_id": run.id,
        },
    )
    session.add(child)
    session.flush()

    action = ReviewAction(
        case_id=run.case_id,
        source_run_id=run.id,
        derived_run_id=child.id,
        actor_label=payload.actor_label,
        reason=payload.reason,
        old_values=old_values,
        proposed_values={
            **payload.fields,
            **({"purchase_order_id": payload.selected_po_id} if payload.selected_po_id else {}),
            **({"vendor_id": payload.selected_vendor_id} if payload.selected_vendor_id else {}),
        },
    )
    session.add(action)
    case = session.get(InvoiceCase, run.case_id)
    if case is not None:
        case.current_run_id = child.id
    run.version += 1
    session.commit()

    _record_idempotency(session, idempotency_key, "runs.correction", fingerprint, child)
    _queue(child)
    return {
        "run": serialize_run_summary(child),
        "review_action_id": action.id,
        "idempotent_replay": False,
    }


def _validate_correction_values(fields: dict[str, str]) -> None:
    """Server-side validation of reviewer input; the client is never trusted."""
    from app.domain.money import MoneyError, normalize_currency, parse_decimal
    from app.domain.normalization import parse_invoice_date

    problems: list[str] = []
    for name in ("subtotal", "tax_total", "gross_total"):
        if name in fields:
            try:
                value = parse_decimal(fields[name])
            except MoneyError as exc:
                problems.append(f"{name}: {exc}")
                continue
            if name == "gross_total" and value <= 0:
                problems.append("gross_total: must be a positive amount.")
    if "currency" in fields and not normalize_currency(fields["currency"]):
        problems.append("currency: use an ISO code such as INR.")
    if "invoice_date" in fields:
        parsed = parse_invoice_date(fields["invoice_date"])
        if parsed.value is None:
            problems.append(f"invoice_date: {parsed.reason or 'not a valid date'}.")
    if "invoice_number" in fields and len(fields["invoice_number"]) > 64:
        problems.append("invoice_number: longer than 64 characters.")
    if problems:
        raise ApiError(
            "invalid_correction",
            "The corrected values were rejected.",
            status_code=422,
            detail="; ".join(problems),
        )


@router.get("/{run_id}/duplicate-of", summary="Original case for a duplicate decision")
def duplicate_of(run_id: str, session: Session = Depends(get_db)) -> dict:
    run = _get_run(session, run_id)
    payload = run.decision_payload or {}
    return {
        "duplicate_file": payload.get("duplicate_file"),
        "duplicate_identity": payload.get("duplicate_identity"),
    }

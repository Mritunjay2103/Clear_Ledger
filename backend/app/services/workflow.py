"""The durable processing pipeline.

Stage events are written as stages actually happen, so the live view reflects
real execution rather than a UI animation. Every started stage terminates:
either it completes, it fails with a specific code, or startup recovery marks it
interrupted.

The decision and its reservation are committed inside one immediate write
transaction that re-reads commitments and re-checks duplicates first, which is
what makes concurrent approvals safe.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    InvoiceCase,
    InvoiceDocument,
    PurchaseOrder,
    Vendor,
    WorkflowEvent,
    WorkflowRun,
)
from app.db.session import immediate_transaction, session_scope
from app.domain import matching
from app.domain.money import display_minor
from app.domain.policy import (
    DEFAULT_POLICY,
    PolicyInputs,
    PoSnapshot,
    VendorSnapshot,
    evaluate,
)
from app.providers.base import ExtractionError
from app.schemas.invoice import Evidence, ExtractedInvoice, ExtractionResult
from app.services import documents as document_service
from app.services import extraction as extraction_service
from app.services import ledger
from app.services.pdf_text import PdfError, extract_text

logger = logging.getLogger("clearledger.workflow")

STAGES: tuple[tuple[str, str], ...] = (
    ("intake", "Intake"),
    ("read_document", "Read document"),
    ("extract_fields", "Extract fields"),
    ("validate_facts", "Validate facts"),
    ("match_references", "Match references"),
    ("evaluate_policy", "Evaluate policy"),
    ("commit_decision", "Commit decision"),
    ("publish_output", "Publish output"),
)
STAGE_LABELS = dict(STAGES)

CORRECTABLE_FIELDS = (
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax_total",
    "gross_total",
    "vendor_name",
)


class WorkflowFailure(Exception):
    """A technical failure. Never presented as a business rejection."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class StageTimer:
    key: str
    started: float


class RunExecutor:
    def __init__(self, session: Session, run: WorkflowRun) -> None:
        self.session = session
        self.run = run
        self._sequence = (
            session.scalar(
                select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run.id)
            )
            or 0
        )

    # -- events -----------------------------------------------------------

    def emit(
        self,
        stage_key: str,
        event_type: str,
        message: str,
        metadata: dict | None = None,
    ) -> None:
        self._sequence += 1
        event = WorkflowEvent(
            run_id=self.run.id,
            sequence=self._sequence,
            stage_key=stage_key,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            short_message=message,
            structured_metadata=metadata,
        )
        self.session.add(event)
        self.session.commit()

    @contextmanager
    def stage(self, key: str, start_message: str) -> Iterator[StageTimer]:
        self.emit(key, "started", start_message)
        timer = StageTimer(key=key, started=time.perf_counter())
        try:
            yield timer
        except Exception:
            raise

    def complete_stage(self, timer: StageTimer, message: str, metadata: dict | None = None) -> None:
        payload = dict(metadata or {})
        payload["elapsed_ms"] = int((time.perf_counter() - timer.started) * 1000)
        self.emit(timer.key, "completed", message, payload)

    def skip_stage(self, key: str, reason: str) -> None:
        self.emit(key, "skipped", reason)

    def warn(self, key: str, message: str, metadata: dict | None = None) -> None:
        self.emit(key, "warning", message, metadata)


def execute_run(run_id: str) -> None:
    """Entry point for the worker. Owns its own database session."""
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            logger.warning("run %s vanished before execution", run_id)
            return
        if run.execution_status not in {"QUEUED", "RUNNING"}:
            return
        executor = RunExecutor(session, run)
        try:
            _run_pipeline(session, run, executor)
        except WorkflowFailure as failure:
            _mark_failed(session, run, executor, failure)
        except Exception as exc:  # unexpected: still recorded honestly
            logger.exception("unhandled error in run %s", run_id)
            _mark_failed(
                session,
                run,
                executor,
                WorkflowFailure(
                    "internal_error",
                    "The run stopped because of an unexpected internal error.",
                ),
            )
            del exc


def _mark_failed(
    session: Session, run: WorkflowRun, executor: RunExecutor, failure: WorkflowFailure
) -> None:
    run.execution_status = "FAILED"
    run.decision = None
    run.error_code = failure.code
    run.error_message = failure.message
    run.completed_at = datetime.now(UTC)
    if run.started_at:
        run.processing_duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
    run.version += 1
    session.commit()
    executor.emit(
        "publish_output",
        "failed",
        failure.message,
        {"error_code": failure.code, "retryable": failure.retryable},
    )


def _run_pipeline(session: Session, run: WorkflowRun, executor: RunExecutor) -> None:
    run.execution_status = "RUNNING"
    run.started_at = run.started_at or datetime.now(UTC)
    session.commit()

    case = session.get(InvoiceCase, run.case_id)
    document = session.get(InvoiceDocument, run.document_id)
    if case is None or document is None:
        raise WorkflowFailure(
            "record_missing", "The case or document record is missing.", retryable=False
        )

    overrides = dict(run.overrides or {})
    override_fields: dict[str, str] = dict(overrides.get("fields") or {})
    selected_po_id: str | None = overrides.get("selected_po_id")
    selected_vendor_id: str | None = overrides.get("selected_vendor_id")
    actor_label: str = overrides.get("actor_label") or "reviewer"

    # 1. Intake -----------------------------------------------------------
    with executor.stage("intake", "Validating and recording the uploaded document") as timer:
        path = document_service.storage_path(document.storage_key)
        if not path.exists():
            raise WorkflowFailure(
                "document_missing",
                "The stored PDF for this case is no longer on disk.",
                retryable=False,
            )
        executor.complete_stage(
            timer,
            f"{document.safe_original_filename} accepted "
            f"({document.byte_count:,} bytes, {document.page_count} page(s))",
            {
                "sha256": document.sha256,
                "page_count": document.page_count,
                "byte_count": document.byte_count,
                "trigger": run.trigger,
            },
        )

    # 2. Read document ----------------------------------------------------
    with executor.stage("read_document", "Reading the PDF text layer") as timer:
        try:
            parsed = extract_text(path)
        except PdfError as exc:
            raise WorkflowFailure(exc.code, exc.message, retryable=False) from exc
        pages_needing_ocr = [page.page_number for page in parsed.pages if page.needs_ocr]
        if pages_needing_ocr:
            executor.warn(
                "read_document",
                f"Page(s) {', '.join(map(str, pages_needing_ocr))} have no usable text layer; "
                "attempting OCR.",
                {"pages": pages_needing_ocr},
            )
        read_result = extraction_service.read_document(path, parsed)
        for warning in read_result.warnings:
            executor.warn("read_document", warning)
        if not read_result.readable:
            raise WorkflowFailure(
                "document_unreadable",
                "No text could be read from this document, with or without OCR. "
                + (read_result.warnings[0] if read_result.warnings else ""),
            )
        executor.complete_stage(
            timer,
            (
                f"Read {len(read_result.page_texts)} page(s); "
                + (
                    f"OCR used on page(s) {', '.join(map(str, read_result.ocr_pages))}"
                    if read_result.ocr_pages
                    else "text layer used for every page"
                )
            ),
            {
                "pages": len(read_result.page_texts),
                "ocr_pages": read_result.ocr_pages,
                "characters": read_result.total_characters,
                "reader": parsed.reader,
            },
        )

    # 3. Extract fields ---------------------------------------------------
    parent_extraction: dict | None = None
    if run.parent_run_id:
        parent = session.get(WorkflowRun, run.parent_run_id)
        parent_extraction = (parent.structured_extraction or {}).get("invoice") if parent else None

    with executor.stage("extract_fields", "Extracting invoice facts") as timer:
        if parent_extraction is not None:
            invoice = ExtractedInvoice.model_validate(parent_extraction)
            metadata_source = (
                session.get(WorkflowRun, run.parent_run_id).structured_extraction or {}
            ).get("metadata") or {}
            result = ExtractionResult.model_validate(
                {"invoice": invoice.model_dump(), "metadata": metadata_source}
            )
            result.metadata.notes = [
                *list(result.metadata.notes),
                "Reused the original extraction from the parent run; only the reviewer's "
                "explicit overrides were changed.",
            ]
            executor.complete_stage(
                timer,
                "Reused the parent run's extracted facts for this correction",
                {"parent_run_id": run.parent_run_id},
            )
        else:
            try:
                result = extraction_service.run_extraction(read_result)
            except ExtractionError as exc:
                raise WorkflowFailure(exc.code, exc.message) from exc
            invoice = result.invoice
            if result.metadata.fallback_from:
                executor.warn(
                    "extract_fields",
                    f"Provider {result.metadata.fallback_from!r} failed "
                    f"({result.metadata.fallback_reason}); the deterministic parser was used "
                    "because ALLOW_RULES_FALLBACK is enabled.",
                )
            for warning in invoice.extraction_warnings:
                executor.warn("extract_fields", warning)
            executor.complete_stage(
                timer,
                (
                    f"{_provider_label(result.metadata.provider)} produced "
                    f"{len(invoice.populated_critical_fields())} of 5 critical fields"
                ),
                {
                    "provider": result.metadata.provider,
                    "model": result.metadata.model,
                    "prompt_hash": result.metadata.prompt_hash,
                    "repair_attempted": result.metadata.repair_attempted,
                    "duration_ms": result.metadata.duration_ms,
                },
            )

    invoice = result.invoice
    if override_fields:
        _apply_overrides(invoice, override_fields, actor_label)
        executor.warn(
            "extract_fields",
            "A reviewer supplied "
            + ", ".join(sorted(override_fields))
            + "; these values are labelled human-supplied, not extracted.",
            {"fields": sorted(override_fields), "actor": actor_label},
        )

    run.extraction_provider = result.metadata.provider
    run.actual_model = result.metadata.model
    run.extraction_warnings = list(invoice.extraction_warnings) + list(read_result.warnings)
    run.structured_extraction = {
        "invoice": invoice.model_dump(mode="json"),
        "metadata": result.metadata.model_dump(mode="json"),
        "page_texts": read_result.page_texts,
        "page_provenance": read_result.page_provenance,
    }
    session.commit()

    # 4. Validate facts ---------------------------------------------------
    with executor.stage("validate_facts", "Checking values against the document") as timer:
        qualities, evidence_warnings = extraction_service.verify_evidence(invoice, read_result)
        for warning in evidence_warnings:
            executor.warn("validate_facts", warning)
        verified = sum(1 for quality in qualities if quality.label == "verified")
        executor.complete_stage(
            timer,
            f"{verified} of {len(qualities)} critical field(s) verified against the document text",
            {"qualities": [quality.model_dump(mode="json") for quality in qualities]},
        )

    # 5. Match references -------------------------------------------------
    with executor.stage("match_references", "Resolving vendor and purchase order") as timer:
        vendor_resolution = matching.resolve_vendor(session, invoice.vendor_name)
        if selected_vendor_id:
            chosen = session.get(Vendor, selected_vendor_id)
            if chosen is not None:
                vendor_resolution = matching.VendorResolution(
                    vendor=chosen,
                    matched_on="human_selection",
                    confident=True,
                    reason=f"{chosen.canonical_name} was confirmed by {actor_label} during review.",
                    candidates=vendor_resolution.candidates,
                )

        facts_currency = invoice.currency
        gross_minor = _safe_minor(invoice.gross_total, facts_currency)
        po_resolution = matching.resolve_purchase_order(
            session,
            printed_po_number=invoice.explicit_po_number,
            vendor=vendor_resolution.vendor,
            invoice_currency=(facts_currency or "INR").upper() if facts_currency else None,
            invoice_gross_minor=gross_minor,
            selected_po_id=selected_po_id,
            exclude_case_id=run.case_id,
        )
        matched_vendor = vendor_resolution.vendor
        matched_po = po_resolution.purchase_order
        executor.complete_stage(
            timer,
            f"Vendor: {matched_vendor.canonical_name if matched_vendor else 'unresolved'}"
            f" · Purchase order: {matched_po.po_number if matched_po else 'unresolved'}",
            {
                "vendor_confident": vendor_resolution.confident,
                "vendor_reason": vendor_resolution.reason,
                "po_source": po_resolution.source,
                "po_reason": po_resolution.reason,
                "po_candidates": [candidate.__dict__ for candidate in po_resolution.candidates],
            },
        )

    # 6. Evaluate policy --------------------------------------------------
    with executor.stage("evaluate_policy", "Applying policy rules") as timer:
        evaluation = _evaluate(
            session,
            run=run,
            document=document,
            invoice=invoice,
            qualities=qualities,
            vendor_resolution=vendor_resolution,
            po_resolution=po_resolution,
        )
        executor.complete_stage(
            timer,
            f"{len(evaluation.rule_results)} rules evaluated · provisional outcome "
            f"{evaluation.decision}",
            {
                "decision": evaluation.decision,
                "blocking_codes": evaluation.blocking_codes,
                "review_codes": evaluation.review_codes,
            },
        )

    # 7. Commit decision --------------------------------------------------
    with executor.stage("commit_decision", "Recording the decision and any commitment") as timer:
        final = _commit_decision(
            session,
            run=run,
            document=document,
            invoice=invoice,
            qualities=qualities,
            vendor_resolution=vendor_resolution,
            po_resolution=po_resolution,
        )
        executor.complete_stage(
            timer,
            final["message"],
            {
                "decision": final["decision"],
                "reserved_minor": final["reserved_minor"],
                "reservation_id": final["reservation_id"],
            },
        )

    # 8. Publish output ---------------------------------------------------
    with executor.stage("publish_output", "Publishing the decision report") as timer:
        executor.complete_stage(
            timer,
            "Decision report and downloadable exports are available.",
            {"exports": ["export.json", "export.csv"]},
        )


def _provider_label(provider: str) -> str:
    return {
        "rules": "Rules-based extraction",
        "ollama": "Local model extraction (Ollama)",
        "openai_compatible": "Hosted model extraction",
    }.get(provider, provider)


def _safe_minor(raw: str | None, currency: str | None) -> int | None:
    from app.domain.money import MoneyError, normalize_currency, parse_decimal, to_minor

    code = normalize_currency(currency) or "INR"
    if raw in (None, ""):
        return None
    try:
        return to_minor(parse_decimal(raw), code)
    except MoneyError:
        return None


def _apply_overrides(
    invoice: ExtractedInvoice, override_fields: dict[str, str], actor_label: str
) -> None:
    """Write reviewer-supplied values in, labelled as human-supplied.

    Original PDF-derived evidence for untouched fields is preserved; only the
    corrected fields get human provenance.
    """
    for name, value in override_fields.items():
        if name not in CORRECTABLE_FIELDS:
            continue
        setattr(invoice, name, value)
        invoice.evidence[name] = Evidence(
            page=1,
            excerpt=f"Supplied by {actor_label} during review: {value}",
            provenance="human_correction",
        )
    invoice.missing_fields = [
        field for field in invoice.missing_fields if field not in override_fields
    ]


def _build_inputs(
    session: Session,
    *,
    run: WorkflowRun,
    document: InvoiceDocument,
    invoice: ExtractedInvoice,
    qualities,
    vendor_resolution,
    po_resolution,
) -> PolicyInputs:
    vendor = vendor_resolution.vendor
    vendor_snapshot = (
        VendorSnapshot(
            id=vendor.id,
            canonical_name=vendor.canonical_name,
            status=vendor.status,
            supported_currency=vendor.supported_currency,
        )
        if vendor is not None
        else None
    )

    purchase_order = po_resolution.purchase_order
    po_snapshot = None
    committed_before = 0
    if purchase_order is not None:
        po_vendor = session.get(Vendor, purchase_order.vendor_id)
        po_snapshot = PoSnapshot(
            id=purchase_order.id,
            po_number=purchase_order.po_number,
            vendor_id=purchase_order.vendor_id,
            vendor_name=po_vendor.canonical_name if po_vendor else "unknown vendor",
            currency=purchase_order.currency,
            approved_total_minor=purchase_order.approved_total_minor,
            status=purchase_order.status,
        )
        committed_before = matching.committed_minor_for_po(
            session, purchase_order.id, exclude_case_id=run.case_id
        )

    from app.domain.normalization import normalize_invoice_number

    duplicate_file = ledger.find_duplicate_file(
        session, sha256=document.sha256, current_case_id=run.case_id
    )
    duplicate_identity = None
    if vendor_snapshot is not None and vendor_resolution.confident:
        duplicate_identity = ledger.find_duplicate_identity(
            session,
            vendor_id=vendor_snapshot.id,
            normalized_invoice_number=normalize_invoice_number(invoice.invoice_number),
            current_case_id=run.case_id,
        )

    return PolicyInputs(
        invoice=invoice,
        qualities=qualities,
        vendor=vendor_snapshot,
        vendor_confident=vendor_resolution.confident,
        vendor_reason=vendor_resolution.reason,
        vendor_candidates=[candidate.__dict__ for candidate in vendor_resolution.candidates],
        purchase_order=po_snapshot,
        po_source=po_resolution.source,
        po_reference_found=po_resolution.reference_found,
        po_reference_raw=po_resolution.reference_raw,
        po_reference_exists=po_resolution.reference_exists,
        po_reason=po_resolution.reason,
        po_candidates=[candidate.__dict__ for candidate in po_resolution.candidates],
        committed_before_minor=committed_before,
        duplicate_file=duplicate_file,
        duplicate_identity=duplicate_identity,
        case_already_approved=ledger.case_has_active_reservation(session, run.case_id),
        extraction_warnings=list(invoice.extraction_warnings),
        config=DEFAULT_POLICY,
    )


def _evaluate(session: Session, **kwargs):
    return evaluate(_build_inputs(session, **kwargs))


def _commit_decision(
    session: Session,
    *,
    run: WorkflowRun,
    document: InvoiceDocument,
    invoice: ExtractedInvoice,
    qualities,
    vendor_resolution,
    po_resolution,
) -> dict:
    """Re-evaluate and persist inside one immediate write transaction.

    The evaluation is repeated here rather than reused: between the earlier
    policy stage and this moment another run may have reserved funds against the
    same purchase order or approved the same invoice identity.
    """
    reservation_id: str | None = None
    reserved_minor: int | None = None

    with immediate_transaction(session):
        inputs = _build_inputs(
            session,
            run=run,
            document=document,
            invoice=invoice,
            qualities=qualities,
            vendor_resolution=vendor_resolution,
            po_resolution=po_resolution,
        )
        evaluation = evaluate(inputs)

        if evaluation.decision == "APPROVED":
            purchase_order = po_resolution.purchase_order
            vendor = vendor_resolution.vendor
            from app.domain.normalization import normalize_invoice_number

            assert purchase_order is not None and vendor is not None
            amount_minor = evaluation.facts.gross_total_minor
            assert amount_minor is not None
            try:
                reservation = ledger.create_reservation(
                    session,
                    case_id=run.case_id,
                    run_id=run.id,
                    vendor_id=vendor.id,
                    normalized_invoice_number=normalize_invoice_number(invoice.invoice_number),
                    invoice_number_raw=invoice.invoice_number or "",
                    po_id=purchase_order.id,
                    amount_minor=amount_minor,
                    currency=evaluation.facts.currency or purchase_order.currency,
                )
            except IntegrityError as exc:
                session.rollback()
                raise WorkflowFailure(
                    "reservation_conflict",
                    "Another run reserved this invoice identity or case at the same moment. "
                    "Retry to re-evaluate against the committed state.",
                ) from exc
            reservation_id = reservation.id
            reserved_minor = amount_minor

        completed_at = datetime.now(UTC)
        run.execution_status = "COMPLETED"
        run.decision = evaluation.decision
        run.completed_at = completed_at
        run.processing_duration_ms = (
            int((completed_at - run.started_at).total_seconds() * 1000) if run.started_at else None
        )
        run.rule_results = [result.model_dump(mode="json") for result in evaluation.rule_results]
        run.decision_payload = {
            "outcome": evaluation.outcome.model_dump(mode="json"),
            "facts": evaluation.facts.model_dump(mode="json"),
            "po_comparison": evaluation.po_comparison.model_dump(mode="json"),
            "vendor_resolution": {
                "confident": vendor_resolution.confident,
                "matched_on": vendor_resolution.matched_on,
                "reason": vendor_resolution.reason,
                "vendor": inputs.vendor.__dict__ if inputs.vendor else None,
                "candidates": inputs.vendor_candidates,
            },
            "po_resolution": {
                "source": po_resolution.source,
                "reason": po_resolution.reason,
                "reference_found": po_resolution.reference_found,
                "reference_raw": po_resolution.reference_raw,
                "purchase_order": inputs.purchase_order.__dict__ if inputs.purchase_order else None,
                "candidates": inputs.po_candidates,
            },
            "field_quality": [quality.model_dump(mode="json") for quality in qualities],
            "reservation_id": reservation_id,
            "duplicate_file": inputs.duplicate_file.__dict__ if inputs.duplicate_file else None,
            "duplicate_identity": (
                inputs.duplicate_identity.__dict__ if inputs.duplicate_identity else None
            ),
        }
        run.policy_snapshot = DEFAULT_POLICY.as_snapshot()
        run.reference_snapshot = _reference_snapshot(session, inputs)
        run.invoice_number = evaluation.facts.invoice_number
        run.vendor_display_name = (
            inputs.vendor.canonical_name if inputs.vendor else invoice.vendor_name
        )
        run.gross_total_minor = evaluation.facts.gross_total_minor
        run.currency = evaluation.facts.currency
        run.matched_po_number = evaluation.po_comparison.po_number
        run.has_human_correction = bool((run.overrides or {}).get("fields")) or bool(
            (run.overrides or {}).get("selected_po_id")
        )
        run.error_code = None
        run.error_message = None
        run.version += 1

        case = session.get(InvoiceCase, run.case_id)
        if case is not None:
            case.current_run_id = run.id

    currency = run.currency or "INR"
    if reserved_minor is not None:
        message = (
            f"APPROVED · reserved {display_minor(reserved_minor, currency)} against "
            f"{run.matched_po_number}"
        )
    else:
        message = f"{run.decision} · no commitment reserved"
    return {
        "decision": run.decision,
        "message": message,
        "reserved_minor": reserved_minor,
        "reservation_id": reservation_id,
    }


def _reference_snapshot(session: Session, inputs: PolicyInputs) -> dict:
    """Record the reference data the decision actually depended on."""
    snapshot: dict = {"captured_at": datetime.now(UTC).isoformat()}
    if inputs.vendor:
        snapshot["vendor"] = inputs.vendor.__dict__
    if inputs.purchase_order:
        purchase_order = session.get(PurchaseOrder, inputs.purchase_order.id)
        snapshot["purchase_order"] = {
            **inputs.purchase_order.__dict__,
            "description": purchase_order.description if purchase_order else None,
            "committed_before_minor": inputs.committed_before_minor,
        }
    snapshot["settings"] = {
        "extraction_provider": settings.extraction_provider,
        "allow_rules_fallback": settings.allow_rules_fallback,
        "max_upload_mb": settings.max_upload_mb,
        "max_pdf_pages": settings.max_pdf_pages,
    }
    return snapshot

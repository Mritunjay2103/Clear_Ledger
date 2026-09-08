"""Duplicate detection and the approval reservation ledger.

A reservation is a commitment created by an APPROVED decision. It is not a
payment: nothing in this prototype moves money. The ledger is what makes split
invoices add up and what stops the same invoice identity being approved twice.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApprovalReservation, InvoiceCase, InvoiceDocument, WorkflowRun
from app.domain.policy import DuplicateHit


def find_duplicate_file(
    session: Session, *, sha256: str, current_case_id: str
) -> DuplicateHit | None:
    """Find a previous case built from byte-identical content.

    A case whose only runs failed technically is not duplicate evidence — the
    invoice was never actually processed, so the user is entitled to retry it as
    a fresh intake.
    """
    documents = list(
        session.scalars(select(InvoiceDocument).where(InvoiceDocument.sha256 == sha256))
    )
    if not documents:
        return None
    document_ids = [document.id for document in documents]

    candidates = list(
        session.scalars(
            select(InvoiceCase)
            .where(
                InvoiceCase.original_document_id.in_(document_ids),
                InvoiceCase.id != current_case_id,
            )
            .order_by(InvoiceCase.created_at)
        )
    )
    for case in candidates:
        decided = session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.case_id == case.id,
                WorkflowRun.execution_status == "COMPLETED",
                WorkflowRun.decision.is_not(None),
            )
            .order_by(WorkflowRun.completed_at.desc())
        )
        if decided is None:
            continue
        return DuplicateHit(
            case_id=case.id,
            run_id=decided.id,
            kind="identical_file",
            detail=(
                f"The same file was processed on {decided.completed_at:%Y-%m-%d %H:%M UTC} and "
                f"decided {decided.decision}."
            ),
            amount_minor=decided.gross_total_minor,
            invoice_number=decided.invoice_number,
        )
    return None


def find_duplicate_identity(
    session: Session,
    *,
    vendor_id: str,
    normalized_invoice_number: str,
    current_case_id: str,
) -> DuplicateHit | None:
    """Find an active approved reservation for the same vendor + invoice number.

    The current case is excluded so a correction on the same case is not treated
    as a second invoice from the same vendor.
    """
    if not vendor_id or not normalized_invoice_number:
        return None
    reservation = session.scalar(
        select(ApprovalReservation).where(
            ApprovalReservation.vendor_id == vendor_id,
            ApprovalReservation.normalized_invoice_number == normalized_invoice_number,
            ApprovalReservation.active.is_(True),
            ApprovalReservation.case_id != current_case_id,
        )
    )
    if reservation is None:
        return None
    return DuplicateHit(
        case_id=reservation.case_id,
        run_id=reservation.run_id,
        kind="approved_identity",
        detail=(
            f"Invoice {reservation.invoice_number_raw} from this vendor was approved on "
            f"{reservation.created_at:%Y-%m-%d %H:%M UTC} for "
            f"{reservation.amount_minor / 100:.2f} {reservation.currency}."
        ),
        amount_minor=reservation.amount_minor,
        invoice_number=reservation.invoice_number_raw,
    )


def case_has_active_reservation(session: Session, case_id: str) -> bool:
    return (
        session.scalar(
            select(ApprovalReservation.id).where(
                ApprovalReservation.case_id == case_id,
                ApprovalReservation.active.is_(True),
            )
        )
        is not None
    )


def create_reservation(
    session: Session,
    *,
    case_id: str,
    run_id: str,
    vendor_id: str,
    normalized_invoice_number: str,
    invoice_number_raw: str,
    po_id: str,
    amount_minor: int,
    currency: str,
) -> ApprovalReservation:
    reservation = ApprovalReservation(
        case_id=case_id,
        run_id=run_id,
        vendor_id=vendor_id,
        normalized_invoice_number=normalized_invoice_number,
        invoice_number_raw=invoice_number_raw,
        po_id=po_id,
        amount_minor=amount_minor,
        currency=currency,
        active=True,
    )
    session.add(reservation)
    session.flush()
    return reservation

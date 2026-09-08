"""The commitment ledger: the part where a bug becomes a double payment."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ApprovalReservation,
    InvoiceCase,
    InvoiceDocument,
    PurchaseOrder,
    Vendor,
    WorkflowRun,
)
from app.domain import matching
from app.services import ledger


def _case(session, *, sha256: str = "a" * 64) -> tuple[InvoiceCase, WorkflowRun]:
    document = InvoiceDocument(
        sha256=sha256,
        storage_key=f"{sha256}.pdf",
        safe_original_filename="invoice.pdf",
        byte_count=1024,
        page_count=1,
    )
    session.add(document)
    session.flush()
    case = InvoiceCase(original_document_id=document.id)
    session.add(case)
    session.flush()
    run = WorkflowRun(
        case_id=case.id,
        document_id=document.id,
        trigger="upload",
        policy_version="v1",
        execution_status="COMPLETED",
        decision="APPROVED",
    )
    session.add(run)
    session.flush()
    return case, run


def _vendor_and_po(session) -> tuple[Vendor, PurchaseOrder]:
    vendor = session.scalars(select(Vendor).where(Vendor.status == "approved")).first()
    po = session.scalars(select(PurchaseOrder).where(PurchaseOrder.vendor_id == vendor.id)).first()
    return vendor, po


def test_commitments_against_one_po_add_up(seeded_session) -> None:
    session = seeded_session
    vendor, po = _vendor_and_po(session)

    for index, amount in enumerate((40_000_00, 30_000_00)):
        case, run = _case(session, sha256=f"{index:064d}")
        ledger.create_reservation(
            session,
            case_id=case.id,
            run_id=run.id,
            vendor_id=vendor.id,
            normalized_invoice_number=f"inv{index}",
            invoice_number_raw=f"INV-{index}",
            po_id=po.id,
            amount_minor=amount,
            currency=po.currency,
        )
    session.commit()

    assert matching.committed_minor_for_po(session, po.id) == 70_000_00


def test_an_inactive_reservation_stops_counting(seeded_session) -> None:
    session = seeded_session
    vendor, po = _vendor_and_po(session)
    case, run = _case(session)
    reservation = ledger.create_reservation(
        session,
        case_id=case.id,
        run_id=run.id,
        vendor_id=vendor.id,
        normalized_invoice_number="inv1",
        invoice_number_raw="INV-1",
        po_id=po.id,
        amount_minor=10_000_00,
        currency=po.currency,
    )
    session.commit()
    assert matching.committed_minor_for_po(session, po.id) == 10_000_00

    reservation.active = False
    session.commit()
    assert matching.committed_minor_for_po(session, po.id) == 0


def test_the_database_itself_refuses_a_second_commitment_for_one_case(
    seeded_session,
) -> None:
    # Belt and braces behind the policy check: even a logic bug upstream cannot
    # write two live commitments for the same invoice case.
    session = seeded_session
    vendor, po = _vendor_and_po(session)
    case, run = _case(session)

    def reserve(suffix: str) -> None:
        ledger.create_reservation(
            session,
            case_id=case.id,
            run_id=run.id,
            vendor_id=vendor.id,
            normalized_invoice_number=f"inv{suffix}",
            invoice_number_raw=f"INV-{suffix}",
            po_id=po.id,
            amount_minor=1_000_00,
            currency=po.currency,
        )

    reserve("a")
    session.commit()
    with pytest.raises(IntegrityError):
        reserve("b")
        session.commit()


def test_the_database_refuses_two_live_commitments_for_one_invoice_identity(
    seeded_session,
) -> None:
    session = seeded_session
    vendor, po = _vendor_and_po(session)

    def reserve(index: int) -> None:
        case, run = _case(session, sha256=f"{index:064d}")
        ledger.create_reservation(
            session,
            case_id=case.id,
            run_id=run.id,
            vendor_id=vendor.id,
            normalized_invoice_number="inv-1001",
            invoice_number_raw="INV-1001",
            po_id=po.id,
            amount_minor=1_000_00,
            currency=po.currency,
        )

    reserve(0)
    session.commit()
    with pytest.raises(IntegrityError):
        reserve(1)
        session.commit()


def test_an_approved_identity_is_reported_as_a_duplicate(seeded_session) -> None:
    session = seeded_session
    vendor, po = _vendor_and_po(session)
    case, run = _case(session)
    ledger.create_reservation(
        session,
        case_id=case.id,
        run_id=run.id,
        vendor_id=vendor.id,
        normalized_invoice_number="inv1001",
        invoice_number_raw="INV-1001",
        po_id=po.id,
        amount_minor=1_000_00,
        currency=po.currency,
    )
    session.commit()

    other_case, _ = _case(session, sha256="b" * 64)
    session.commit()

    hit = ledger.find_duplicate_identity(
        session,
        vendor_id=vendor.id,
        normalized_invoice_number="inv1001",
        current_case_id=other_case.id,
    )
    assert hit is not None
    assert hit.kind == "approved_identity"

    # The same case looking at its own reservation is not a duplicate; that is
    # what makes a correction on an existing case possible.
    assert (
        ledger.find_duplicate_identity(
            session,
            vendor_id=vendor.id,
            normalized_invoice_number="inv1001",
            current_case_id=case.id,
        )
        is None
    )


def test_a_case_that_only_failed_technically_is_not_duplicate_evidence(
    seeded_session,
) -> None:
    session = seeded_session
    _, run = _case(session)
    run.execution_status = "FAILED"
    run.decision = None
    session.commit()

    other_case, _ = _case(session, sha256="c" * 64)
    session.commit()

    hit = ledger.find_duplicate_file(
        session,
        sha256="a" * 64,
        current_case_id=other_case.id,
    )
    assert hit is None, "a technical failure must not block a genuine retry"


def test_two_threads_racing_to_commit_produce_exactly_one_reservation(
    seeded_session,
) -> None:
    """The reservation write is serialised, not optimistic.

    Two workers deciding the same invoice at the same moment must not both
    write a live commitment. The unique partial index plus an immediate write
    transaction is what enforces that; this proves the pair actually holds
    under concurrency rather than only in review.
    """
    from app.db.session import get_session_factory, immediate_transaction

    session = seeded_session
    vendor, po = _vendor_and_po(session)
    case, run = _case(session)
    session.commit()
    case_id, run_id, vendor_id, po_id, currency = (
        case.id,
        run.id,
        vendor.id,
        po.id,
        po.currency,
    )

    factory = get_session_factory()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                with immediate_transaction(db):
                    existing = db.scalar(
                        select(ApprovalReservation).where(
                            ApprovalReservation.case_id == case_id,
                            ApprovalReservation.active.is_(True),
                        )
                    )
                    if existing is not None:
                        raise RuntimeError("already reserved")
                    ledger.create_reservation(
                        db,
                        case_id=case_id,
                        run_id=run_id,
                        vendor_id=vendor_id,
                        normalized_invoice_number="race1001",
                        invoice_number_raw="INV-RACE",
                        po_id=po_id,
                        amount_minor=5_000_00,
                        currency=currency,
                    )
                result = "committed"
            except Exception as exc:
                result = type(exc).__name__
            with lock:
                outcomes.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("committed") == 1, outcomes
    session.expire_all()
    live = session.scalars(
        select(ApprovalReservation).where(
            ApprovalReservation.case_id == case_id,
            ApprovalReservation.active.is_(True),
        )
    ).all()
    assert len(live) == 1

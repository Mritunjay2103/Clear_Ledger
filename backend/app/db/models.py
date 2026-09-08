"""SQLAlchemy models.

Money is stored exclusively as integer minor units (paise). Public identifiers
are UUID strings and every timestamp is timezone-aware UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(DateTime):
    """DateTime that always round-trips as timezone-aware UTC."""

    def __init__(self) -> None:
        super().__init__(timezone=True)


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    explicit_aliases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    supported_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    vendor_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)

    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="vendor")

    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_vendors_normalized_name"),
        CheckConstraint("status in ('approved','blocked')", name="ck_vendors_status"),
    )


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_po_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    approved_total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)

    vendor: Mapped[Vendor] = relationship(back_populates="purchase_orders")

    __table_args__ = (
        UniqueConstraint("normalized_po_number", name="uq_po_normalized_number"),
        CheckConstraint("status in ('open','closed')", name="ck_po_status"),
        CheckConstraint("approved_total_minor >= 0", name="ck_po_total_non_negative"),
    )


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    safe_original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)


class InvoiceCase(Base):
    __tablename__ = "invoice_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_document_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_documents.id"), nullable=False
    )
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)

    document: Mapped[InvoiceDocument] = relationship()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("invoice_cases.id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("invoice_documents.id"), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    processing_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extraction_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    reference_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    structured_extraction: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rule_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decision_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Denormalised for history filtering and the dashboard.
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vendor_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gross_total_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    matched_po_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    has_human_correction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "execution_status in ('QUEUED','RUNNING','COMPLETED','FAILED','INTERRUPTED')",
            name="ck_run_execution_status",
        ),
        CheckConstraint(
            "decision is null or decision in ('APPROVED','REVIEW','BLOCKED')",
            name="ck_run_decision",
        ),
        Index("ix_runs_created_at", "created_at"),
    )


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)
    short_message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_event_run_sequence"),
        CheckConstraint(
            "event_type in ('started','completed','failed','warning','skipped')",
            name="ck_event_type",
        ),
    )


class ApprovalReservation(Base):
    """A commitment reserved by an APPROVED decision. Not a payment record."""

    __tablename__ = "approval_reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("invoice_cases.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    normalized_invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_number_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)


# Partial unique indexes: both are scoped to active rows, so a future
# revoke-and-reapprove flow can deactivate a reservation without dropping the
# audit row. They are the database-level guarantee that concurrent requests
# cannot approve one case twice or reuse one vendor+invoice identity.
Index(
    "uq_reservation_active_case",
    ApprovalReservation.case_id,
    unique=True,
    sqlite_where=ApprovalReservation.active.is_(True),
)
Index(
    "uq_reservation_active_identity",
    ApprovalReservation.vendor_id,
    ApprovalReservation.normalized_invoice_number,
    unique=True,
    sqlite_where=ApprovalReservation.active.is_(True),
)


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("invoice_cases.id"), nullable=False, index=True)
    source_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    derived_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    proposed_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)


class IdempotencyRecord(Base):
    """Maps an Idempotency-Key to the run it originally created."""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    case_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, nullable=False)

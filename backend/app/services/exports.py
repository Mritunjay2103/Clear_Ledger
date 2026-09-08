"""Decision exports.

The JSON export is the provenance record: input hash, provider, field
provenance, rule results, snapshots, decision and lineage. The CSV export is a
flattened summary for a spreadsheet, with untrusted text neutralised against
formula injection while our own numeric columns stay numeric.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalReservation,
    InvoiceCase,
    InvoiceDocument,
    ReviewAction,
    WorkflowEvent,
    WorkflowRun,
)
from app.domain.money import format_minor

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def neutralize_for_csv(value: Any) -> str:
    """Defuse spreadsheet formula injection in untrusted text."""
    if value is None:
        return ""
    text = str(value)
    text = _CONTROL_RE.sub(" ", text).replace("\t", " ").replace("\r", " ")
    if text[:1] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def build_export_json(session: Session, run: WorkflowRun) -> dict:
    document = session.get(InvoiceDocument, run.document_id)
    case = session.get(InvoiceCase, run.case_id)
    extraction = dict(run.structured_extraction or {})
    # Page text is retained on the run for review but is not part of the export.
    extraction.pop("page_texts", None)
    extraction.pop("page_provenance", None)

    reservation = session.scalar(
        select(ApprovalReservation).where(ApprovalReservation.run_id == run.id)
    )
    actions = list(
        session.scalars(
            select(ReviewAction)
            .where(ReviewAction.case_id == run.case_id)
            .order_by(ReviewAction.timestamp)
        )
    )
    children = list(
        session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.parent_run_id == run.id)
            .order_by(WorkflowRun.created_at)
        )
    )
    events = list(
        session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == run.id)
            .order_by(WorkflowEvent.sequence)
        )
    )

    return {
        "export_version": "1.0",
        "generated_for": {"run_id": run.id, "case_id": run.case_id},
        "policy_version": run.policy_version,
        "input": {
            "filename": document.safe_original_filename if document else None,
            "sha256": document.sha256 if document else None,
            "byte_count": document.byte_count if document else None,
            "page_count": document.page_count if document else None,
        },
        "execution": {
            "status": run.execution_status,
            "trigger": run.trigger,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
            "processing_duration_ms": run.processing_duration_ms,
            "error_code": run.error_code,
            "error_message": run.error_message,
        },
        "extraction": extraction,
        "extraction_warnings": run.extraction_warnings,
        # Duplicated out of the extraction block so a reader auditing "where did
        # this number come from" does not have to know the extraction schema.
        "evidence": ((extraction.get("invoice") or {}).get("evidence") or {}),
        "decision": run.decision,
        "decision_payload": run.decision_payload,
        "rule_results": run.rule_results,
        "policy_snapshot": run.policy_snapshot,
        "reference_snapshot": run.reference_snapshot,
        "reservation": (
            {
                "id": reservation.id,
                "amount": format_minor(reservation.amount_minor, reservation.currency),
                "currency": reservation.currency,
                "purchase_order_id": reservation.po_id,
                "active": reservation.active,
                "created_at": _iso(reservation.created_at),
                "note": "A reserved commitment against a purchase order. No payment is made.",
            }
            if reservation
            else None
        ),
        "events": [
            {
                "sequence": event.sequence,
                "stage_key": event.stage_key,
                "event_type": event.event_type,
                "timestamp": _iso(event.timestamp),
                "message": event.short_message,
                "metadata": event.structured_metadata,
            }
            for event in events
        ],
        "lineage": {
            "case_created_at": _iso(case.created_at) if case else None,
            "parent_run_id": run.parent_run_id,
            "child_run_ids": [child.id for child in children],
            "overrides": run.overrides,
            "review_actions": [
                {
                    "id": action.id,
                    "actor_label": action.actor_label,
                    "reason": action.reason,
                    "old_values": action.old_values,
                    "proposed_values": action.proposed_values,
                    "source_run_id": action.source_run_id,
                    "derived_run_id": action.derived_run_id,
                    "timestamp": _iso(action.timestamp),
                }
                for action in actions
            ],
        },
    }


CSV_COLUMNS = (
    "run_id",
    "case_id",
    "execution_status",
    "decision",
    "trigger",
    "invoice_number",
    "vendor",
    "currency",
    "invoice_gross_total",
    "matched_po",
    "po_approved_total",
    "po_committed_before",
    "po_tolerance",
    "po_allowed_total",
    "po_projected_total",
    "reserved_amount",
    "created_at",
    "completed_at",
    "processing_duration_ms",
    "extraction_provider",
    "extraction_model",
    "human_corrected",
    "failing_rule_codes",
    "headline",
)


def build_export_csv(session: Session, run: WorkflowRun) -> str:
    payload = run.decision_payload or {}
    comparison = payload.get("po_comparison") or {}
    outcome = payload.get("outcome") or {}
    currency = run.currency or "INR"
    reservation = session.scalar(
        select(ApprovalReservation).where(ApprovalReservation.run_id == run.id)
    )

    def amount(minor: Any) -> str:
        return format_minor(int(minor), currency) if isinstance(minor, int) else ""

    failing = [
        result.get("code")
        for result in (run.rule_results or [])
        if result.get("status") in {"fail", "review"}
    ]

    row = {
        "run_id": run.id,
        "case_id": run.case_id,
        "execution_status": run.execution_status,
        "decision": run.decision or "",
        "trigger": run.trigger,
        "invoice_number": neutralize_for_csv(run.invoice_number),
        "vendor": neutralize_for_csv(run.vendor_display_name),
        "currency": currency,
        "invoice_gross_total": amount(run.gross_total_minor),
        "matched_po": neutralize_for_csv(run.matched_po_number),
        "po_approved_total": amount(comparison.get("approved_total_minor")),
        "po_committed_before": amount(comparison.get("committed_before_minor")),
        "po_tolerance": amount(comparison.get("tolerance_minor")),
        "po_allowed_total": amount(comparison.get("allowed_total_minor")),
        "po_projected_total": amount(comparison.get("projected_total_minor")),
        "reserved_amount": amount(reservation.amount_minor) if reservation else "",
        "created_at": _iso(run.created_at) or "",
        "completed_at": _iso(run.completed_at) or "",
        "processing_duration_ms": run.processing_duration_ms or "",
        "extraction_provider": run.extraction_provider or "",
        "extraction_model": neutralize_for_csv(run.actual_model),
        "human_corrected": "yes" if run.has_human_correction else "no",
        "failing_rule_codes": neutralize_for_csv(" ".join(code for code in failing if code)),
        "headline": neutralize_for_csv(outcome.get("headline")),
    }

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\r\n")
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue()


def _iso(value) -> str | None:
    return value.isoformat() if value else None

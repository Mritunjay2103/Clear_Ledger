"""Run serialisation for the API.

Amounts leave the backend as decimal strings alongside their minor-unit integer,
so the browser never has to do money arithmetic to render a figure.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalReservation,
    InvoiceDocument,
    ReviewAction,
    WorkflowEvent,
    WorkflowRun,
)
from app.domain.money import display_minor, format_minor
from app.services.workflow import STAGE_LABELS, STAGES


def money(minor: int | None, currency: str | None) -> dict | None:
    if minor is None:
        return None
    code = currency or "INR"
    return {"minor": minor, "amount": format_minor(minor, code), "currency": code}


def serialize_event(event: WorkflowEvent) -> dict:
    return {
        "sequence": event.sequence,
        "stage_key": event.stage_key,
        "stage_label": STAGE_LABELS.get(event.stage_key, event.stage_key),
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat(),
        "short_message": event.short_message,
        "structured_metadata": event.structured_metadata,
    }


def build_stage_view(events: list[WorkflowEvent]) -> list[dict]:
    """Fold the event log into one row per stage, in pipeline order."""
    by_stage: dict[str, dict] = {
        key: {
            "stage_key": key,
            "stage_label": label,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "elapsed_ms": None,
            "messages": [],
            "warnings": [],
            "errors": [],
        }
        for key, label in STAGES
    }
    for event in sorted(events, key=lambda item: item.sequence):
        stage = by_stage.get(event.stage_key)
        if stage is None:
            continue
        timestamp = event.timestamp.isoformat()
        if event.event_type == "started":
            stage["status"] = "running"
            stage["started_at"] = timestamp
        elif event.event_type == "completed":
            stage["status"] = "completed"
            stage["completed_at"] = timestamp
            stage["messages"].append(event.short_message)
            metadata = event.structured_metadata or {}
            if isinstance(metadata.get("elapsed_ms"), int):
                stage["elapsed_ms"] = metadata["elapsed_ms"]
        elif event.event_type == "failed":
            stage["status"] = "failed"
            stage["completed_at"] = timestamp
            stage["errors"].append(event.short_message)
        elif event.event_type == "warning":
            stage["warnings"].append(event.short_message)
        elif event.event_type == "skipped":
            stage["status"] = "skipped"
            stage["messages"].append(event.short_message)
    return [by_stage[key] for key, _ in STAGES]


def serialize_run_summary(run: WorkflowRun) -> dict:
    payload = run.decision_payload or {}
    outcome = payload.get("outcome") or {}
    return {
        "id": run.id,
        "case_id": run.case_id,
        "parent_run_id": run.parent_run_id,
        "trigger": run.trigger,
        "execution_status": run.execution_status,
        "decision": run.decision,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "processing_duration_ms": run.processing_duration_ms,
        "invoice_number": run.invoice_number,
        "vendor_display_name": run.vendor_display_name,
        "gross_total": money(run.gross_total_minor, run.currency),
        "currency": run.currency,
        "matched_po_number": run.matched_po_number,
        "has_human_correction": run.has_human_correction,
        "extraction_provider": run.extraction_provider,
        "actual_model": run.actual_model,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "headline": outcome.get("headline"),
    }


def serialize_run_detail(session: Session, run: WorkflowRun) -> dict:
    document = session.get(InvoiceDocument, run.document_id)
    events = list(
        session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == run.id)
            .order_by(WorkflowEvent.sequence)
        )
    )
    reservation = session.scalar(
        select(ApprovalReservation).where(ApprovalReservation.run_id == run.id)
    )
    siblings = list(
        session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.case_id == run.case_id)
            .order_by(WorkflowRun.created_at)
        )
    )
    actions = list(
        session.scalars(
            select(ReviewAction)
            .where(ReviewAction.case_id == run.case_id)
            .order_by(ReviewAction.timestamp)
        )
    )

    extraction = dict(run.structured_extraction or {})
    page_texts: list[str] = extraction.pop("page_texts", []) or []
    page_provenance: list[str] = extraction.pop("page_provenance", []) or []
    payload = run.decision_payload or {}
    comparison = dict(payload.get("po_comparison") or {})
    currency = run.currency or comparison.get("currency") or "INR"

    for key in (
        "invoice_gross_minor",
        "approved_total_minor",
        "committed_before_minor",
        "tolerance_minor",
        "allowed_total_minor",
        "projected_total_minor",
        "remaining_nominal_before_minor",
        "remaining_allowed_before_minor",
        "remaining_nominal_after_minor",
        "projected_remaining_allowed_minor",
    ):
        value = comparison.get(key)
        comparison[key.replace("_minor", "_display")] = (
            display_minor(value, currency) if isinstance(value, int) else None
        )

    return {
        **serialize_run_summary(run),
        "version": run.version,
        "policy_version": run.policy_version,
        "overrides": run.overrides,
        "extraction_warnings": run.extraction_warnings,
        "document": (
            {
                "id": document.id,
                "filename": document.safe_original_filename,
                "sha256": document.sha256,
                "byte_count": document.byte_count,
                "page_count": document.page_count,
            }
            if document
            else None
        ),
        "extraction": extraction,
        "page_count": len(page_texts),
        "page_provenance": page_provenance,
        "events": [serialize_event(event) for event in events],
        "stages": build_stage_view(events),
        "rule_results": run.rule_results or [],
        "decision_payload": {**payload, "po_comparison": comparison},
        "policy_snapshot": run.policy_snapshot,
        "reference_snapshot": run.reference_snapshot,
        "reservation": (
            {
                "id": reservation.id,
                "amount": money(reservation.amount_minor, reservation.currency),
                "purchase_order_id": reservation.po_id,
                "active": reservation.active,
                "created_at": reservation.created_at.isoformat(),
            }
            if reservation
            else None
        ),
        "case_runs": [serialize_run_summary(sibling) for sibling in siblings],
        "review_actions": [
            {
                "id": action.id,
                "actor_label": action.actor_label,
                "reason": action.reason,
                "old_values": action.old_values,
                "proposed_values": action.proposed_values,
                "source_run_id": action.source_run_id,
                "derived_run_id": action.derived_run_id,
                "timestamp": action.timestamp.isoformat(),
            }
            for action in actions
        ],
        "last_event_sequence": events[-1].sequence if events else 0,
        "is_terminal": run.execution_status in {"COMPLETED", "FAILED", "INTERRUPTED"},
    }


def evidence_for_field(run: WorkflowRun, field: str) -> dict[str, Any] | None:
    extraction = (run.structured_extraction or {}).get("invoice") or {}
    return (extraction.get("evidence") or {}).get(field)

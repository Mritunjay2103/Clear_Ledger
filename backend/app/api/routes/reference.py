"""Read-only reference data and the dashboard aggregates.

Reference data is read-only by design in version 1: the CSV files under
``data/reference`` are the editable source, validated and applied by
``scripts/manage.py seed``. Every dashboard number is computed from persisted
records; nothing here is illustrative.
"""

from __future__ import annotations

import statistics

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ApprovalReservation, PurchaseOrder, Vendor, WorkflowRun
from app.db.session import get_db
from app.domain.money import format_minor
from app.domain.policy import DEFAULT_POLICY

router = APIRouter(tags=["reference"])


@router.get("/vendors", summary="Reference vendors (read-only)")
def list_vendors(session: Session = Depends(get_db)) -> dict:
    vendors = list(session.scalars(select(Vendor).order_by(Vendor.canonical_name)))
    return {
        "source": "data/reference/vendors.csv",
        "editable_in_ui": False,
        "note": (
            "Edit the CSV and run `python scripts/manage.py seed` to apply changes. Seeding is "
            "idempotent and never deletes history."
        ),
        "items": [
            {
                "id": vendor.id,
                "vendor_code": vendor.vendor_code,
                "canonical_name": vendor.canonical_name,
                "normalized_name": vendor.normalized_name,
                "aliases": vendor.explicit_aliases or [],
                "status": vendor.status,
                "supported_currency": vendor.supported_currency,
            }
            for vendor in vendors
        ],
    }


@router.get("/purchase-orders", summary="Reference purchase orders with live commitments")
def list_purchase_orders(session: Session = Depends(get_db)) -> dict:
    rows = list(
        session.execute(
            select(PurchaseOrder, Vendor)
            .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .order_by(PurchaseOrder.po_number)
        )
    )
    committed_by_po = dict(
        session.execute(
            select(
                ApprovalReservation.po_id,
                func.coalesce(func.sum(ApprovalReservation.amount_minor), 0),
            )
            .where(ApprovalReservation.active.is_(True))
            .group_by(ApprovalReservation.po_id)
        ).all()
    )

    items = []
    for purchase_order, vendor in rows:
        committed = int(committed_by_po.get(purchase_order.id, 0))
        tolerance = DEFAULT_POLICY.tolerance_minor(purchase_order.approved_total_minor)
        allowed = purchase_order.approved_total_minor + tolerance
        currency = purchase_order.currency
        items.append(
            {
                "id": purchase_order.id,
                "po_number": purchase_order.po_number,
                "vendor_id": vendor.id,
                "vendor_name": vendor.canonical_name,
                "vendor_status": vendor.status,
                "currency": currency,
                "status": purchase_order.status,
                "description": purchase_order.description,
                "approved_total": format_minor(purchase_order.approved_total_minor, currency),
                "approved_total_minor": purchase_order.approved_total_minor,
                "committed": format_minor(committed, currency),
                "committed_minor": committed,
                "available_nominal": format_minor(
                    purchase_order.approved_total_minor - committed, currency
                ),
                "tolerance": format_minor(tolerance, currency),
                "available_including_tolerance": format_minor(allowed - committed, currency),
                "allowed_total_including_tolerance": format_minor(allowed, currency),
            }
        )
    return {
        "source": "data/reference/purchase_orders.csv",
        "editable_in_ui": False,
        "balance_note": (
            "'Available including tolerance' adds the demo overage allowance to the approved "
            "value. It is not the nominal purchase-order value."
        ),
        "items": items,
    }


@router.get("/dashboard", summary="Aggregates over persisted records")
def dashboard(session: Session = Depends(get_db)) -> dict:
    """Metrics computed from stored runs.

    Case-level decision counts use the newest decision-bearing run per case, so a
    later failed retry never erases a business decision that really happened.
    """
    newest = (
        select(
            WorkflowRun.case_id.label("case_id"),
            func.max(WorkflowRun.completed_at).label("newest"),
        )
        .where(WorkflowRun.execution_status == "COMPLETED", WorkflowRun.decision.is_not(None))
        .group_by(WorkflowRun.case_id)
        .subquery()
    )
    latest_runs = list(
        session.scalars(
            select(WorkflowRun).join(
                newest,
                (WorkflowRun.case_id == newest.c.case_id)
                & (WorkflowRun.completed_at == newest.c.newest),
            )
        )
    )

    decision_counts = {"APPROVED": 0, "REVIEW": 0, "BLOCKED": 0}
    corrected_cases = 0
    for run in latest_runs:
        if run.decision in decision_counts:
            decision_counts[run.decision] += 1
        if run.has_human_correction:
            corrected_cases += 1

    total_cases = session.scalar(select(func.count(func.distinct(WorkflowRun.case_id)))) or 0
    total_runs = session.scalar(select(func.count(WorkflowRun.id))) or 0
    failed_runs = (
        session.scalar(
            select(func.count(WorkflowRun.id)).where(
                WorkflowRun.execution_status.in_(["FAILED", "INTERRUPTED"])
            )
        )
        or 0
    )
    in_flight = (
        session.scalar(
            select(func.count(WorkflowRun.id)).where(
                WorkflowRun.execution_status.in_(["QUEUED", "RUNNING"])
            )
        )
        or 0
    )

    durations = [
        run.processing_duration_ms
        for run in session.scalars(
            select(WorkflowRun).where(
                WorkflowRun.execution_status == "COMPLETED",
                WorkflowRun.processing_duration_ms.is_not(None),
            )
        )
    ]
    median_ms = int(statistics.median(durations)) if durations else None

    decided_cases = sum(decision_counts.values())
    auto_approved = decision_counts["APPROVED"] - sum(
        1 for run in latest_runs if run.decision == "APPROVED" and run.has_human_correction
    )
    reserved_total = (
        session.scalar(
            select(func.coalesce(func.sum(ApprovalReservation.amount_minor), 0)).where(
                ApprovalReservation.active.is_(True)
            )
        )
        or 0
    )

    recent = list(
        session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(8))
    )
    from app.api.serializers import serialize_run_summary

    return {
        "metrics": {
            "cases_processed": {
                "value": total_cases,
                "definition": "Distinct invoice cases that have at least one run.",
            },
            "cases_approved": {
                "value": decision_counts["APPROVED"],
                "definition": "Cases whose newest decision-bearing run is APPROVED.",
            },
            "cases_needing_review": {
                "value": decision_counts["REVIEW"],
                "definition": "Cases whose newest decision-bearing run is REVIEW.",
            },
            "cases_blocked": {
                "value": decision_counts["BLOCKED"],
                "definition": "Cases whose newest decision-bearing run is BLOCKED.",
            },
            "failed_attempts": {
                "value": failed_runs,
                "definition": (
                    "Runs that failed or were interrupted technically. Reported separately; they "
                    "do not erase a prior business decision on the same case."
                ),
            },
            "runs_in_flight": {
                "value": in_flight,
                "definition": "Runs currently queued or executing.",
            },
            "total_runs": {
                "value": total_runs,
                "definition": "All run attempts, including retries and corrections.",
            },
            "median_processing_ms": {
                "value": median_ms,
                "definition": "Median wall-clock duration of completed runs on this machine.",
            },
            "auto_approval_rate": {
                "value": (round(auto_approved / decided_cases, 4) if decided_cases else None),
                "definition": (
                    f"Cases approved without any human correction ({auto_approved}) divided by "
                    f"cases with a completed decision ({decided_cases})."
                ),
                "numerator": auto_approved,
                "denominator": decided_cases,
            },
            "reserved_commitments": {
                "value": format_minor(int(reserved_total), "INR"),
                "definition": (
                    "Sum of active approval reservations. A reserved commitment against a "
                    "purchase order, not a payment."
                ),
            },
        },
        "decision_breakdown": decision_counts,
        "cases_with_human_correction": corrected_cases,
        "recent_runs": [serialize_run_summary(run) for run in recent],
    }

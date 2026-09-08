"""Vendor and purchase-order resolution.

Matching is deliberately conservative. Exact canonical-name or maintained-alias
equality (after case/whitespace/punctuation folding) is the only thing that
counts as a confident vendor match. Fuzzy similarity exists solely to rank
candidates for a human to look at; it can never carry an automatic approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApprovalReservation, PurchaseOrder, Vendor
from app.domain.normalization import (
    normalize_po_number,
    normalize_vendor_name,
    token_similarity,
)

#: Similarity at or above this is worth showing a reviewer. It never approves.
CANDIDATE_DISPLAY_THRESHOLD = 0.34


@dataclass
class VendorCandidate:
    vendor_id: str
    canonical_name: str
    status: str
    similarity: float
    matched_on: str


@dataclass
class VendorResolution:
    vendor: Vendor | None = None
    matched_on: str | None = None
    confident: bool = False
    reason: str = ""
    candidates: list[VendorCandidate] = field(default_factory=list)


@dataclass
class PoCandidate:
    po_id: str
    po_number: str
    currency: str
    approved_total_minor: int
    committed_minor: int
    status: str
    rationale: str


@dataclass
class PoResolution:
    purchase_order: PurchaseOrder | None = None
    source: str = "none"  # explicit_reference | human_selection | none
    reference_found: bool = False
    reference_raw: str | None = None
    reference_exists: bool = True
    reason: str = ""
    candidates: list[PoCandidate] = field(default_factory=list)


def resolve_vendor(session: Session, printed_name: str | None) -> VendorResolution:
    """Resolve the printed supplier name against reference data."""
    if not printed_name or not printed_name.strip():
        return VendorResolution(reason="No vendor name was extracted from the invoice.")

    normalized = normalize_vendor_name(printed_name)
    vendors = list(session.scalars(select(Vendor)))

    exact = [vendor for vendor in vendors if vendor.normalized_name == normalized]
    if len(exact) == 1:
        return VendorResolution(
            vendor=exact[0],
            matched_on="canonical_name",
            confident=True,
            reason=f"Printed name matches the canonical name of {exact[0].canonical_name}.",
        )
    if len(exact) > 1:
        return VendorResolution(
            reason="Several reference vendors share this normalised name.",
            candidates=[_to_candidate(vendor, 1.0, "canonical_name") for vendor in exact],
        )

    alias_matches = [
        (vendor, alias)
        for vendor in vendors
        for alias in (vendor.explicit_aliases or [])
        if normalize_vendor_name(alias) == normalized
    ]
    # Several aliases of one vendor may normalise identically; that is one match,
    # not an ambiguity. Only aliases across different vendors are ambiguous.
    alias_vendor_ids = {vendor.id for vendor, _ in alias_matches}
    if len(alias_vendor_ids) == 1:
        vendor, alias = alias_matches[0]
        return VendorResolution(
            vendor=vendor,
            matched_on="alias",
            confident=True,
            reason=(
                f"Printed name matches the maintained alias {alias!r} of {vendor.canonical_name}."
            ),
        )
    if len(alias_vendor_ids) > 1:
        return VendorResolution(
            reason="The printed name matches aliases belonging to more than one vendor.",
            candidates=[
                _to_candidate(vendor, 1.0, f"alias:{alias}") for vendor, alias in alias_matches
            ],
        )

    ranked = sorted(
        (
            _to_candidate(
                vendor, token_similarity(printed_name, vendor.canonical_name), "similarity"
            )
            for vendor in vendors
        ),
        key=lambda candidate: candidate.similarity,
        reverse=True,
    )
    shortlist = [c for c in ranked if c.similarity >= CANDIDATE_DISPLAY_THRESHOLD][:5]
    reason = (
        f"No reference vendor has the canonical name or alias {printed_name!r}."
        if not shortlist
        else (
            f"{printed_name!r} is not an exact canonical name or maintained alias. "
            "Similar vendors are listed for a reviewer; similarity alone cannot confirm identity."
        )
    )
    return VendorResolution(reason=reason, candidates=shortlist)


def _to_candidate(vendor: Vendor, similarity: float, matched_on: str) -> VendorCandidate:
    return VendorCandidate(
        vendor_id=vendor.id,
        canonical_name=vendor.canonical_name,
        status=vendor.status,
        similarity=round(similarity, 3),
        matched_on=matched_on,
    )


def committed_minor_for_po(session: Session, po_id: str, exclude_case_id: str | None = None) -> int:
    """Sum active approval reservations against a purchase order.

    ``exclude_case_id`` keeps a correction on the same case from counting its own
    earlier reservation as a competing commitment.
    """
    statement = select(ApprovalReservation).where(
        ApprovalReservation.po_id == po_id,
        ApprovalReservation.active.is_(True),
    )
    if exclude_case_id:
        statement = statement.where(ApprovalReservation.case_id != exclude_case_id)
    return sum(reservation.amount_minor for reservation in session.scalars(statement))


def resolve_purchase_order(
    session: Session,
    *,
    printed_po_number: str | None,
    vendor: Vendor | None,
    invoice_currency: str | None,
    invoice_gross_minor: int | None,
    selected_po_id: str | None = None,
    exclude_case_id: str | None = None,
) -> PoResolution:
    """Resolve the purchase order, or produce ranked candidates for a reviewer."""
    if selected_po_id:
        purchase_order = session.get(PurchaseOrder, selected_po_id)
        if purchase_order is None:
            return PoResolution(
                source="human_selection",
                reference_found=bool(printed_po_number),
                reference_raw=printed_po_number,
                reference_exists=False,
                reason="The purchase order selected during review no longer exists.",
            )
        return PoResolution(
            purchase_order=purchase_order,
            source="human_selection",
            reference_found=bool(printed_po_number),
            reference_raw=printed_po_number,
            reason=(
                f"{purchase_order.po_number} was selected by a reviewer; the invoice itself "
                "does not print a purchase-order reference."
                if not printed_po_number
                else f"{purchase_order.po_number} was confirmed by a reviewer."
            ),
        )

    if printed_po_number:
        normalized = normalize_po_number(printed_po_number)
        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.normalized_po_number == normalized)
        )
        if purchase_order is None:
            return PoResolution(
                source="explicit_reference",
                reference_found=True,
                reference_raw=printed_po_number,
                reference_exists=False,
                reason=(
                    f"The invoice references {printed_po_number}, which is not in reference data."
                ),
            )
        return PoResolution(
            purchase_order=purchase_order,
            source="explicit_reference",
            reference_found=True,
            reference_raw=printed_po_number,
            reason=f"The invoice prints an explicit reference to {purchase_order.po_number}.",
        )

    candidates: list[PoCandidate] = []
    if vendor is not None:
        open_pos = list(
            session.scalars(
                select(PurchaseOrder).where(
                    PurchaseOrder.vendor_id == vendor.id,
                    PurchaseOrder.status == "open",
                )
            )
        )
        for purchase_order in open_pos:
            if invoice_currency and purchase_order.currency != invoice_currency:
                continue
            committed = committed_minor_for_po(session, purchase_order.id, exclude_case_id)
            remaining = purchase_order.approved_total_minor - committed
            plausible = invoice_gross_minor is None or invoice_gross_minor <= remaining
            rationale = (
                "Currency matches and the remaining nominal balance covers this invoice."
                if plausible
                else "Currency matches but the remaining balance does not cover this invoice."
            )
            candidates.append(
                PoCandidate(
                    po_id=purchase_order.id,
                    po_number=purchase_order.po_number,
                    currency=purchase_order.currency,
                    approved_total_minor=purchase_order.approved_total_minor,
                    committed_minor=committed,
                    status=purchase_order.status,
                    rationale=rationale,
                )
            )
        candidates.sort(
            key=lambda candidate: (
                invoice_gross_minor is not None
                and candidate.approved_total_minor - candidate.committed_minor
                >= invoice_gross_minor,
                -abs(
                    (candidate.approved_total_minor - candidate.committed_minor)
                    - (invoice_gross_minor or 0)
                ),
            ),
            reverse=True,
        )

    return PoResolution(
        source="none",
        reference_found=False,
        reference_raw=None,
        reason=(
            "The invoice does not print a purchase-order reference. Version 1 always routes "
            "these to review, even when one candidate looks plausible."
        ),
        candidates=candidates[:5],
    )

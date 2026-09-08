"""Deterministic policy evaluation.

This module is a pure function of its inputs: everything it needs from the
database is fetched by the caller and handed over as plain snapshots. That makes
each rule testable in isolation and guarantees the decision cannot depend on
anything but the facts recorded on the run.

All values here are demo assumptions for a prototype, not accounting standards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.decisions import Decision, DecisionOutcome, RuleResult, compose_decision, rule
from app.domain.money import (
    ARITHMETIC_EPSILON,
    MoneyError,
    display_minor,
    format_minor,
    is_supported_currency,
    normalize_currency,
    parse_decimal,
    to_minor,
)
from app.domain.normalization import normalize_invoice_number, parse_invoice_date
from app.schemas.invoice import ExtractedInvoice, FieldQuality


class PolicyConfig(BaseModel):
    """Versioned policy configuration. Changing a value means a new version."""

    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    overage_tolerance_percentage: str = "0.01"
    overage_tolerance_cap: str = "500.00"
    base_currency: str = "INR"
    supported_currencies: list[str] = ["INR"]
    require_explicit_po_reference: bool = True
    arithmetic_epsilon: str = "0.01"

    def tolerance_minor(self, approved_total_minor: int) -> int:
        """min(1% of the PO, INR 500) expressed in paise."""
        percentage = (
            Decimal(approved_total_minor) * parse_decimal(self.overage_tolerance_percentage)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        cap = Decimal(to_minor(parse_decimal(self.overage_tolerance_cap), self.base_currency))
        return int(min(percentage, cap))

    def as_snapshot(self) -> dict:
        return self.model_dump()


DEFAULT_POLICY = PolicyConfig()


@dataclass
class VendorSnapshot:
    id: str
    canonical_name: str
    status: str
    supported_currency: str


@dataclass
class PoSnapshot:
    id: str
    po_number: str
    vendor_id: str
    vendor_name: str
    currency: str
    approved_total_minor: int
    status: str


@dataclass
class DuplicateHit:
    case_id: str
    run_id: str
    kind: str
    detail: str
    amount_minor: int | None = None
    invoice_number: str | None = None


@dataclass
class PolicyInputs:
    invoice: ExtractedInvoice
    qualities: list[FieldQuality]
    vendor: VendorSnapshot | None
    vendor_confident: bool
    vendor_reason: str
    vendor_candidates: list[dict] = field(default_factory=list)
    purchase_order: PoSnapshot | None = None
    po_source: str = "none"
    po_reference_found: bool = False
    po_reference_raw: str | None = None
    po_reference_exists: bool = True
    po_reason: str = ""
    po_candidates: list[dict] = field(default_factory=list)
    committed_before_minor: int = 0
    duplicate_file: DuplicateHit | None = None
    duplicate_identity: DuplicateHit | None = None
    case_already_approved: bool = False
    extraction_warnings: list[str] = field(default_factory=list)
    config: PolicyConfig = field(default_factory=lambda: DEFAULT_POLICY)


class NormalizedFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_number: str | None = None
    normalized_invoice_number: str | None = None
    invoice_date: str | None = None
    invoice_date_iso: str | None = None
    currency: str | None = None
    subtotal_minor: int | None = None
    tax_total_minor: int | None = None
    gross_total_minor: int | None = None
    document_kind: str = "unknown"
    parse_errors: list[str] = []


class PoComparison(BaseModel):
    model_config = ConfigDict(extra="ignore")

    po_number: str | None = None
    currency: str | None = None
    invoice_gross_minor: int | None = None
    approved_total_minor: int | None = None
    committed_before_minor: int = 0
    tolerance_minor: int | None = None
    allowed_total_minor: int | None = None
    projected_total_minor: int | None = None
    remaining_nominal_before_minor: int | None = None
    remaining_allowed_before_minor: int | None = None
    remaining_nominal_after_minor: int | None = None
    projected_remaining_allowed_minor: int | None = None
    reserved: bool = False
    verdict: str = "not_evaluated"
    within_tolerance_overage: bool = False


class PolicyEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Decision
    rule_results: list[RuleResult]
    facts: NormalizedFacts
    po_comparison: PoComparison
    outcome: DecisionOutcome
    blocking_codes: list[str] = []
    review_codes: list[str] = []


def normalize_facts(invoice: ExtractedInvoice, config: PolicyConfig) -> NormalizedFacts:
    """Turn printed strings into exact values, collecting parse failures."""
    errors: list[str] = []
    currency = normalize_currency(invoice.currency)

    def money(raw: str | None, label: str) -> int | None:
        if raw in (None, ""):
            return None
        try:
            return to_minor(parse_decimal(raw), currency or config.base_currency)
        except MoneyError as exc:
            errors.append(f"{label}: {exc}")
            return None

    parsed_date = parse_invoice_date(invoice.invoice_date)
    if parsed_date.value is None and invoice.invoice_date:
        errors.append(f"invoice_date: {parsed_date.reason}")

    return NormalizedFacts(
        invoice_number=invoice.invoice_number,
        normalized_invoice_number=normalize_invoice_number(invoice.invoice_number) or None,
        invoice_date=invoice.invoice_date,
        invoice_date_iso=parsed_date.value.isoformat() if parsed_date.value else None,
        currency=currency,
        subtotal_minor=money(invoice.subtotal, "subtotal"),
        tax_total_minor=money(invoice.tax_total, "tax_total"),
        gross_total_minor=money(invoice.gross_total, "gross_total"),
        document_kind=invoice.document_kind,
        parse_errors=errors,
    )


def evaluate(inputs: PolicyInputs) -> PolicyEvaluation:
    """Apply every rule and compose the decision."""
    config = inputs.config
    invoice = inputs.invoice
    facts = normalize_facts(invoice, config)
    quality_by_field = {quality.field: quality for quality in inputs.qualities}
    results: list[RuleResult] = []

    results.append(_document_kind_rule(facts))
    results.append(_fields_present_rule(invoice, facts))
    results.append(_evidence_rule(quality_by_field))
    results.append(_date_rule(invoice, facts))
    results.append(_currency_rule(invoice, facts, config))
    results.append(_amount_rule(facts))
    results.extend(_arithmetic_rules(invoice, facts, config))
    results.extend(_vendor_rules(inputs))
    results.extend(_po_rules(inputs, facts))
    results.extend(_duplicate_rules(inputs, facts))

    comparison, budget_rule = _budget_rule(inputs, facts, config)
    results.append(budget_rule)

    decision, blocking, reviewing = compose_decision(results)
    comparison.reserved = decision == "APPROVED" and comparison.verdict == "within_limit"
    if comparison.projected_total_minor is not None and comparison.approved_total_minor is not None:
        if decision == "APPROVED":
            comparison.remaining_nominal_after_minor = (
                comparison.approved_total_minor - comparison.projected_total_minor
            )
        else:
            comparison.projected_remaining_allowed_minor = (
                comparison.allowed_total_minor or 0
            ) - comparison.projected_total_minor

    outcome = build_outcome(decision, results, facts, comparison, inputs)
    return PolicyEvaluation(
        decision=decision,
        rule_results=results,
        facts=facts,
        po_comparison=comparison,
        outcome=outcome,
        blocking_codes=blocking,
        review_codes=reviewing,
    )


def _document_kind_rule(facts: NormalizedFacts) -> RuleResult:
    if facts.document_kind == "credit_note":
        return rule(
            "DOC_KIND_SUPPORTED",
            "review",
            "review",
            "The document is a credit note. Version 1 evaluates positive invoices only.",
            observed_value="credit_note",
            expected_value="invoice",
            next_action=(
                "Process credit notes outside ClearLedger, or convert to a positive invoice."
            ),
        )
    if facts.document_kind == "unknown":
        return rule(
            "DOC_KIND_SUPPORTED",
            "pass",
            "warning",
            "The document does not label itself as an invoice or credit note; it was assessed "
            "as an invoice.",
            observed_value="unknown",
            expected_value="invoice",
            mandatory=False,
        )
    return rule(
        "DOC_KIND_SUPPORTED",
        "pass",
        "info",
        "The document is a positive invoice.",
        observed_value="invoice",
        expected_value="invoice",
    )


def _fields_present_rule(invoice: ExtractedInvoice, facts: NormalizedFacts) -> RuleResult:
    required = {
        "vendor_name": invoice.vendor_name,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "currency": invoice.currency,
        "gross_total": invoice.gross_total,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        return rule(
            "FIELDS_PRESENT",
            "review",
            "review",
            "Required invoice fields could not be read from the document.",
            observed_value=f"missing: {', '.join(missing)}",
            expected_value="all required fields present",
            next_action="Open the document and supply the missing values as a correction.",
        )
    if facts.parse_errors:
        return rule(
            "FIELDS_PRESENT",
            "review",
            "review",
            "A printed value could not be interpreted exactly.",
            observed_value="; ".join(facts.parse_errors),
            expected_value="values parse as exact decimals and dates",
            next_action="Correct the affected field and rerun the checks.",
        )
    return rule("FIELDS_PRESENT", "pass", "info", "Every required field was extracted.")


def _evidence_rule(quality_by_field: dict[str, FieldQuality]) -> RuleResult:
    unreliable = [
        quality
        for name, quality in quality_by_field.items()
        if name in {"vendor_name", "invoice_number", "invoice_date", "currency", "gross_total"}
        and quality.label == "uncertain"
    ]
    if unreliable:
        detail = "; ".join(f"{q.field}: {q.reason}" for q in unreliable)
        return rule(
            "EVIDENCE_SUPPORTED",
            "review",
            "review",
            "A critical value is not backed by verified evidence from the document.",
            observed_value=detail,
            expected_value="every critical field cites a verified excerpt",
            evidence_refs=[q.field for q in unreliable],
            next_action="Check the document and confirm or correct the affected values.",
        )
    human = [q.field for q in quality_by_field.values() if q.label == "human_supplied"]
    if human:
        return rule(
            "EVIDENCE_SUPPORTED",
            "pass",
            "warning",
            "Critical values are evidenced; some were supplied by a reviewer.",
            observed_value=f"human-supplied: {', '.join(human)}",
            evidence_refs=human,
            mandatory=False,
        )
    return rule(
        "EVIDENCE_SUPPORTED", "pass", "info", "Every critical value cites a verified excerpt."
    )


def _date_rule(invoice: ExtractedInvoice, facts: NormalizedFacts) -> RuleResult:
    if not invoice.invoice_date:
        return rule(
            "DATE_VALID",
            "review",
            "review",
            "No invoice date was found.",
            expected_value="a readable invoice date",
            next_action="Supply the invoice date from the document as a correction.",
        )
    parsed = parse_invoice_date(invoice.invoice_date)
    if parsed.ambiguous:
        return rule(
            "DATE_VALID",
            "review",
            "review",
            "The printed date is ambiguous.",
            observed_value=invoice.invoice_date,
            expected_value="an unambiguous date",
            next_action=f"{parsed.reason}. Confirm the intended date.",
        )
    if parsed.value is None:
        return rule(
            "DATE_VALID",
            "review",
            "review",
            "The printed date could not be interpreted.",
            observed_value=invoice.invoice_date,
            expected_value="a valid calendar date",
            next_action=parsed.reason or "Confirm the invoice date.",
        )
    return rule(
        "DATE_VALID",
        "pass",
        "info",
        "The invoice date is present and unambiguous.",
        observed_value=facts.invoice_date_iso,
    )


def _currency_rule(
    invoice: ExtractedInvoice, facts: NormalizedFacts, config: PolicyConfig
) -> RuleResult:
    if not facts.currency:
        return rule(
            "CURRENCY_SUPPORTED",
            "review",
            "review",
            "The invoice currency is not explicit in the document.",
            observed_value=invoice.currency or "not found",
            expected_value=", ".join(config.supported_currencies),
            next_action="Confirm the currency from the document; symbols alone are not conclusive.",
        )
    if facts.currency not in config.supported_currencies or not is_supported_currency(
        facts.currency
    ):
        return rule(
            "CURRENCY_SUPPORTED",
            "review",
            "review",
            f"{facts.currency} is outside the currencies this prototype evaluates automatically.",
            observed_value=facts.currency,
            expected_value=", ".join(config.supported_currencies),
            next_action="Route this invoice to the manual process for non-INR currencies.",
        )
    return rule(
        "CURRENCY_SUPPORTED",
        "pass",
        "info",
        f"The invoice is in {facts.currency}.",
        observed_value=facts.currency,
    )


def _amount_rule(facts: NormalizedFacts) -> RuleResult:
    if facts.gross_total_minor is None:
        return rule(
            "AMOUNT_POSITIVE",
            "review",
            "review",
            "The invoice total could not be read as an exact amount.",
            expected_value="a positive invoice total",
            next_action="Supply the printed invoice total as a correction.",
        )
    if facts.gross_total_minor <= 0:
        return rule(
            "AMOUNT_POSITIVE",
            "review",
            "review",
            "The invoice total is not positive. Version 1 processes positive invoices only.",
            observed_value=display_minor(facts.gross_total_minor, facts.currency or "INR"),
            expected_value="greater than zero",
            next_action="Handle credits and zero-value documents outside this prototype.",
        )
    return rule(
        "AMOUNT_POSITIVE",
        "pass",
        "info",
        "The invoice total is a positive amount.",
        observed_value=display_minor(facts.gross_total_minor, facts.currency or "INR"),
    )


def _arithmetic_rules(
    invoice: ExtractedInvoice, facts: NormalizedFacts, config: PolicyConfig
) -> list[RuleResult]:
    results: list[RuleResult] = []
    currency = facts.currency or config.base_currency
    epsilon_minor = to_minor(parse_decimal(config.arithmetic_epsilon), currency)

    if (
        facts.subtotal_minor is not None
        and facts.tax_total_minor is not None
        and facts.gross_total_minor is not None
    ):
        expected = facts.subtotal_minor + facts.tax_total_minor
        difference = abs(expected - facts.gross_total_minor)
        if difference > epsilon_minor:
            results.append(
                rule(
                    "ARITHMETIC_TOTALS",
                    "review",
                    "review",
                    "The printed subtotal and tax do not add up to the printed invoice total.",
                    observed_value=(
                        f"{display_minor(facts.subtotal_minor, currency)} + "
                        f"{display_minor(facts.tax_total_minor, currency)} = "
                        f"{display_minor(expected, currency)}, but the invoice states a total of "
                        f"{display_minor(facts.gross_total_minor, currency)} "
                        f"(difference {display_minor(difference, currency)})"
                    ),
                    expected_value=f"agreement within {ARITHMETIC_EPSILON} {currency}",
                    evidence_refs=["subtotal", "tax_total", "gross_total"],
                    next_action=(
                        "Ask the vendor for a corrected invoice, or confirm which printed figure "
                        "is authoritative. The printed values were preserved as they appear."
                    ),
                )
            )
        else:
            results.append(
                rule(
                    "ARITHMETIC_TOTALS",
                    "pass",
                    "info",
                    "Printed subtotal plus tax agrees with the printed invoice total.",
                    observed_value=(
                        f"{display_minor(facts.subtotal_minor, currency)} + "
                        f"{display_minor(facts.tax_total_minor, currency)} = "
                        f"{display_minor(facts.gross_total_minor, currency)}"
                    ),
                    evidence_refs=["subtotal", "tax_total", "gross_total"],
                )
            )
    else:
        missing = [
            name
            for name, value in (
                ("subtotal", facts.subtotal_minor),
                ("tax", facts.tax_total_minor),
                ("total", facts.gross_total_minor),
            )
            if value is None
        ]
        results.append(
            rule(
                "ARITHMETIC_TOTALS",
                "skipped",
                "info",
                "The subtotal/tax/total cross-check could not run.",
                observed_value=f"not printed: {', '.join(missing)}",
                next_action="No action: a bundled invoice may legitimately omit these figures.",
                mandatory=False,
            )
        )

    if invoice.line_items_complete and facts.subtotal_minor is not None:
        try:
            total = sum(
                to_minor(parse_decimal(item.net_amount), currency)
                for item in invoice.line_items
                if item.net_amount
            )
        except MoneyError:
            total = None
        if total is None:
            results.append(
                rule(
                    "ARITHMETIC_LINE_ITEMS",
                    "skipped",
                    "info",
                    "Line amounts could not be parsed exactly, so they were not summed.",
                    mandatory=False,
                )
            )
        elif invoice.line_amounts_tax_inclusive:
            results.append(
                rule(
                    "ARITHMETIC_LINE_ITEMS",
                    "skipped",
                    "info",
                    "Line amounts are tax inclusive, so they are not comparable with the "
                    "pre-tax subtotal.",
                    observed_value=display_minor(total, currency),
                    mandatory=False,
                )
            )
        elif abs(total - facts.subtotal_minor) > epsilon_minor:
            results.append(
                rule(
                    "ARITHMETIC_LINE_ITEMS",
                    "review",
                    "review",
                    "Line amounts do not sum to the printed subtotal.",
                    observed_value=(
                        f"lines total {display_minor(total, currency)} against a printed subtotal "
                        f"of {display_minor(facts.subtotal_minor, currency)}"
                    ),
                    expected_value=display_minor(facts.subtotal_minor, currency),
                    next_action="Confirm the itemised breakdown with the vendor.",
                )
            )
        else:
            results.append(
                rule(
                    "ARITHMETIC_LINE_ITEMS",
                    "pass",
                    "info",
                    "Line amounts sum to the printed subtotal.",
                    observed_value=display_minor(total, currency),
                )
            )
    else:
        results.append(
            rule(
                "ARITHMETIC_LINE_ITEMS",
                "skipped",
                "info",
                "The invoice has no complete itemised breakdown to check.",
                observed_value=f"{len(invoice.line_items)} line(s) parsed",
                next_action="No action: bundled invoices are accepted without line detail.",
                mandatory=False,
            )
        )
    return results


def _vendor_rules(inputs: PolicyInputs) -> list[RuleResult]:
    if inputs.vendor is None or not inputs.vendor_confident:
        candidate_note = ""
        if inputs.vendor_candidates:
            names = ", ".join(str(c.get("canonical_name")) for c in inputs.vendor_candidates[:3])
            candidate_note = f" Closest reference vendors: {names}."
        return [
            rule(
                "VENDOR_RESOLVED",
                "review",
                "review",
                "The invoicing vendor could not be confirmed against reference data.",
                observed_value=inputs.invoice.vendor_name or "not extracted",
                expected_value="an exact canonical name or maintained alias",
                next_action=(
                    inputs.vendor_reason
                    + candidate_note
                    + " Confirm the vendor, or add the printed name as an alias in reference data."
                ).strip(),
            ),
            rule(
                "VENDOR_STATUS",
                "skipped",
                "info",
                "Vendor status could not be checked because the vendor is unresolved.",
                mandatory=False,
            ),
        ]

    resolved = rule(
        "VENDOR_RESOLVED",
        "pass",
        "info",
        "The invoicing vendor was matched to reference data.",
        observed_value=inputs.vendor.canonical_name,
        next_action=None,
    )
    resolved.description = inputs.vendor_reason or resolved.description

    if inputs.vendor.status == "blocked":
        return [
            resolved,
            rule(
                "VENDOR_STATUS",
                "fail",
                "block",
                f"{inputs.vendor.canonical_name} is blocked for processing.",
                observed_value="blocked",
                expected_value="approved",
                next_action=(
                    "Processing is prohibited for this vendor. Escalate to procurement; a "
                    "correction cannot override a blocked vendor."
                ),
            ),
        ]
    return [
        resolved,
        rule(
            "VENDOR_STATUS",
            "pass",
            "info",
            f"{inputs.vendor.canonical_name} is approved for processing.",
            observed_value="approved",
            expected_value="approved",
        ),
    ]


def _po_rules(inputs: PolicyInputs, facts: NormalizedFacts) -> list[RuleResult]:
    results: list[RuleResult] = []
    purchase_order = inputs.purchase_order

    if inputs.po_reference_found:
        results.append(
            rule(
                "PO_REFERENCE_PRESENT",
                "pass",
                "info",
                "The invoice prints an explicit purchase-order reference.",
                observed_value=inputs.po_reference_raw,
            )
        )
    elif inputs.po_source == "human_selection":
        results.append(
            rule(
                "PO_REFERENCE_PRESENT",
                "pass",
                "warning",
                "The invoice prints no purchase-order reference; a reviewer selected one.",
                observed_value=(
                    "selected by reviewer: "
                    f"{purchase_order.po_number if purchase_order else 'unknown'}"
                ),
                expected_value="a reference printed on the invoice",
                mandatory=False,
            )
        )
    else:
        candidate_note = ""
        if inputs.po_candidates:
            names = ", ".join(str(c.get("po_number")) for c in inputs.po_candidates)
            candidate_note = f" Open candidates for this vendor: {names}."
        results.append(
            rule(
                "PO_REFERENCE_PRESENT",
                "review",
                "review",
                "The invoice does not print a purchase-order reference.",
                observed_value="not printed",
                expected_value="an explicit purchase-order reference",
                next_action=(
                    "Select the correct purchase order with a reason to rerun the checks."
                    + candidate_note
                ),
            )
        )

    if purchase_order is None:
        if inputs.po_reference_found and not inputs.po_reference_exists:
            results.append(
                rule(
                    "PO_RESOLVED",
                    "review",
                    "review",
                    "The referenced purchase order is not in reference data.",
                    observed_value=inputs.po_reference_raw,
                    expected_value="a known purchase order",
                    next_action="Confirm the purchase-order number with procurement.",
                )
            )
        else:
            results.append(
                rule(
                    "PO_RESOLVED",
                    "review",
                    "review",
                    "No purchase order is associated with this invoice.",
                    observed_value="unresolved",
                    expected_value="one matched purchase order",
                    next_action=inputs.po_reason or "Select a purchase order during review.",
                )
            )
        for code, description in (
            ("PO_VENDOR_MATCH", "The purchase order owner could not be checked."),
            ("PO_CURRENCY_MATCH", "The purchase order currency could not be checked."),
            ("PO_STATUS_OPEN", "The purchase order status could not be checked."),
        ):
            results.append(
                rule(
                    code,
                    "skipped",
                    "info",
                    description + " No purchase order is resolved.",
                    mandatory=False,
                )
            )
        return results

    results.append(
        rule(
            "PO_RESOLVED",
            "pass",
            "info",
            f"{purchase_order.po_number} was located in reference data.",
            observed_value=purchase_order.po_number,
        )
    )

    vendor_reliable = inputs.vendor is not None and inputs.vendor_confident
    if inputs.vendor is not None and purchase_order.vendor_id != inputs.vendor.id:
        if vendor_reliable:
            results.append(
                rule(
                    "PO_VENDOR_MATCH",
                    "fail",
                    "block",
                    f"{purchase_order.po_number} belongs to {purchase_order.vendor_name}, not to "
                    f"the invoicing vendor {inputs.vendor.canonical_name}.",
                    observed_value=purchase_order.vendor_name,
                    expected_value=inputs.vendor.canonical_name,
                    next_action=(
                        "Do not process this invoice against another vendor's purchase order. "
                        "Ask the vendor for the correct reference."
                    ),
                )
            )
        else:
            results.append(
                rule(
                    "PO_VENDOR_MATCH",
                    "review",
                    "review",
                    "The purchase order belongs to a different vendor, but the invoicing vendor "
                    "is itself unconfirmed.",
                    observed_value=purchase_order.vendor_name,
                    expected_value="the invoicing vendor",
                    next_action="Confirm the vendor identity before deciding.",
                )
            )
    elif inputs.vendor is None:
        results.append(
            rule(
                "PO_VENDOR_MATCH",
                "review",
                "review",
                "The purchase order owner cannot be compared while the vendor is unresolved.",
                observed_value=purchase_order.vendor_name,
                next_action="Confirm the vendor identity before deciding.",
            )
        )
    else:
        results.append(
            rule(
                "PO_VENDOR_MATCH",
                "pass",
                "info",
                f"{purchase_order.po_number} belongs to {purchase_order.vendor_name}.",
                observed_value=purchase_order.vendor_name,
                expected_value=inputs.vendor.canonical_name,
            )
        )

    if facts.currency and purchase_order.currency != facts.currency:
        results.append(
            rule(
                "PO_CURRENCY_MATCH",
                "review",
                "review",
                "The invoice currency differs from the purchase-order currency.",
                observed_value=f"invoice {facts.currency}",
                expected_value=f"purchase order {purchase_order.currency}",
                next_action="Amounts in different currencies are not compared automatically.",
            )
        )
    elif not facts.currency:
        results.append(
            rule(
                "PO_CURRENCY_MATCH",
                "skipped",
                "info",
                "Currency comparison was skipped because the invoice currency is unknown.",
                mandatory=False,
            )
        )
    else:
        results.append(
            rule(
                "PO_CURRENCY_MATCH",
                "pass",
                "info",
                f"Invoice and purchase order are both in {facts.currency}.",
                observed_value=facts.currency,
                expected_value=purchase_order.currency,
            )
        )

    if purchase_order.status != "open":
        results.append(
            rule(
                "PO_STATUS_OPEN",
                "review",
                "review",
                f"{purchase_order.po_number} is {purchase_order.status}.",
                observed_value=purchase_order.status,
                expected_value="open",
                next_action="Ask procurement to reopen or amend the purchase order.",
            )
        )
    else:
        results.append(
            rule(
                "PO_STATUS_OPEN",
                "pass",
                "info",
                f"{purchase_order.po_number} is open.",
                observed_value="open",
                expected_value="open",
            )
        )
    return results


def _duplicate_rules(inputs: PolicyInputs, facts: NormalizedFacts) -> list[RuleResult]:
    results: list[RuleResult] = []

    if inputs.duplicate_file:
        hit = inputs.duplicate_file
        results.append(
            rule(
                "DUPLICATE_FILE",
                "fail",
                "block",
                "This exact file has already been submitted and processed.",
                observed_value=hit.detail,
                expected_value="a file not seen before",
                next_action=(
                    "Open the original case instead of processing it again. No second commitment "
                    "was reserved."
                ),
            )
        )
    else:
        results.append(
            rule("DUPLICATE_FILE", "pass", "info", "This file has not been processed before.")
        )

    if inputs.duplicate_identity:
        hit = inputs.duplicate_identity
        conflicting = (
            hit.amount_minor is not None
            and facts.gross_total_minor is not None
            and hit.amount_minor != facts.gross_total_minor
        )
        description = (
            "This vendor and invoice number are already approved, and this document states a "
            "different amount. That is identity reuse with conflicting facts, not a new invoice."
            if conflicting
            else "This vendor and invoice number are already approved on another case."
        )
        results.append(
            rule(
                "DUPLICATE_IDENTITY",
                "fail",
                "block",
                description,
                observed_value=hit.detail,
                expected_value="an invoice identity not already approved",
                next_action=(
                    "Review the original approved case. If the vendor genuinely re-issued this "
                    "invoice, they must supply a new invoice number."
                ),
            )
        )
    elif (
        not facts.normalized_invoice_number or inputs.vendor is None or not inputs.vendor_confident
    ):
        results.append(
            rule(
                "DUPLICATE_IDENTITY",
                "review",
                "review",
                "Duplicate detection needs a confirmed vendor and invoice number; identity could "
                "not be established.",
                observed_value=(
                    f"vendor {'confirmed' if inputs.vendor_confident else 'unconfirmed'}, "
                    f"invoice number {facts.invoice_number or 'missing'}"
                ),
                expected_value="a confirmed vendor and a readable invoice number",
                next_action="Confirm the vendor and invoice number so duplicates can be ruled out.",
            )
        )
    else:
        results.append(
            rule(
                "DUPLICATE_IDENTITY",
                "pass",
                "info",
                "This vendor and invoice number are not already approved.",
                observed_value=f"{inputs.vendor.canonical_name} / {facts.invoice_number}",
            )
        )

    if inputs.case_already_approved:
        results.append(
            rule(
                "CASE_ALREADY_APPROVED",
                "fail",
                "block",
                "This case already holds an approved commitment.",
                observed_value="an active reservation exists for this case",
                expected_value="no active reservation for this case",
                next_action=(
                    "Approved cases cannot be edited or reprocessed in version 1, so an existing "
                    "commitment cannot be silently replaced or double counted."
                ),
            )
        )
    return results


def _budget_rule(
    inputs: PolicyInputs, facts: NormalizedFacts, config: PolicyConfig
) -> tuple[PoComparison, RuleResult]:
    purchase_order = inputs.purchase_order
    comparison = PoComparison(
        invoice_gross_minor=facts.gross_total_minor,
        currency=facts.currency,
        committed_before_minor=inputs.committed_before_minor,
    )
    if purchase_order is None or facts.gross_total_minor is None:
        comparison.verdict = "not_evaluated"
        return comparison, rule(
            "PO_BUDGET_TOLERANCE",
            "skipped",
            "info",
            "The purchase-order balance check could not run.",
            observed_value=(
                "no purchase order resolved" if purchase_order is None else "no invoice total"
            ),
            next_action="Resolve the purchase order and invoice total, then rerun the checks.",
            mandatory=False,
        )

    currency = purchase_order.currency
    tolerance = config.tolerance_minor(purchase_order.approved_total_minor)
    allowed = purchase_order.approved_total_minor + tolerance
    projected = inputs.committed_before_minor + facts.gross_total_minor

    comparison.po_number = purchase_order.po_number
    comparison.approved_total_minor = purchase_order.approved_total_minor
    comparison.tolerance_minor = tolerance
    comparison.allowed_total_minor = allowed
    comparison.projected_total_minor = projected
    comparison.remaining_nominal_before_minor = (
        purchase_order.approved_total_minor - inputs.committed_before_minor
    )
    comparison.remaining_allowed_before_minor = allowed - inputs.committed_before_minor

    arithmetic = (
        f"prior approved commitments {display_minor(inputs.committed_before_minor, currency)} "
        f"+ this invoice {display_minor(facts.gross_total_minor, currency)} "
        f"= {display_minor(projected, currency)}; "
        f"limit {display_minor(purchase_order.approved_total_minor, currency)} "
        f"+ tolerance {display_minor(tolerance, currency)} "
        f"= {display_minor(allowed, currency)}"
    )

    if projected > allowed:
        comparison.verdict = "above_tolerance"
        return comparison, rule(
            "PO_BUDGET_TOLERANCE",
            "review",
            "review",
            f"Approving this invoice would take {purchase_order.po_number} above its tolerated "
            "limit.",
            observed_value=arithmetic,
            expected_value=(
                f"projected commitments at or below {display_minor(allowed, currency)}"
            ),
            next_action=(
                "Ask procurement to amend the purchase order, or request a corrected invoice. "
                "Nothing was reserved for this invoice."
            ),
        )

    if projected > purchase_order.approved_total_minor:
        comparison.verdict = "within_limit"
        comparison.within_tolerance_overage = True
        overage = projected - purchase_order.approved_total_minor
        return comparison, rule(
            "PO_BUDGET_TOLERANCE",
            "pass",
            "warning",
            f"This invoice exceeds the nominal value of {purchase_order.po_number} by "
            f"{display_minor(overage, currency)}, which is inside the tolerated allowance.",
            observed_value=arithmetic,
            expected_value=f"at or below {display_minor(allowed, currency)}",
            mandatory=False,
        )

    comparison.verdict = "within_limit"
    return comparison, rule(
        "PO_BUDGET_TOLERANCE",
        "pass",
        "info",
        f"Commitments against {purchase_order.po_number} stay within its approved value.",
        observed_value=arithmetic,
        expected_value=f"at or below {display_minor(allowed, currency)}",
    )


def build_outcome(
    decision: Decision,
    results: list[RuleResult],
    facts: NormalizedFacts,
    comparison: PoComparison,
    inputs: PolicyInputs,
) -> DecisionOutcome:
    """Compose the reviewer-facing explanation from the run's own numbers."""
    currency = facts.currency or comparison.currency or "INR"
    amount = (
        display_minor(facts.gross_total_minor, currency)
        if facts.gross_total_minor is not None
        else "an amount that could not be read"
    )
    vendor_name = (
        inputs.vendor.canonical_name
        if inputs.vendor and inputs.vendor_confident
        else (inputs.invoice.vendor_name or "an unidentified vendor")
    )
    invoice_label = facts.invoice_number or "an invoice with no readable number"

    driving = [r for r in results if (r.blocks if decision == "BLOCKED" else r.needs_review)]
    reasons = [r.description for r in driving]
    actions = [r.next_action for r in driving if r.next_action]

    if decision == "APPROVED":
        warnings = [
            r.description for r in results if r.severity == "warning" and r.status == "pass"
        ]
        headline = f"Approved: {invoice_label} from {vendor_name} for {amount}."
        detail = [headline]
        if comparison.po_number:
            detail.append(
                f"Commitments against {comparison.po_number} move to "
                f"{display_minor(comparison.projected_total_minor or 0, currency)} of an allowed "
                f"{display_minor(comparison.allowed_total_minor or 0, currency)}."
            )
        detail.append(
            "A commitment was reserved against the purchase order. No payment is initiated by "
            "this prototype."
        )
        detail.extend(warnings)
        return DecisionOutcome(
            decision=decision,
            headline=headline,
            explanation=" ".join(detail),
            primary_reasons=[r.description for r in results if r.status == "pass" and r.mandatory][
                :4
            ],
            next_actions=["Release the invoice to the payment run in your accounting system."],
        )

    if decision == "BLOCKED":
        headline = f"Blocked: {invoice_label} from {vendor_name} for {amount}."
        return DecisionOutcome(
            decision=decision,
            headline=headline,
            explanation=" ".join([headline, *reasons, "Nothing was reserved for this invoice."]),
            primary_reasons=reasons,
            next_actions=actions,
            blocking_codes=[r.code for r in driving],
        )

    headline = f"Needs review: {invoice_label} from {vendor_name} for {amount}."
    explanation_parts = [headline, *reasons]
    if comparison.verdict == "above_tolerance" and comparison.po_number:
        explanation_parts.append(
            f"Approving it would bring {comparison.po_number} commitments to "
            f"{display_minor(comparison.projected_total_minor or 0, currency)}, above the "
            f"{display_minor(comparison.allowed_total_minor or 0, currency)} limit. Prior approved "
            f"commitments are {display_minor(comparison.committed_before_minor, currency)}."
        )
    explanation_parts.append("Nothing was reserved for this invoice.")
    return DecisionOutcome(
        decision=decision,
        headline=headline,
        explanation=" ".join(explanation_parts),
        primary_reasons=reasons,
        next_actions=actions,
        review_codes=[r.code for r in driving],
    )


def format_amount(minor: int | None, currency: str = "INR") -> str | None:
    return None if minor is None else format_minor(minor, currency)

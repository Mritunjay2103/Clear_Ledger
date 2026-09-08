"""Rule results and decision composition.

Precedence is explicit and total:

1. any reliable BLOCK finding  -> BLOCKED
2. otherwise any REVIEW finding -> REVIEW
3. otherwise, every mandatory check passed -> APPROVED

A check whose prerequisite evidence is missing is *unresolved*, never passed, so
absence of information can never produce an approval.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuleStatus = Literal["pass", "fail", "review", "skipped"]
RuleSeverity = Literal["info", "warning", "review", "block"]
Decision = Literal["APPROVED", "REVIEW", "BLOCKED"]


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    status: RuleStatus
    severity: RuleSeverity
    description: str
    observed_value: str | None = None
    expected_value: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    next_action: str | None = None
    mandatory: bool = True

    @property
    def blocks(self) -> bool:
        return self.severity == "block" and self.status in {"fail", "review"}

    @property
    def needs_review(self) -> bool:
        return self.severity == "review" and self.status in {"fail", "review", "skipped"}


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Decision
    headline: str
    explanation: str
    primary_reasons: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    blocking_codes: list[str] = Field(default_factory=list)
    review_codes: list[str] = Field(default_factory=list)


def compose_decision(results: list[RuleResult]) -> tuple[Decision, list[str], list[str]]:
    """Return the decision plus the codes that caused it."""
    blocking = [result.code for result in results if result.blocks]
    reviewing = [result.code for result in results if result.needs_review]
    if blocking:
        return "BLOCKED", blocking, reviewing
    if reviewing:
        return "REVIEW", blocking, reviewing
    return "APPROVED", [], []


def rule(
    code: str,
    status: RuleStatus,
    severity: RuleSeverity,
    description: str,
    *,
    observed_value: str | None = None,
    expected_value: str | None = None,
    evidence_refs: list[str] | None = None,
    next_action: str | None = None,
    mandatory: bool = True,
) -> RuleResult:
    return RuleResult(
        code=code,
        status=status,
        severity=severity,
        description=description,
        observed_value=observed_value,
        expected_value=expected_value,
        evidence_refs=evidence_refs or [],
        next_action=next_action,
        mandatory=mandatory,
    )


RULE_DESCRIPTIONS: dict[str, str] = {
    "DOC_KIND_SUPPORTED": "The document is a positive invoice, not a credit note.",
    "FIELDS_PRESENT": "Every field required to evaluate the invoice was extracted.",
    "EVIDENCE_SUPPORTED": "Each critical value is backed by a verified source excerpt.",
    "DATE_VALID": "The invoice date is present and unambiguous.",
    "CURRENCY_SUPPORTED": (
        "The invoice currency is explicit and supported for automatic evaluation."
    ),
    "AMOUNT_POSITIVE": "The invoice total is a positive amount.",
    "ARITHMETIC_TOTALS": "Printed subtotal plus tax agrees with the printed invoice total.",
    "ARITHMETIC_LINE_ITEMS": "Line amounts sum to the printed subtotal.",
    "VENDOR_RESOLVED": "The printed vendor matches exactly one vendor in reference data.",
    "VENDOR_STATUS": "The resolved vendor is approved for processing.",
    "PO_REFERENCE_PRESENT": "The invoice prints an explicit purchase-order reference.",
    "PO_RESOLVED": "The referenced purchase order exists in reference data.",
    "PO_VENDOR_MATCH": "The purchase order belongs to the invoicing vendor.",
    "PO_CURRENCY_MATCH": "The purchase order currency matches the invoice currency.",
    "PO_STATUS_OPEN": "The purchase order is open.",
    "DUPLICATE_FILE": "This exact file has not already been processed.",
    "DUPLICATE_IDENTITY": "This vendor and invoice number have not already been approved.",
    "PO_BUDGET_TOLERANCE": (
        "Approving this invoice keeps the purchase order within its tolerated limit."
    ),
    "CASE_ALREADY_APPROVED": "This case has not already produced an approved reservation.",
}

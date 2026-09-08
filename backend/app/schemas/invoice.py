"""Typed invoice facts produced by any extraction provider.

Amounts and identifiers stay strings all the way through this layer. They are
converted to Decimal/minor units only in the validation stage, where a failure
can be reported as a rule result instead of a parse crash.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Provenance = Literal["pdf_text", "ocr", "human_correction"]
DocumentKind = Literal["invoice", "credit_note", "unknown"]
QualityLabel = Literal["verified", "uncertain", "missing", "human_supplied"]

#: Fields that must be reliable before an invoice can be approved automatically.
CRITICAL_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "gross_total",
)


class Evidence(BaseModel):
    """Where a value came from. Bounding boxes are omitted rather than invented."""

    model_config = ConfigDict(extra="ignore")

    page: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=400)
    provenance: Provenance = "pdf_text"


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    net_amount: str | None = None
    tax_amount: str | None = None


class ExtractedInvoice(BaseModel):
    """Facts asserted about the document. Never facts imported from a PO."""

    model_config = ConfigDict(extra="ignore")

    vendor_name: str | None = None
    vendor_reference: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    explicit_po_number: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    tax_total: str | None = None
    gross_total: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    line_items_complete: bool = False
    line_amounts_tax_inclusive: bool | None = None
    document_kind: DocumentKind = "unknown"
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    def populated_critical_fields(self) -> list[str]:
        return [name for name in CRITICAL_FIELDS if getattr(self, name, None)]


class ExtractionMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str | None = None
    parser_version: str | None = None
    prompt_hash: str | None = None
    duration_ms: int = 0
    repair_attempted: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None
    pages_total: int = 0
    pages_ocr: list[int] = Field(default_factory=list)
    ocr_engine: str | None = None
    notes: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice: ExtractedInvoice
    metadata: ExtractionMetadata


class FieldQuality(BaseModel):
    """Evidence-based quality label. Deliberately not a confidence percentage."""

    model_config = ConfigDict(extra="ignore")

    field: str
    label: QualityLabel
    reason: str | None = None
    evidence: Evidence | None = None

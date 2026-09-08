"""Extraction, and the evidence check that keeps it honest.

The rules provider is deterministic, so these assert exact values. The evidence
check is applied to whatever a provider returns — rules or model — so a value
with no support in the page text gets downgraded rather than trusted.
"""

from __future__ import annotations

import pytest

from app.schemas.invoice import ExtractedInvoice
from app.services.extraction import ReadDocument, read_document, run_extraction, verify_evidence


def read(path) -> ReadDocument:
    return read_document(path)


def extract(path):
    return run_extraction(read(path), provider_name="rules")


def test_a_clean_invoice_yields_the_printed_values(samples_dir) -> None:
    invoice = extract(samples_dir.path("happy-path")).invoice

    assert invoice.vendor_name == "Saffron Office Systems Pvt. Ltd."
    assert invoice.invoice_number == "INV-1001"
    assert invoice.invoice_date == "2026-08-24"
    assert invoice.currency == "INR"
    assert invoice.gross_total == "11800.00"
    assert invoice.explicit_po_number == "PO-1001"
    assert invoice.document_kind == "invoice"


def test_every_value_is_returned_with_the_text_it_came_from(samples_dir) -> None:
    document = read(samples_dir.path("happy-path"))
    result = run_extraction(document, provider_name="rules")
    qualities, _ = verify_evidence(result.invoice, document)

    critical = {"vendor_name", "invoice_number", "invoice_date", "currency", "gross_total"}
    labels = {quality.field: quality.label for quality in qualities}
    for field in critical:
        assert labels[field] == "verified", f"{field} could not be confirmed in the page text"


def test_a_missing_field_is_reported_missing_rather_than_guessed(samples_dir) -> None:
    invoice = extract(samples_dir.path("r3-missing-number")).invoice

    assert invoice.invoice_number is None
    assert "invoice_number" in invoice.missing_fields
    # The rest of the invoice is still read.
    assert invoice.gross_total == "2360.00"


def test_a_credit_note_is_recognised_as_a_different_document(samples_dir) -> None:
    assert extract(samples_dir.path("r5-credit-note")).invoice.document_kind == "credit_note"


def test_a_foreign_currency_is_reported_as_printed(samples_dir) -> None:
    assert extract(samples_dir.path("r4-currency")).invoice.currency == "USD"


def test_instructions_embedded_in_the_document_are_flagged(samples_dir) -> None:
    invoice = extract(samples_dir.path("r6-prompt-injection")).invoice
    warnings = " ".join(invoice.extraction_warnings).lower()

    assert "instruction" in warnings
    # The printed PO number is still read — it is what the document says — but
    # the instruction alongside it is recorded as content, never obeyed. None of
    # the injected words leak into an extracted field.
    assert invoice.explicit_po_number == "PO-9999"
    values = " ".join(
        str(getattr(invoice, name) or "")
        for name in ("vendor_name", "invoice_number", "currency", "gross_total")
    ).lower()
    for phrase in ("approved", "override", "ignore", "skip"):
        assert phrase not in values


def test_an_unreadable_document_produces_nothing_rather_than_noise(samples_dir) -> None:
    invoice = extract(samples_dir.path("r7-unreadable-scan")).invoice

    assert invoice.gross_total is None
    assert invoice.invoice_number is None


def test_a_scanned_page_is_recorded_as_read_by_ocr(samples_dir) -> None:
    document = read(samples_dir.path("e3-scanned"))

    assert "ocr" in document.page_provenance
    result = run_extraction(document, provider_name="rules")
    assert result.metadata.pages_ocr
    assert result.metadata.ocr_engine


@pytest.mark.parametrize(
    "sample_id",
    [
        "happy-path",
        "e1-duplicate",
        "e2-part-1",
        "e2-part-2",
        "e2-part-3",
        "e4-arithmetic",
        "r1-blocked-vendor",
        "r2-wrong-vendor-po",
        "r5-credit-note",
        "r6-prompt-injection",
    ],
)
def test_no_amount_is_ever_returned_without_a_quotable_source(samples_dir, sample_id) -> None:
    document = read(samples_dir.path(sample_id))
    invoice = run_extraction(document, provider_name="rules").invoice

    for field in ("subtotal", "tax_total", "gross_total"):
        if getattr(invoice, field) is None:
            continue
        assert field in invoice.evidence, f"{sample_id}: {field} has a value but no evidence"
        assert invoice.evidence[field].excerpt.strip()


# --- the evidence check -----------------------------------------------------


def _document(*pages: str) -> ReadDocument:
    document = ReadDocument()
    document.page_texts = list(pages)
    document.page_provenance = ["pdf_text"] * len(pages)
    return document


def test_an_excerpt_the_document_does_not_contain_is_downgraded() -> None:
    invoice = ExtractedInvoice(
        vendor_name="Ghost Traders",
        gross_total="99999.00",
        currency="INR",
        evidence={
            "vendor_name": {"page": 1, "excerpt": "Ghost Traders", "provenance": "pdf_text"},
            "gross_total": {
                "page": 1,
                "excerpt": "Total payable 99,999.00",
                "provenance": "pdf_text",
            },
        },
    )
    document = _document("Invoice from Ghost Traders. Total payable 1,234.00")

    qualities, warnings = verify_evidence(invoice, document)
    labels = {quality.field: quality for quality in qualities}

    assert labels["vendor_name"].label == "verified"
    assert labels["gross_total"].label == "uncertain"
    assert labels["gross_total"].reason
    assert warnings, "an unsupported excerpt should be surfaced as a warning"


def test_a_value_offered_without_any_evidence_is_never_verified() -> None:
    invoice = ExtractedInvoice(invoice_number="INV-1", evidence={})
    qualities, _ = verify_evidence(invoice, _document("some page text"))
    labels = {quality.field: quality.label for quality in qualities}

    assert labels["invoice_number"] == "uncertain"
    assert labels["vendor_name"] == "missing"


def test_evidence_matching_ignores_harmless_spacing_differences() -> None:
    invoice = ExtractedInvoice(
        gross_total="11800.00",
        evidence={
            "gross_total": {
                "page": 1,
                "excerpt": "Grand  Total   INR 11,800.00",
                "provenance": "pdf_text",
            }
        },
    )
    document = _document("Grand Total INR 11,800.00")

    qualities, _ = verify_evidence(invoice, document)
    labels = {quality.field: quality.label for quality in qualities}
    assert labels["gross_total"] == "verified"

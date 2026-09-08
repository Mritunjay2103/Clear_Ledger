"""Normalization decides what counts as "the same" invoice or vendor.

It has to absorb harmless printing differences without ever merging two records
that a human would consider distinct.
"""

from __future__ import annotations

import pytest

from app.domain.normalization import (
    normalize_invoice_number,
    normalize_po_number,
    normalize_vendor_name,
    parse_invoice_date,
    token_similarity,
)


@pytest.mark.parametrize(
    "left,right",
    [
        ("Saffron Office Systems Pvt. Ltd.", "SAFFRON OFFICE SYSTEMS PVT LTD"),
        ("Saffron  Office\tSystems Pvt Ltd", "Saffron Office Systems Pvt Ltd"),
        ("Cedar Cloud Services Private Limited", "cedar cloud services private limited"),
    ],
)
def test_printing_differences_do_not_create_a_new_vendor(left: str, right: str) -> None:
    assert normalize_vendor_name(left) == normalize_vendor_name(right)


def test_genuinely_different_vendors_stay_different() -> None:
    assert normalize_vendor_name("Cedar Cloud Services") != normalize_vendor_name(
        "Cedar Cloud Systems"
    )
    assert normalize_vendor_name("Ironwood Logistics") != normalize_vendor_name("Ironwood Labs")


@pytest.mark.parametrize(
    "left,right",
    [
        ("INV-1001", "inv 1001"),
        ("INV-1001", "INV_1001"),
        ("INV-1001", "INV.1001"),
        ("INV-1001", "  INV - 1001  "),
        ("CCS/2026/0041", "ccs/2026/0041"),
    ],
)
def test_the_same_invoice_number_survives_reformatting(left: str, right: str) -> None:
    assert normalize_invoice_number(left) == normalize_invoice_number(right)


def test_different_invoice_numbers_are_never_merged() -> None:
    assert normalize_invoice_number("INV-1001") != normalize_invoice_number("INV-10010")
    assert normalize_invoice_number("CCS/2026/0041") != normalize_invoice_number("CCS/2026/0042")


def test_po_numbers_normalize_the_same_way() -> None:
    assert normalize_po_number("po 1001") == normalize_po_number("PO-1001")
    assert normalize_po_number("PO-1001") != normalize_po_number("PO-1002")


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("2026-08-24", "2026-08-24"),
        ("24 Aug 2026", "2026-08-24"),
        ("24 August 2026", "2026-08-24"),
        ("Aug 24, 2026", "2026-08-24"),
        ("August 24 2026", "2026-08-24"),
    ],
)
def test_unambiguous_dates_are_read(raw: str, iso: str) -> None:
    parsed = parse_invoice_date(raw)
    assert parsed.value is not None
    assert parsed.value.isoformat() == iso
    assert not parsed.ambiguous


def test_a_numeric_date_that_could_be_two_dates_is_flagged_not_guessed() -> None:
    parsed = parse_invoice_date("07/08/2026")
    assert parsed.ambiguous
    assert parsed.reason


def test_a_numeric_date_with_only_one_reading_is_accepted() -> None:
    parsed = parse_invoice_date("24/08/2026")
    assert parsed.value is not None
    assert parsed.value.isoformat() == "2026-08-24"
    assert not parsed.ambiguous


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "2026-13-45", "31 Feb 2026", None])
def test_an_unreadable_date_produces_no_date(raw) -> None:
    parsed = parse_invoice_date(raw)
    assert parsed.value is None


def test_similarity_is_only_used_to_rank_suggestions() -> None:
    high = token_similarity(
        normalize_vendor_name("Saffron Office Systems Pvt Ltd"),
        normalize_vendor_name("Saffron Office Systems Private Limited"),
    )
    low = token_similarity(
        normalize_vendor_name("Saffron Office Systems"),
        normalize_vendor_name("Ironwood Logistics"),
    )
    assert 0.0 <= low < high <= 1.0

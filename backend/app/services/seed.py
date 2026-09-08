"""Reference-data loading and validation.

The CSV files under ``data/reference`` are the editable source of truth. Seeding
is idempotent: rows are matched by their stable code and updated in place, and
nothing is ever deleted, so processing history keeps referring to real vendors
and purchase orders.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.db.models import PurchaseOrder, Vendor
from app.domain.money import MoneyError, parse_decimal, to_minor
from app.domain.normalization import normalize_po_number, normalize_vendor_name

REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
VENDORS_CSV = REFERENCE_DIR / "vendors.csv"
PURCHASE_ORDERS_CSV = REFERENCE_DIR / "purchase_orders.csv"

VALID_VENDOR_STATUS = {"approved", "blocked"}
VALID_PO_STATUS = {"open", "closed"}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    vendor_count: int = 0
    purchase_order_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Reference file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def validate_reference_data(
    vendors_csv: Path = VENDORS_CSV, purchase_orders_csv: Path = PURCHASE_ORDERS_CSV
) -> ValidationReport:
    """Check the CSVs before they touch the database."""
    report = ValidationReport()
    try:
        vendor_rows = _read_csv(vendors_csv)
        po_rows = _read_csv(purchase_orders_csv)
    except FileNotFoundError as exc:
        report.errors.append(str(exc))
        return report

    seen_codes: set[str] = set()
    seen_normalized: dict[str, str] = {}
    for index, row in enumerate(vendor_rows, start=2):
        code = row.get("vendor_code", "")
        name = row.get("canonical_name", "")
        if not code:
            report.errors.append(f"vendors.csv line {index}: vendor_code is required.")
        elif code in seen_codes:
            report.errors.append(f"vendors.csv line {index}: duplicate vendor_code {code!r}.")
        seen_codes.add(code)
        if not name:
            report.errors.append(f"vendors.csv line {index}: canonical_name is required.")
        normalized = normalize_vendor_name(name)
        if normalized in seen_normalized and seen_normalized[normalized] != code:
            report.errors.append(
                f"vendors.csv line {index}: {name!r} normalises to the same key as "
                f"vendor {seen_normalized[normalized]}."
            )
        seen_normalized[normalized] = code
        status = row.get("status", "")
        if status not in VALID_VENDOR_STATUS:
            report.errors.append(
                f"vendors.csv line {index}: status {status!r} must be one of "
                f"{sorted(VALID_VENDOR_STATUS)}."
            )
        for alias in _aliases(row):
            alias_key = normalize_vendor_name(alias)
            if alias_key in seen_normalized and seen_normalized[alias_key] != code:
                report.errors.append(
                    f"vendors.csv line {index}: alias {alias!r} collides with vendor "
                    f"{seen_normalized[alias_key]}."
                )
            seen_normalized.setdefault(alias_key, code)
    report.vendor_count = len(vendor_rows)

    seen_po: set[str] = set()
    for index, row in enumerate(po_rows, start=2):
        number = row.get("po_number", "")
        if not number:
            report.errors.append(f"purchase_orders.csv line {index}: po_number is required.")
        key = normalize_po_number(number)
        if key in seen_po:
            report.errors.append(
                f"purchase_orders.csv line {index}: duplicate purchase order {number!r}."
            )
        seen_po.add(key)
        if row.get("vendor_code") not in seen_codes:
            report.errors.append(
                f"purchase_orders.csv line {index}: vendor_code "
                f"{row.get('vendor_code')!r} is not in vendors.csv."
            )
        if row.get("status") not in VALID_PO_STATUS:
            report.errors.append(
                f"purchase_orders.csv line {index}: status {row.get('status')!r} must be one of "
                f"{sorted(VALID_PO_STATUS)}."
            )
        try:
            amount = parse_decimal(row.get("approved_total"))
            if amount < 0:
                report.errors.append(
                    f"purchase_orders.csv line {index}: approved_total must not be negative."
                )
        except MoneyError as exc:
            report.errors.append(f"purchase_orders.csv line {index}: approved_total {exc}.")
    report.purchase_order_count = len(po_rows)
    return report


def _aliases(row: dict[str, str]) -> list[str]:
    raw = row.get("aliases", "")
    return [alias.strip() for alias in raw.split("|") if alias.strip()]


def seed_reference_data(
    session: Session,
    vendors_csv: Path = VENDORS_CSV,
    purchase_orders_csv: Path = PURCHASE_ORDERS_CSV,
) -> dict[str, int]:
    """Insert or update reference rows. Existing history is untouched."""
    report = validate_reference_data(vendors_csv, purchase_orders_csv)
    if not report.ok:
        raise ValueError("Reference data is invalid:\n" + "\n".join(report.errors))

    created_vendors = updated_vendors = 0
    code_to_vendor: dict[str, Vendor] = {}
    for row in _read_csv(vendors_csv):
        code = row["vendor_code"]
        vendor = session.scalar(select(Vendor).where(Vendor.vendor_code == code))
        if vendor is None:
            vendor = Vendor(vendor_code=code)
            session.add(vendor)
            created_vendors += 1
        else:
            updated_vendors += 1
        vendor.canonical_name = row["canonical_name"]
        vendor.normalized_name = normalize_vendor_name(row["canonical_name"])
        vendor.explicit_aliases = _aliases(row)
        vendor.status = row["status"]
        vendor.supported_currency = row.get("supported_currency") or "INR"
        session.flush()
        code_to_vendor[code] = vendor

    created_pos = updated_pos = 0
    for row in _read_csv(purchase_orders_csv):
        normalized = normalize_po_number(row["po_number"])
        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.normalized_po_number == normalized)
        )
        if purchase_order is None:
            purchase_order = PurchaseOrder(normalized_po_number=normalized)
            session.add(purchase_order)
            created_pos += 1
        else:
            updated_pos += 1
        currency = row.get("currency") or "INR"
        purchase_order.po_number = row["po_number"]
        purchase_order.vendor_id = code_to_vendor[row["vendor_code"]].id
        purchase_order.currency = currency
        purchase_order.approved_total_minor = to_minor(
            parse_decimal(row["approved_total"]), currency
        )
        purchase_order.status = row["status"]
        purchase_order.description = row.get("description") or None
        session.flush()

    session.commit()
    return {
        "vendors_created": created_vendors,
        "vendors_updated": updated_vendors,
        "purchase_orders_created": created_pos,
        "purchase_orders_updated": updated_pos,
    }

"""Developer aid: run the deterministic parser over the sample PDFs and print facts.

python scripts/inspect_extraction.py [--file data/samples/pdf/xyz.pdf] [--text]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.providers.base import extraction_json_schema
from app.providers.rules import RulesProvider
from app.services.extraction import read_document, verify_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file")
    parser.add_argument("--text", action="store_true", help="print raw page text")
    args = parser.parse_args()

    targets = (
        [Path(args.file)]
        if args.file
        else sorted((PROJECT_ROOT / "data" / "samples" / "pdf").glob("*.pdf"))
    )
    provider = RulesProvider()
    for path in targets:
        document = read_document(path)
        if args.text:
            print("=" * 100)
            print(path.name)
            for index, text in enumerate(document.page_texts, start=1):
                print(f"--- page {index} ({document.page_provenance[index - 1]}) ---")
                print(text)
            continue
        result = provider.extract(document.page_texts, extraction_json_schema())
        invoice = result.invoice
        qualities, warnings = verify_evidence(invoice, document)
        print("=" * 100)
        print(path.name, f"[ocr pages: {document.ocr_pages}]")
        for field in (
            "vendor_name",
            "invoice_number",
            "invoice_date",
            "explicit_po_number",
            "currency",
            "subtotal",
            "tax_total",
            "gross_total",
            "document_kind",
        ):
            print(f"  {field:22} {getattr(invoice, field)!r}")
        print(
            f"  {'line_items':22} {len(invoice.line_items)} complete={invoice.line_items_complete}"
        )
        print(f"  {'missing':22} {invoice.missing_fields}")
        print(f"  {'ambiguities':22} {invoice.ambiguities}")
        print(f"  {'quality':22} " + ", ".join(f"{q.field}={q.label}" for q in qualities))
        for warning in warnings:
            print(f"  ! {warning}")


if __name__ == "__main__":
    main()

"""Deterministic, transparent invoice parser.

It reads the document text only. Filenames, database identifiers, hashes and the
fixture manifest are not visible to this module by construction — it receives a
list of page strings and nothing else.

The parser recognises clearly labelled invoice layouts. Anything it cannot
justify from the text is left unresolved and reported in ``missing_fields`` or
``ambiguities``, which downstream policy turns into a review rather than a
guess.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.providers.base import ExtractionProvider, ProviderAvailability
from app.schemas.invoice import (
    Evidence,
    ExtractedInvoice,
    ExtractionMetadata,
    ExtractionResult,
    LineItem,
)

PARSER_VERSION = "rules-1.0.0"

_AMOUNT = r"(?:INR|Rs\.?|₹)?\s*\(?-?[\d,]+(?:\.\d{1,2})?\)?"
_AMOUNT_RE = re.compile(_AMOUNT)
_TRAILING_AMOUNT_RE = re.compile(rf"(?P<amount>{_AMOUNT})\s*$")

_BUYER_MARKERS = (
    "bill to",
    "billed to",
    "invoice to",
    "buyer",
    "customer",
    "ship to",
    "sold to",
    "consignee",
    "recipient",
)
_DOC_TITLES = (
    "tax invoice",
    "invoice",
    "credit note",
    "proforma invoice",
    "gst invoice",
    "commercial invoice",
    "bill of supply",
    "statement",
)
_VENDOR_LABELS = ("supplier", "vendor", "seller", "from", "issued by", "service provider")
_INVOICE_NUMBER_LABELS = (
    "invoice no",
    "invoice number",
    "invoice #",
    "invoice num",
    "bill no",
    "bill number",
    "document no",
    "document number",
    "tax invoice no",
    "inv no",
    "invoice ref",
)
_INVOICE_DATE_LABELS = (
    "invoice date",
    "date of invoice",
    "bill date",
    "dated",
    "issue date",
    "date of issue",
    "document date",
    "date",
)
_PO_LABELS = (
    "po number",
    "po no",
    "p.o. no",
    "p.o number",
    "purchase order no",
    "purchase order number",
    "purchase order",
    "buyer order ref",
    "order ref",
    "customer po",
    "po ref",
    "po#",
    "po #",
    "against po",
)
_CURRENCY_LABELS = ("currency", "invoice currency", "billing currency")
_SUBTOTAL_LABELS = (
    "subtotal",
    "sub total",
    "sub-total",
    "taxable value",
    "net amount",
    "amount before tax",
    "total before tax",
    "net total",
    "taxable amount",
)
_TAX_LABELS = (
    "total tax",
    "tax amount",
    "gst amount",
    "gst total",
    "igst",
    "total gst",
    "tax total",
    "cgst + sgst",
    "tax",
)
_GROSS_LABELS = (
    "grand total",
    "invoice total",
    "total amount payable",
    "total payable",
    "amount payable",
    "total amount due",
    "total invoice value",
    "total amount",
    "total due",
    "net payable",
    "total",
)
_BALANCE_LABELS = ("balance due", "amount due", "outstanding balance")
_PAID_LABELS = (
    "amount paid",
    "less advance",
    "advance paid",
    "payments received",
    "prior payments",
)
_LINE_HEADER_TOKENS = ("description", "particulars", "item", "service")
_LINE_HEADER_AMOUNT_TOKENS = ("amount", "total", "value")

_LINE_ITEM_FULL_RE = re.compile(
    r"^(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>[\d,]+\.\d{2})\s+(?P<amount>[\d,]+\.\d{2})$"
)
_LINE_ITEM_MIN_RE = re.compile(r"^(?P<desc>\D.*?)\s+(?P<amount>[\d,]+\.\d{2})$")


@dataclass
class SourceLine:
    page: int
    index: int
    text: str

    @property
    def lowered(self) -> str:
        return self.text.casefold()


@dataclass
class LabelHit:
    value: str
    line: SourceLine


def _split_lines(page_texts: list[str]) -> list[SourceLine]:
    lines: list[SourceLine] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        for index, raw in enumerate((page_text or "").splitlines()):
            text = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
            if text:
                lines.append(SourceLine(page=page_number, index=index, text=text))
    return lines


def _label_pattern(label: str) -> re.Pattern[str]:
    """Label at the start of a line, with an optional separator."""
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(rf"^\W*{escaped}\s*[:\-#]?\s*(?P<value>.*)$", re.IGNORECASE)


def _inline_label_pattern(label: str) -> re.Pattern[str]:
    """Label anywhere on a line, but only when an explicit separator follows.

    Side-by-side header blocks flatten into one text line, so ``Bill To: Acme
    Invoice No: INV-1001`` has to remain findable. Requiring ``:`` or ``#`` keeps
    a stray word such as "total" in prose from matching.
    """
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(rf"(?:^|\s){escaped}\s*[:#]\s*(?P<value>.*)$", re.IGNORECASE)


def _find_labeled(
    lines: list[SourceLine], labels: tuple[str, ...], *, allow_next_line: bool = True
) -> LabelHit | None:
    """Find ``Label: value`` on one line, or a label whose value sits below it.

    Labels are tried in the order given, so a specific label (``Grand Total``)
    wins over a generic one (``Total``) that would otherwise match first.
    Line-anchored matches are preferred over mid-line ones.
    """
    for label in labels:
        pattern = _label_pattern(label)
        for position, line in enumerate(lines):
            match = pattern.match(line.text)
            if not match:
                continue
            value = match.group("value").strip(" :-#\t")
            if value:
                return LabelHit(value=value, line=line)
            if allow_next_line and position + 1 < len(lines):
                nxt = lines[position + 1]
                if nxt.page == line.page and nxt.text:
                    return LabelHit(value=nxt.text.strip(), line=line)
    for label in labels:
        pattern = _inline_label_pattern(label)
        for line in lines:
            match = pattern.search(line.text)
            if match:
                value = match.group("value").strip(" :-#\t")
                if value:
                    return LabelHit(value=value, line=line)
    return None


def _find_labeled_amount(lines: list[SourceLine], labels: tuple[str, ...]) -> LabelHit | None:
    """Find a labelled monetary value, taking the amount at the end of the line."""
    for builder in (_label_pattern, _inline_label_pattern):
        for label in labels:
            pattern = builder(label)
            for line in lines:
                match = (
                    pattern.match(line.text)
                    if builder is _label_pattern
                    else pattern.search(line.text)
                )
                if not match:
                    continue
                remainder = match.group("value")
                amount_match = _TRAILING_AMOUNT_RE.search(remainder) or _AMOUNT_RE.search(remainder)
                if amount_match:
                    token = amount_match.group(0).strip()
                    if re.search(r"\d", token):
                        return LabelHit(value=token, line=line)
    return None


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/.]*(?:\s[A-Za-z0-9\-_/.]+){0,2}$")


def _identifier_from(value: str) -> str:
    """Read an identifier that may legitimately contain spaces.

    ``inv 1001`` and ``INV-1001`` are both whole identifiers, so the value is
    kept intact when the remainder of the line looks like nothing but an
    identifier. Anything longer or containing further labels falls back to the
    first token rather than swallowing unrelated text.
    """
    text = value.strip().strip(".,;")
    if not text:
        return ""
    if len(text) <= 40 and _IDENTIFIER_RE.match(text):
        return text
    return text.split()[0].strip(".,;")


def _clean_amount(token: str) -> str:
    text = token.strip()
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[()]", "", text)
    text = re.sub(r"(?i)^(inr|rs\.?|₹)\s*", "", text).strip()
    text = text.replace(",", "").replace(" ", "")
    if negative and not text.startswith("-"):
        text = "-" + text
    return text


def _excerpt(line: SourceLine) -> str:
    return line.text[:300]


def _detect_currency(lines: list[SourceLine]) -> tuple[str | None, SourceLine | None, str | None]:
    """Resolve the invoice currency, refusing to guess an ambiguous symbol."""
    labeled = _find_labeled(lines, _CURRENCY_LABELS, allow_next_line=False)
    if labeled:
        token = labeled.value.split()[0].strip(".,")
        if re.fullmatch(r"[A-Za-z]{3}", token):
            return token.upper(), labeled.line, None
        if token in {"₹", "Rs", "Rs."}:
            return "INR", labeled.line, None

    for line in lines:
        if re.search(r"(?i)\b(?:INR|₹|Rs\.?)\s*[\d,]", line.text):
            return "INR", line, None

    for line in lines:
        if re.search(r"\$\s*[\d,]", line.text):
            return (
                None,
                line,
                (
                    "A bare '$' appears next to amounts; the currency cannot be determined "
                    "from the document."
                ),
            )
    return None, None, None


def _detect_vendor(lines: list[SourceLine]) -> tuple[str | None, SourceLine | None]:
    """Identify the issuing party, never the addressee."""
    labeled = _find_labeled(lines, _VENDOR_LABELS, allow_next_line=True)
    if labeled and labeled.value:
        candidate = labeled.value.strip()
        if len(candidate) >= 3 and not _looks_like_buyer_marker(candidate):
            return candidate, labeled.line

    first_page = [line for line in lines if line.page == 1][:12]
    for line in first_page:
        lowered = line.lowered.strip(" :")
        if any(lowered.startswith(marker) for marker in _BUYER_MARKERS):
            break
        if lowered in _DOC_TITLES or lowered.rstrip(":") in _DOC_TITLES:
            continue
        if len(line.text) < 3 or not re.search(r"[A-Za-z]{3}", line.text):
            continue
        if re.match(r"(?i)^(gstin|pan|cin|phone|email|tel|www|http|address)\b", line.text):
            continue
        if re.match(r"(?i)^(invoice|bill|document|date|po)\b", line.text):
            continue
        return line.text.strip(), line
    return None, None


def _looks_like_buyer_marker(text: str) -> bool:
    lowered = text.casefold()
    return any(lowered.startswith(marker) for marker in _BUYER_MARKERS)


def _parse_line_items(lines: list[SourceLine]) -> tuple[list[LineItem], bool, bool | None]:
    """Parse the itemised table if the document has one.

    Returns the items, whether the parse looks complete, and whether line
    amounts are tax inclusive when the document says so. A bundled invoice with
    no table is legitimate; the caller must not invent rows for it.
    """
    header_position: int | None = None
    for position, line in enumerate(lines):
        lowered = line.lowered
        if any(token in lowered for token in _LINE_HEADER_TOKENS) and any(
            token in lowered for token in _LINE_HEADER_AMOUNT_TOKENS
        ):
            header_position = position
            break
    if header_position is None:
        return [], False, None

    tax_inclusive: bool | None = None
    header_text = lines[header_position].lowered
    if "inclusive of tax" in header_text or "tax inclusive" in header_text:
        tax_inclusive = True
    elif "excl" in header_text and "tax" in header_text:
        tax_inclusive = False

    items: list[LineItem] = []
    complete = True
    for line in lines[header_position + 1 :]:
        lowered = line.lowered
        if any(
            lowered.startswith(label) for label in _SUBTOTAL_LABELS + _GROSS_LABELS + _TAX_LABELS
        ):
            break
        if not _AMOUNT_RE.search(line.text):
            continue
        full = _LINE_ITEM_FULL_RE.match(line.text)
        if full:
            items.append(
                LineItem(
                    description=full.group("desc").strip(),
                    quantity=full.group("qty"),
                    unit_price=_clean_amount(full.group("unit")),
                    net_amount=_clean_amount(full.group("amount")),
                )
            )
            continue
        minimal = _LINE_ITEM_MIN_RE.match(line.text)
        if minimal:
            items.append(
                LineItem(
                    description=minimal.group("desc").strip(),
                    net_amount=_clean_amount(minimal.group("amount")),
                )
            )
            complete = False
            continue
        complete = False
    if not items:
        return [], False, tax_inclusive
    return items, complete and all(item.net_amount for item in items), tax_inclusive


_INJECTION_MARKERS = (
    "ignore all previous",
    "ignore previous instructions",
    "system override",
    "mark this invoice approved",
    "set decision",
    "skip every policy",
    "skip all checks",
    "you are now",
    "disregard the rules",
    "pre-authorised for any amount",
)


def _detect_injection_attempt(lines: list[SourceLine]) -> str | None:
    """Report instruction-like text found in the document body.

    This parser cannot be steered by it — it only matches labels — but the same
    text is fed to model providers, so a reviewer deserves to know it is there.
    """
    hits = [
        line.text[:120]
        for line in lines
        if any(marker in line.lowered for marker in _INJECTION_MARKERS)
    ]
    if not hits:
        return None
    return (
        "The document contains text that reads like an instruction to the extraction system "
        f"({hits[0]!r}). It was treated as document content and had no effect on the decision."
    )


class RulesProvider(ExtractionProvider):
    name = "rules"

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=True,
            detail=(
                "Deterministic parser for clearly labelled invoice layouts. Arbitrary or "
                "unlabelled layouts may need a model or human review."
            ),
            model=PARSER_VERSION,
        )

    def extract(self, page_texts: list[str], extraction_schema: dict) -> ExtractionResult:
        started = time.perf_counter()
        lines = _split_lines(page_texts)
        invoice = ExtractedInvoice()
        evidence: dict[str, Evidence] = {}
        warnings: list[str] = []
        ambiguities: list[str] = []

        if not lines:
            invoice.missing_fields = [
                "vendor_name",
                "invoice_number",
                "invoice_date",
                "currency",
                "gross_total",
            ]
            invoice.extraction_warnings = ["No readable text was found in the document."]
            return ExtractionResult(
                invoice=invoice,
                metadata=ExtractionMetadata(
                    provider=self.name,
                    parser_version=PARSER_VERSION,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    pages_total=len(page_texts),
                ),
            )

        joined = " ".join(line.lowered for line in lines)
        if "credit note" in joined:
            invoice.document_kind = "credit_note"
        elif "invoice" in joined:
            invoice.document_kind = "invoice"

        vendor_name, vendor_line = _detect_vendor(lines)
        if vendor_name:
            invoice.vendor_name = vendor_name
            if vendor_line:
                evidence["vendor_name"] = Evidence(
                    page=vendor_line.page, excerpt=_excerpt(vendor_line)
                )

        gstin = _find_labeled(lines, ("gstin", "gst no", "gst number"), allow_next_line=False)
        if gstin:
            invoice.vendor_reference = gstin.value.split()[0] if gstin.value else None

        number_hit = _find_labeled(lines, _INVOICE_NUMBER_LABELS, allow_next_line=False)
        if number_hit:
            token = _identifier_from(number_hit.value)
            if token:
                invoice.invoice_number = token
                evidence["invoice_number"] = Evidence(
                    page=number_hit.line.page, excerpt=_excerpt(number_hit.line)
                )

        date_hit = _find_labeled(lines, _INVOICE_DATE_LABELS, allow_next_line=False)
        if date_hit:
            # OCR frequently leaves separator artefacts between a label and its
            # value, so leading punctuation is trimmed before parsing.
            value = re.sub(r"^[^0-9A-Za-z]+", "", date_hit.value.strip())
            value = re.split(r"\s{2,}", value)[0].strip()
            if value:
                invoice.invoice_date = value
                evidence["invoice_date"] = Evidence(
                    page=date_hit.line.page, excerpt=_excerpt(date_hit.line)
                )

        po_hit = _find_labeled(lines, _PO_LABELS, allow_next_line=False)
        if po_hit:
            token = _identifier_from(po_hit.value)
            if token and re.search(r"\d", token):
                invoice.explicit_po_number = token
                evidence["explicit_po_number"] = Evidence(
                    page=po_hit.line.page, excerpt=_excerpt(po_hit.line)
                )

        currency, currency_line, currency_note = _detect_currency(lines)
        if currency:
            invoice.currency = currency
            if currency_line:
                evidence["currency"] = Evidence(
                    page=currency_line.page, excerpt=_excerpt(currency_line)
                )
        elif currency_note:
            ambiguities.append(currency_note)

        subtotal_hit = _find_labeled_amount(lines, _SUBTOTAL_LABELS)
        if subtotal_hit:
            invoice.subtotal = _clean_amount(subtotal_hit.value)
            evidence["subtotal"] = Evidence(
                page=subtotal_hit.line.page, excerpt=_excerpt(subtotal_hit.line)
            )

        tax_hit = _find_labeled_amount(lines, _TAX_LABELS)
        if tax_hit:
            invoice.tax_total = _clean_amount(tax_hit.value)
            evidence["tax_total"] = Evidence(page=tax_hit.line.page, excerpt=_excerpt(tax_hit.line))

        gross_hit = _find_labeled_amount(lines, _GROSS_LABELS)
        if gross_hit:
            invoice.gross_total = _clean_amount(gross_hit.value)
            evidence["gross_total"] = Evidence(
                page=gross_hit.line.page, excerpt=_excerpt(gross_hit.line)
            )

        balance_hit = _find_labeled_amount(lines, _BALANCE_LABELS)
        if balance_hit and invoice.gross_total:
            balance_value = _clean_amount(balance_hit.value)
            if balance_value != invoice.gross_total:
                ambiguities.append(
                    f"Printed balance due ({balance_value}) differs from the invoice total "
                    f"({invoice.gross_total}); the invoice total was used."
                )
        paid_hit = _find_labeled_amount(lines, _PAID_LABELS)
        if paid_hit:
            warnings.append(
                "The document records a prior payment or advance "
                f"({_clean_amount(paid_hit.value)}); it does not change the invoice total."
            )

        items, complete, tax_inclusive = _parse_line_items(lines)
        invoice.line_items = items
        invoice.line_items_complete = complete
        invoice.line_amounts_tax_inclusive = tax_inclusive

        injection = _detect_injection_attempt(lines)
        if injection:
            warnings.append(injection)

        invoice.evidence = evidence
        invoice.missing_fields = [
            name
            for name in ("vendor_name", "invoice_number", "invoice_date", "currency", "gross_total")
            if not getattr(invoice, name)
        ]
        if not invoice.explicit_po_number:
            invoice.missing_fields.append("explicit_po_number")
        if invoice.document_kind == "unknown":
            ambiguities.append("The document type could not be identified from its text.")
        invoice.ambiguities = ambiguities
        invoice.extraction_warnings = warnings

        return ExtractionResult(
            invoice=invoice,
            metadata=ExtractionMetadata(
                provider=self.name,
                model=PARSER_VERSION,
                parser_version=PARSER_VERSION,
                duration_ms=int((time.perf_counter() - started) * 1000),
                pages_total=len(page_texts),
            ),
        )

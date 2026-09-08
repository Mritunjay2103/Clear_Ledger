"""Conservative normalization of vendor names, invoice numbers and dates.

Normalization here only removes presentation noise (case, whitespace, punctuation
used as a separator). It never drops meaningful tokens, because two distinct
companies whose names merely look alike must not collapse onto one identity.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# The typographic dashes are deliberate: a PDF renderer may print an invoice
# number with an en or em dash where the vendor's system holds a plain hyphen.
_INVOICE_SEPARATOR_RE = re.compile(r"[\s._\-–—]+")  # noqa: RUF001


def collapse_whitespace(raw: str) -> str:
    return _WHITESPACE_RE.sub(" ", raw).strip()


def normalize_vendor_name(raw: str | None) -> str:
    """Fold a printed vendor name to a comparison key.

    Every alphanumeric token is preserved and joined by a single space, so
    ``Saffron Office Systems Pvt. Ltd.`` and ``SAFFRON OFFICE SYSTEMS PVT LTD``
    agree while ``Saffron Office Systems`` (a different legal entity) does not.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", raw).casefold()
    text = _NON_ALNUM_RE.sub(" ", text)
    return collapse_whitespace(text)


def normalize_invoice_number(raw: str | None) -> str:
    """Fold an invoice number for duplicate detection.

    Case and separator characters are ignored (``INV-1001`` == ``inv 1001``);
    every other character is significant.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", str(raw)).strip().casefold()
    text = _INVOICE_SEPARATOR_RE.sub("", text)
    return text


def normalize_po_number(raw: str | None) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", str(raw)).strip().casefold()
    return _INVOICE_SEPARATOR_RE.sub("", text)


def token_similarity(left: str, right: str) -> float:
    """Jaccard token overlap, used only to rank candidates for a human."""
    left_tokens = set(normalize_vendor_name(left).split())
    right_tokens = set(normalize_vendor_name(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


@dataclass(frozen=True)
class ParsedDate:
    value: date | None
    ambiguous: bool
    reason: str | None = None


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$")
_TEXT_DMY_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$")
_TEXT_MDY_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")


def parse_invoice_date(raw: str | None) -> ParsedDate:
    """Parse a printed date, reporting ambiguity instead of guessing.

    A purely numeric ``dd/mm`` vs ``mm/dd`` date where both readings are valid
    is returned as ambiguous so policy can route the invoice to review.
    """
    if not raw or not str(raw).strip():
        return ParsedDate(None, False, "no date supplied")
    text = collapse_whitespace(str(raw)).replace(",", ", ").replace("  ", " ").strip()
    text = collapse_whitespace(text)

    iso = _ISO_RE.match(text)
    if iso:
        return _build(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    textual = _TEXT_DMY_RE.match(text)
    if textual:
        month = _MONTHS.get(textual.group(2).lower())
        if month is None:
            return ParsedDate(None, False, f"unrecognised month name {textual.group(2)!r}")
        return _build(int(textual.group(3)), month, int(textual.group(1)))

    textual_mdy = _TEXT_MDY_RE.match(text.replace(", ", " "))
    if textual_mdy:
        month = _MONTHS.get(textual_mdy.group(1).lower())
        if month is None:
            return ParsedDate(None, False, f"unrecognised month name {textual_mdy.group(1)!r}")
        return _build(int(textual_mdy.group(3)), month, int(textual_mdy.group(2)))

    numeric = _NUMERIC_RE.match(text)
    if numeric:
        first, second, year_raw = (int(numeric.group(1)), int(numeric.group(2)), numeric.group(3))
        year = int(year_raw) if len(year_raw) == 4 else 2000 + int(year_raw)
        dmy_valid = _valid(year, second, first)
        mdy_valid = _valid(year, first, second)
        if dmy_valid and mdy_valid and first != second:
            return ParsedDate(
                None,
                True,
                f"{text!r} reads as both {first:02d}/{second:02d} and {second:02d}/{first:02d}",
            )
        if dmy_valid:
            return _build(year, second, first)
        if mdy_valid:
            return _build(year, first, second)
        return ParsedDate(None, False, f"{text!r} is not a possible calendar date")

    return ParsedDate(None, False, f"unrecognised date format {text!r}")


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _build(year: int, month: int, day: int) -> ParsedDate:
    if not _valid(year, month, day):
        return ParsedDate(None, False, f"{year:04d}-{month:02d}-{day:02d} is not a calendar date")
    return ParsedDate(date(year, month, day), False, None)

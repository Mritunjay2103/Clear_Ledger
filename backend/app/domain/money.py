"""Exact monetary arithmetic.

Every amount enters the system as a string, becomes a ``Decimal`` built from that
string, and is stored as an integer number of minor units (paise for INR).
Binary floating point is never used for a value that can influence a decision.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

#: Currencies this prototype evaluates automatically. Anything else is routed to
#: review by policy — but its amounts are still parsed and displayed exactly, so
#: a reviewer sees the real figure instead of an error.
SUPPORTED_CURRENCIES: Final[frozenset[str]] = frozenset({"INR"})
MINOR_UNIT_EXPONENT: Final[dict[str, int]] = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2}
DEFAULT_MINOR_UNIT_EXPONENT: Final[int] = 2

#: Arithmetic agreement threshold for printed invoice figures (INR 0.01).
ARITHMETIC_EPSILON: Final[Decimal] = Decimal("0.01")

_AMOUNT_CLEAN_RE = re.compile(r"[\s,\u00a0\u202f]")
_CURRENCY_PREFIX_RE = re.compile(r"^(?:INR|Rs\.?|₹)", re.IGNORECASE)
_VALID_AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


class MoneyError(ValueError):
    """Raised when a monetary string cannot be interpreted exactly."""


def parse_decimal(raw: str | Decimal | int | None) -> Decimal:
    """Parse an amount into a Decimal, rejecting NaN, infinity and junk.

    Floats are refused outright: accepting one would silently import binary
    rounding error into an accounting decision.
    """
    if raw is None:
        raise MoneyError("amount is missing")
    if isinstance(raw, float):
        raise MoneyError("binary floating point amounts are not accepted")
    if isinstance(raw, Decimal):
        candidate = raw
        if not candidate.is_finite():
            raise MoneyError("amount is not finite")
        return candidate
    if isinstance(raw, int):
        return Decimal(raw)

    text = str(raw).strip()
    if not text:
        raise MoneyError("amount is empty")
    text = _CURRENCY_PREFIX_RE.sub("", text).strip()
    text = _AMOUNT_CLEAN_RE.sub("", text)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not _VALID_AMOUNT_RE.match(text):
        raise MoneyError(f"amount {raw!r} is not a plain decimal number")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise MoneyError(f"amount {raw!r} is not a valid decimal") from exc
    if not value.is_finite():
        raise MoneyError("amount is not finite")
    return value


def minor_unit_exponent(currency: str | None) -> int:
    """Decimal places for a currency, defaulting to 2.

    The default keeps an unsupported-currency invoice renderable and exact; it
    does not make that currency eligible for automatic evaluation, which is
    governed separately by :func:`is_supported_currency`.
    """
    return MINOR_UNIT_EXPONENT.get((currency or "INR").upper(), DEFAULT_MINOR_UNIT_EXPONENT)


def quantize(value: Decimal, currency: str = "INR") -> Decimal:
    """Round to the currency's minor unit using ROUND_HALF_UP (documented rule)."""
    exponent = minor_unit_exponent(currency)
    return value.quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)


def to_minor(value: Decimal | str, currency: str = "INR") -> int:
    """Convert an amount to integer minor units for storage."""
    decimal_value = value if isinstance(value, Decimal) else parse_decimal(value)
    exponent = minor_unit_exponent(currency)
    return int(quantize(decimal_value, currency).scaleb(exponent).to_integral_value())


def from_minor(minor: int, currency: str = "INR") -> Decimal:
    exponent = minor_unit_exponent(currency)
    return (Decimal(minor).scaleb(-exponent)).quantize(Decimal(1).scaleb(-exponent))


def format_minor(minor: int, currency: str = "INR") -> str:
    """Render minor units as a plain decimal string, e.g. ``11800.00``."""
    return str(from_minor(minor, currency))


def display_minor(minor: int, currency: str = "INR") -> str:
    """Render minor units for prose, e.g. ``INR 11,800.00``."""
    amount = from_minor(minor, currency)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = str(abs(amount)).partition(".")
    grouped = f"{int(whole):,}"
    return f"{currency.upper()} {sign}{grouped}.{fraction or '00'}"


def is_supported_currency(currency: str | None) -> bool:
    return bool(currency) and currency.upper() in SUPPORTED_CURRENCIES


def normalize_currency(raw: str | None) -> str | None:
    """Map an unambiguous currency token to an ISO code.

    A bare ``$`` is deliberately not mapped: it is shared by many currencies and
    guessing one would put a wrong number in front of a reviewer.
    """
    if not raw:
        return None
    token = raw.strip().upper()
    if token in {"INR", "₹", "RS", "RS.", "INR.", "RUPEES"}:
        return "INR"
    if token in SUPPORTED_CURRENCIES:
        return token
    if re.fullmatch(r"[A-Z]{3}", token):
        return token
    return None

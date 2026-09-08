"""Money is the one place a rounding mistake becomes a wrong payment."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.money import (
    MoneyError,
    display_minor,
    format_minor,
    from_minor,
    is_supported_currency,
    normalize_currency,
    parse_decimal,
    to_minor,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1180.00", Decimal("1180.00")),
        ("1,18,000.00", Decimal("118000.00")),
        ("11,800.00", Decimal("11800.00")),
        ("INR 11800", Decimal("11800")),
        ("Rs. 11800.50", Decimal("11800.50")),
        ("\u20b911800.50", Decimal("11800.50")),
        ("11 800.00", Decimal("11800.00")),
        ("-250.75", Decimal("-250.75")),
    ],
)
def test_amounts_are_parsed_exactly(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "abc", "1.2.3", "12,00,0.0.0", "1e5", None])
def test_junk_is_refused_rather_than_guessed(raw) -> None:
    with pytest.raises(MoneyError):
        parse_decimal(raw)


def test_storage_is_in_integer_minor_units() -> None:
    assert to_minor(Decimal("11800.00")) == 1_180_000
    assert to_minor("0.01") == 1
    assert from_minor(1_180_000) == Decimal("11800.00")


def test_half_up_rounding_matches_an_invoice_reader() -> None:
    # Banker's rounding would give 2 here, which is not what a printed
    # invoice means by 0.005.
    assert to_minor(Decimal("0.025")) == 3
    assert to_minor(Decimal("0.015")) == 2


def test_a_round_trip_through_minor_units_loses_nothing() -> None:
    for amount in ("0.00", "0.01", "999999.99", "1180.50", "123456.78"):
        assert from_minor(to_minor(amount)) == Decimal(amount)


def test_float_arithmetic_would_disagree_and_we_do_not_use_it() -> None:
    total = to_minor("0.1") + to_minor("0.2")
    assert from_minor(total) == Decimal("0.30")
    assert 0.1 + 0.2 != 0.3


def test_display_is_grouped_and_machine_form_is_not() -> None:
    assert format_minor(1_180_000) == "11800.00"
    assert display_minor(1_180_000) == "INR 11,800.00"
    assert display_minor(118_000_000, "INR") == "INR 1,180,000.00"


def test_only_inr_is_claimed_as_supported() -> None:
    assert is_supported_currency("INR")
    assert is_supported_currency("inr")
    assert not is_supported_currency("USD")
    assert not is_supported_currency(None)


def test_unsupported_currency_still_parses_so_it_can_be_shown_to_a_human() -> None:
    # An unsupported currency must route to review, not crash the run.
    assert to_minor("1000.00", "USD") == 100_000
    assert display_minor(100_000, "USD") == "USD 1,000.00"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("inr", "INR"),
        (" INR ", "INR"),
        ("\u20b9", "INR"),
        ("Rs", "INR"),
        ("usd", "USD"),
        (None, None),
    ],
)
def test_currency_symbols_normalize_to_codes(raw, expected) -> None:
    assert normalize_currency(raw) == expected

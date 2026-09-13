"""Currency-aware parsing of a printed money string read from a receipt image.

The trust boundary matters here. A vision model returns the **verbatim string it sees**
(``"IDR 4,365,000"``), never a computed number; this module, deterministic and fully
tested, turns that string into a `Decimal`. A model that transposes a digit therefore
produces a string that fails the V5 digit check rather than a plausible wrong amount.

Parsing reads separator *structure* before it consults the row's currency, because the
corpus mixes conventions:

* ``image_01`` prints an IDR pay slip as ``IDR 4,365,000`` (comma grouping). A strict
  "IDR uses '.' as the thousands separator" rule would misread it by 10**6.
* Indian receipts use lakh grouping (``1,00,000.00``), which no simple 3-digit-group
  regex accepts.

So: multiple separators, both separator kinds, or a single separator with one or two
trailing digits are unambiguous. Only a single separator with exactly three trailing
digits (``1.234``) is genuinely ambiguous, and there the currency decides.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = [
    "AmountParseError",
    "digit_in_quote",
    "normalize_separators",
    "parse_printed_amount",
]


class AmountParseError(ValueError):
    """A printed amount string could not be read as a positive decimal."""


# For the one ambiguous shape. The corpus is authoritative: the IDR pay slip uses
# comma grouping, but where a *single* separator precedes exactly three digits the
# Indonesian convention (dot groups, comma is the decimal mark) is the safer reading.
_DECIMAL_BY_LOCALE: dict[str, dict[str, bool]] = {
    "IDR": {"dot": False, "comma": True},
    "INR": {"dot": True, "comma": False},
    "EUR": {"dot": True, "comma": False},
    "USD": {"dot": True, "comma": False},
    "ZAR": {"dot": True, "comma": False},
}
_DEFAULT_LOCALE = {"dot": True, "comma": False}

_NEGATIVE_MARKS = ("-", "\u2212", "\u2013", "\u2014")

# Currency codes and abbreviations, stripped whole (including a trailing period, as in
# "Rs."), plus symbols. Stripping these first matters: the period in "Rs." would
# otherwise be read as a thousands separator and turn "Rs. 723.00" into 72300.
_CURRENCY_NOISE = re.compile(
    r"(?i)(?:rs|rp|idr|inr|eur|usd|zar|rm)\.?|[\u20b9$\u20ac\u00a3]"
)


def _separator_is_decimal(separator: str, currency: str | None) -> bool:
    table = _DECIMAL_BY_LOCALE.get((currency or "").upper(), _DEFAULT_LOCALE)
    return table["dot" if separator == "." else "comma"]


def parse_printed_amount(text: str, currency: str | None = None) -> Decimal:
    """Parse ``text`` into a `Decimal`, or raise `AmountParseError`.

    Blank, non-numeric, negative, and over-precise strings are rejected rather than
    guessed at. The result is not rounded: the caller decides presentation.
    """
    if text is None:
        raise AmountParseError("no amount text")
    raw = text.strip()
    if not raw:
        raise AmountParseError("blank amount text")
    if any(mark in raw for mark in _NEGATIVE_MARKS):
        raise AmountParseError(f"negative amount is not a payment: {raw!r}")

    without_noise = _CURRENCY_NOISE.sub("", raw)
    digits_and_separators = re.sub(r"[^0-9.,]", "", without_noise)
    if not any(char.isdigit() for char in digits_and_separators):
        raise AmountParseError(f"no digits in {raw!r}")

    integer_part, fraction_part = _split(digits_and_separators, raw, currency)
    canonical = f"{integer_part}.{fraction_part}" if fraction_part else integer_part
    try:
        return Decimal(canonical)
    except InvalidOperation as exc:  # pragma: no cover - _split guarantees digits
        raise AmountParseError(f"cannot parse {raw!r}") from exc


def _split(cleaned: str, raw: str, currency: str | None) -> tuple[str, str]:
    """Return (integer_digits, fraction_digits) for a cleaned numeric token."""
    dot_count = cleaned.count(".")
    comma_count = cleaned.count(",")

    if dot_count and comma_count:
        # Both kinds present: the rightmost is the decimal mark, the other is grouping.
        if cleaned.rfind(".") > cleaned.rfind(","):
            integer_part, _, fraction_part = cleaned.rpartition(".")
            integer_part = integer_part.replace(",", "")
        else:
            integer_part, _, fraction_part = cleaned.rpartition(",")
            integer_part = integer_part.replace(".", "")
        return _validate_parts(integer_part, fraction_part, raw)

    if dot_count + comma_count == 0:
        return _validate_parts(cleaned, "", raw)

    separator = "." if dot_count else ","
    count = dot_count or comma_count
    if count > 1:
        # A separator cannot be both grouping and decimal; repeated means grouping.
        return _validate_parts(cleaned.replace(separator, ""), "", raw)

    integer_part, _, fraction_part = cleaned.rpartition(separator)
    if not integer_part.isdigit() or not fraction_part.isdigit():
        raise AmountParseError(f"malformed amount {raw!r}")

    if len(fraction_part) in (1, 2):
        return _validate_parts(integer_part, fraction_part, raw)
    if len(fraction_part) == 3:
        # The one ambiguous shape. The row's currency decides whether the lone
        # separator is a decimal mark ("1,234" is 1.234 for Indonesian rupiah) or a
        # thousands group ("2,298" is 2298 for Indian rupees).
        if _separator_is_decimal(separator, currency):
            return _validate_parts(integer_part, fraction_part, raw, max_fraction=3)
        return _validate_parts(integer_part + fraction_part, "", raw)
    raise AmountParseError(f"more than two decimal places in {raw!r}")


def _validate_parts(
    integer_part: str, fraction_part: str, raw: str, max_fraction: int = 2
) -> tuple[str, str]:
    if not integer_part.isdigit():
        raise AmountParseError(f"malformed amount {raw!r}")
    if fraction_part and not fraction_part.isdigit():
        raise AmountParseError(f"malformed amount {raw!r}")
    if len(fraction_part) > max_fraction:
        raise AmountParseError(f"more than two decimal places in {raw!r}")
    return integer_part, fraction_part


def normalize_separators(text: str) -> str:
    """Strip grouping/decimal separators and spaces for digit comparison.

    Deliberately conservative and locale-free: it only ever removes characters, so it
    cannot invent a digit that the source did not print.
    """
    return text.replace(".", "").replace(",", "").replace(" ", "").replace("\u00a0", "")


def _significant_digits(amount: Decimal) -> str:
    """The amount's digits with presentation-only trailing zeros removed.

    `2298.00` is the value 2298; a digit-presence check must not demand two zeros that
    the document never printed. A trailing zero that carries meaning (e.g. the `0` in
    `704.05`) is interior to the string and survives.
    """
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return re.sub(r"[^0-9]", "", text)


def digit_in_quote(amount: Decimal | None, text: str) -> bool:
    """V5: every digit of ``amount`` appears in ``text`` after normalisation.

    A ``None`` amount passes vacuously. The check is digit *presence*, matching the
    contract's wording (section 8, V5), not digit order.
    """
    if amount is None:
        return True
    digits = _significant_digits(amount)
    if not digits:
        return True
    normalized = normalize_separators(text or "")
    return all(digit in normalized for digit in digits)

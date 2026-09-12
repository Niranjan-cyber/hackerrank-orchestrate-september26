"""Money and date formatting.

Two rules that matter more than they look:

1. `Decimal` is always built from a *string*, never from a float. `Decimal(0.1)` is
   not 0.1 and the error compounds across 25k event rows.
2. Arithmetic stays **exact**. We do not round intermediate values. Rounding happens
   once, at output. Floor comparisons run on exact values, so conservative
   (directional) rounding buys nothing and would only risk missing a graded value by
   a cent. See CONTEXT.md D5.

The two output formats are different and both were reverse-engineered from the solved
samples - see CONTEXT.md section 11.12:

  payment_plan / reduce_to : two decimals when fractional, bare integer otherwise
                             620.40, 23.50, 15952906.67, but 25256
  amount_safe_to_pay       : shortest natural form, no trailing-zero padding
                             603.3, 17229139.2, 243849.58
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")


def money(raw: str | None) -> Decimal | None:
    """Parse a CSV money field. Blank returns None - it NEVER becomes zero.

    A blank `amount` means the value lives in a linked receipt image
    (problem_statement.md:45, README.md:113). Treating it as zero is explicitly
    forbidden, so callers must handle None.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return Decimal(text)


def quantize(value: Decimal) -> Decimal:
    """Round to 2dp for presentation. Only ever called on the way out."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money_scale(value: Decimal) -> Decimal:
    """Normalise a *modelled* monetary amount to the home currency's 2dp scale.

    Distinct from `quantize`, which is presentation-only. A forecast statistic such as
    the variable-spend monthly total is an amount of money entering the ledger, so it
    is held to the currency scale rather than carried at a statistical precision the
    currency cannot express. This has a second, load-bearing effect: a mean-of-N
    estimator divides by a non-power-of-two and produces a non-terminating decimal,
    which makes ledger sums order-dependent at the Decimal context precision. Every
    other ledger value (2dp event amounts, balances and 2dp*2dp FX products)
    terminates, so normalising here keeps the whole ledger exactly summable and makes
    the floor test reproducible. See CONTEXT.md D5.
    """
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def format_plan_amount(value: Decimal) -> str:
    """`payment_plan` and `reduce_to` form: 2dp when fractional, else bare integer."""
    q = quantize(value)
    if q == q.to_integral_value():
        return format(q.to_integral_value(), "f")
    return format(q, "f")


def format_safe_amount(value: Decimal) -> str:
    """`amount_safe_to_pay` form: shortest natural representation.

    Deliberately avoids `Decimal.normalize()`, which renders 25256 as `2.5256E+4`.
    """
    text = format(quantize(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """Hold the core invariant `0 <= amount_safe_to_pay <= requested_amount`."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def format_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def horizon_end(start: date, horizon_days: int) -> date:
    """Inclusive end of the forecast window."""
    return start + timedelta(days=horizon_days)

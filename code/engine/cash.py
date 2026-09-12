"""Cash-state classification and dated currency conversion.

This module answers one question per event row: *what does this row do to available
cash, and on what date?* Everything downstream - recurrence (05), the 90-day
simulation (06), plans (07) - consumes the answer and never re-reads `status`,
`direction` or `currency` itself.

THE RULE IS SMALLER THAN THE LIFECYCLE TABLE SUGGESTS
CONTEXT.md section 5 documents seven `linked_event_id` lifecycle patterns, which reads
like seven cases to branch on. It is not. Measured across all 58 linked rows, the link
never decides cash treatment - `status` and `direction` decide it, alone, and the seven
documented outcomes fall out of that single rule. So there is deliberately no parent
lookup here and no graph walk. `code/engine/tests/test_lifecycle_real_data.py` asserts
all seven patterns against the real data, so if that equivalence ever breaks it breaks
loudly rather than silently.

Two readings in this file are conservative on purpose, both verified against the data:

1. A pending debit described as a possible duplicate **is reserved**, not ignored.
   All six such rows carry a bank message stating the dispute is open and no reversal
   has been posted, so the cash has genuinely left the account. Ignoring them would
   overstate headroom by the full charge. (This corrects the earlier reading in
   CONTEXT.md sections 3 and 5.)
2. A blank `amount` on a future cash event becomes `UNKNOWN_AMOUNT`, never zero. Zero
   would be a silent under-reserve; the value lives in the linked receipt image and is
   supplied through `amount_overrides`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from .money import ZERO
from .types import Event, Profile, Request

# --- cash states ------------------------------------------------------------------
# Strings rather than an Enum so that effects stay trivially printable in a trace and
# comparable in a CSV diff, consistent with the rest of the engine's value types.

IN_OPENING_BALANCE = "in_opening_balance"
"""Already inside `current_available_balance`. Contributes nothing to the forecast."""

RESERVED_DEBIT = "reserved_debit"
"""Cash that will leave the account. Must be held out of headroom on `cash_date`."""

EXPECTED_CREDIT = "expected_credit"
"""Cash that will arrive and may be counted, e.g. a confirmed future salary."""

EXCLUDED = "excluded"
"""Never affects cash: cancelled, failed, non-cash, or an unsettled credit."""

UNKNOWN_AMOUNT = "unknown_amount"
"""A future cash event whose amount is blank and unresolved. NEVER treat as zero."""

PROJECTED_DEBIT = "projected_debit"
"""Inferred recurring outflow projected from historical pattern (Ticket 05)."""

PROJECTED_CREDIT = "projected_credit"
"""Inferred recurring inflow projected from historical pattern (Ticket 05)."""


# --- reason codes -----------------------------------------------------------------
# Every effect carries one, so ticket 09 can explain a number without re-deriving why.

SETTLED_IN_BALANCE = "SETTLED_IN_BALANCE"
PENDING_DEBIT_RESERVED = "PENDING_DEBIT_RESERVED"
SCHEDULED_DEBIT_RESERVED = "SCHEDULED_DEBIT_RESERVED"
DISPUTED_DUPLICATE_RESERVED = "DISPUTED_DUPLICATE_RESERVED"
CONFIRMED_CREDIT_COUNTED = "CONFIRMED_CREDIT_COUNTED"
UNSETTLED_CREDIT_NOT_COUNTED = "UNSETTLED_CREDIT_NOT_COUNTED"
STALE_CREDIT_NOT_COUNTED = "STALE_CREDIT_NOT_COUNTED"
CANCELLED_IGNORED = "CANCELLED_IGNORED"
FAILED_IGNORED = "FAILED_IGNORED"
NON_CASH_IGNORED = "NON_CASH_IGNORED"
BLANK_AMOUNT_UNRESOLVED = "BLANK_AMOUNT_UNRESOLVED"
PROJECTED_RECURRING_EXPENSE = "PROJECTED_RECURRING_EXPENSE"
PROJECTED_RECURRING_INCOME = "PROJECTED_RECURRING_INCOME"
PROJECTED_VARIABLE_SPENDING = "PROJECTED_VARIABLE_SPENDING"

CLOSED_STATUSES = frozenset({"settled", "cancelled", "failed", "unrealized"})
OPEN_STATUSES = frozenset({"pending", "scheduled"})


class MissingRateError(LookupError):
    """No exchange-rate row for a (date, from, to) triple.

    Raised rather than defaulted. The dataset has complete coverage for all 140
    foreign-currency events, so this firing means a real data or logic problem, and a
    silent fallback would corrupt a forecast rather than surface the fault.
    """


class UnresolvedAmountError(ValueError):
    """A cash total was requested while a future outflow still has no known amount.

    This is the "or fail loudly" half of the guardrail "never treat a blank `amount`
    as zero - resolve it from the linked image or fail loudly". There is no honest
    number to return: omitting the outflow would report a balance that is too high by
    exactly the unresolved charge, which is the direction that wrongly certifies a
    payment as safe.

    Callers must handle it, not swallow it. The error names the offending event ids so
    ticket 06 can degrade to a conservative recommendation for that one user rather
    than publish a falsely optimistic figure.
    """


def convert(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    on_date: date,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> Decimal:
    """Convert `amount` using the rate row for exactly `on_date` and this direction.

    No inverse rates and no nearest-date fallback: the rate table holds only five
    directed pairs and every event needs one of them, so inverting a rate would be
    inventing a number the dataset declined to supply. Arithmetic stays exact -
    rounding happens once, at output.
    """
    if from_currency == to_currency:
        return amount

    key = (on_date.isoformat(), from_currency, to_currency)
    rate = rates.get(key)
    if rate is None:
        raise MissingRateError(
            f"no exchange rate for {from_currency}->{to_currency} on {on_date.isoformat()}"
        )
    return amount * rate


@dataclass(frozen=True, slots=True)
class CashEffect:
    """What one event row does to cash. `amount_home` is always a positive magnitude;
    the direction of the movement is carried by `state`, not by a sign.
    """

    event_id: str
    state: str
    cash_date: date
    amount_home: Decimal | None
    reason_code: str
    # No defaults: these are copied straight off the event, and a default would
    # fabricate one. `flexibility="fixed"` in particular is the least-changeable
    # value, which ticket 08 would read as "this expense may never be reduced".
    category: str
    flexibility: str

    @property
    def signed_amount(self) -> Decimal:
        """Effect on the balance: negative for a reserve, positive for a credit.

        Raises on `UNKNOWN_AMOUNT` rather than contributing zero - see
        `UnresolvedAmountError`. Every `RESERVED_DEBIT` and `EXPECTED_CREDIT` is
        guaranteed a resolved `amount_home` by `classify_event`, so the arithmetic
        below is total.
        """
        if self.state == UNKNOWN_AMOUNT:
            raise UnresolvedAmountError(
                f"{self.event_id} is a future outflow with no resolved amount "
                f"(due {self.cash_date.isoformat()}); resolve it via amount_overrides"
            )
        if self.state == RESERVED_DEBIT:
            return -self.amount_home
        if self.state == EXPECTED_CREDIT:
            return self.amount_home
        if self.state == PROJECTED_DEBIT:
            return -self.amount_home
        if self.state == PROJECTED_CREDIT:
            return self.amount_home
        return ZERO


def classify_event(
    event: Event,
    *,
    request_date: date,
    home_currency: str,
    rates: Mapping[tuple[str, str, str], Decimal],
    amount_overrides: Mapping[str, Decimal] | None = None,
) -> CashEffect:
    """Classify one event row. Pure, and independent of every other row.

    `amount_overrides` maps `event_id` -> resolved home-currency amount, and is the
    only seam through which evidence reaches this module. Extraction resolves the
    blank amounts (ticket 12) and hands them over already parsed; nothing here reads a
    message, an image, or a `Fact`.
    """
    amount = event.amount
    override = (amount_overrides or {}).get(event.event_id)

    def effect(state: str, reason_code: str, *, value: Decimal | None, when: date) -> CashEffect:
        return CashEffect(
            event_id=event.event_id,
            state=state,
            cash_date=when,
            amount_home=value,
            reason_code=reason_code,
            category=event.category,
            flexibility=event.flexibility,
        )

    # --- rows that never move cash, whatever their amount or date -----------------
    # Checked first so that a cancelled or non-cash row with a blank amount can never
    # reach the amount-resolution path below and raise on a value nobody needs.
    if event.direction == "non_cash" or event.status == "unrealized":
        return effect(EXCLUDED, NON_CASH_IGNORED, value=None, when=event.cash_date)
    if event.status == "cancelled":
        return effect(EXCLUDED, CANCELLED_IGNORED, value=None, when=event.cash_date)
    if event.status == "failed":
        # The retry, where one exists, is a separate `scheduled` row and is reserved
        # on its own merits. Counting the failed parent as well would double-reserve.
        return effect(EXCLUDED, FAILED_IGNORED, value=None, when=event.cash_date)

    # --- settled history is already inside the opening balance --------------------
    if event.status == "settled" and event.cash_date <= request_date:
        return effect(IN_OPENING_BALANCE, SETTLED_IN_BALANCE, value=None, when=event.cash_date)

    # --- unsettled credits are never counted --------------------------------------
    # Pending refunds, bonuses, commissions, lottery proceeds and investment gains all
    # land here: money that has been promised but has not arrived.
    if event.status == "pending" and event.direction == "credit":
        return effect(
            EXCLUDED, UNSETTLED_CREDIT_NOT_COUNTED, value=None, when=event.cash_date
        )

    # A scheduled credit whose date has already passed without settling is income that
    # visibly failed to arrive. Counting it would be the unsafe reading.
    if event.direction == "credit" and event.cash_date <= request_date:
        return effect(EXCLUDED, STALE_CREDIT_NOT_COUNTED, value=None, when=event.cash_date)

    # --- everything remaining is future cash, so its amount matters ---------------
    if override is not None:
        amount_home: Decimal | None = override
    elif amount is None:
        # Blank amount on a future cash event. The value lives in the linked receipt
        # image; zero here would silently under-reserve a real outflow.
        return effect(
            UNKNOWN_AMOUNT,
            BLANK_AMOUNT_UNRESOLVED,
            value=None,
            when=max(event.cash_date, request_date),
        )
    else:
        amount_home = convert(amount, event.currency, home_currency, event.cash_date, rates)

    # A pending debit dated on or before request_date has not settled, so it is not in
    # the balance yet - reserve it immediately rather than on a date already gone.
    when = max(event.cash_date, request_date)

    if event.direction == "credit":
        return effect(EXPECTED_CREDIT, CONFIRMED_CREDIT_COUNTED, value=amount_home, when=when)

    if event.status == "pending":
        reason = (
            DISPUTED_DUPLICATE_RESERVED
            if "duplicate" in event.description.lower()
            else PENDING_DEBIT_RESERVED
        )
        return effect(RESERVED_DEBIT, reason, value=amount_home, when=when)

    return effect(RESERVED_DEBIT, SCHEDULED_DEBIT_RESERVED, value=amount_home, when=when)


@dataclass(frozen=True, slots=True)
class CashPosition:
    """The user's reconstructed cash position as of `request_date`.

    `opening_balance` is taken as given: the profile's `current_available_balance` is
    defined as the balance on `request_date`, with all settled history already applied.
    The forecast therefore starts from that number and layers only open rows on top -
    which is why re-summing settled events would double-count, not verify.
    """

    request_date: date
    home_currency: str
    opening_balance: Decimal
    minimum_balance: Decimal
    effects: tuple[CashEffect, ...]

    @property
    def reserved_debits(self) -> tuple[CashEffect, ...]:
        return tuple(e for e in self.effects if e.state == RESERVED_DEBIT)

    @property
    def expected_credits(self) -> tuple[CashEffect, ...]:
        return tuple(e for e in self.effects if e.state == EXPECTED_CREDIT)

    @property
    def projected_debits(self) -> tuple[CashEffect, ...]:
        return tuple(e for e in self.effects if e.state == PROJECTED_DEBIT)

    @property
    def projected_credits(self) -> tuple[CashEffect, ...]:
        return tuple(e for e in self.effects if e.state == PROJECTED_CREDIT)

    @property
    def unknown_amounts(self) -> tuple[CashEffect, ...]:
        """Future cash events still missing an amount.

        Non-empty means the forecast is incomplete: every total below refuses to
        compute while one of these falls inside its date range. Inspect this *before*
        asking for a total if you need to branch rather than catch.
        """
        return tuple(e for e in self.effects if e.state == UNKNOWN_AMOUNT)

    @property
    def net_forecast_change(self) -> Decimal:
        """Sum of every known future movement. Not a balance - see `balance_on`.

        Raises `UnresolvedAmountError` if any future outflow is still unpriced.
        """
        return sum((e.signed_amount for e in self.effects), ZERO)

    def balance_on(self, when: date) -> Decimal:
        """Projected balance after every movement dated on or before `when`.

        Explicit rows only - inferred recurring streams arrive in ticket 05, so this is
        an upper bound on the balance, not the figure a plan may be certified against.

        Raises `UnresolvedAmountError` if an unpriced outflow falls on or before
        `when`. An unresolved row dated *after* `when` cannot affect this total, so it
        is not an error here.
        """
        return self.opening_balance + sum(
            (e.signed_amount for e in self.effects if e.cash_date <= when), ZERO
        )


def cash_position(
    request: Request,
    profile: Profile,
    events: tuple[Event, ...],
    rates: Mapping[tuple[str, str, str], Decimal],
    amount_overrides: Mapping[str, Decimal] | None = None,
) -> CashPosition:
    """Classify every one of the user's events into one cash position value.

    Effects are ordered by `(cash_date, event_id)` - debits and credits are *not*
    separated here. Same-day ordering is a simulation concern and belongs to ticket 06,
    which owns the debits-before-credits rule.
    """
    effects = tuple(
        sorted(
            (
                classify_event(
                    event,
                    request_date=request.request_date,
                    home_currency=profile.home_currency,
                    rates=rates,
                    amount_overrides=amount_overrides,
                )
                for event in events
            ),
            key=lambda e: (e.cash_date, e.event_id),
        )
    )
    return CashPosition(
        request_date=request.request_date,
        home_currency=profile.home_currency,
        opening_balance=profile.current_available_balance,
        minimum_balance=profile.minimum_balance_to_keep,
        effects=effects,
    )

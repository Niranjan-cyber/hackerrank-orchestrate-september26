"""The 90-day simulator (Ticket 06).

Given a reconstructed `CashPosition` - explicit open rows from ticket 04 plus the
inferred recurring streams from ticket 05 - this module projects the balance across
a fixed 90-day window anchored at `request_date`, tests the minimum-balance floor
after **every** applied movement, and derives the two capacity figures:

    amount_safe_to_pay              the most that can be paid on `request_date`
    earliest_date_for_full_payment  the first date a single full payment is safe

Both are computed **before and without** any spending change: this module has no
notion of a spending change at all. Choosing *wait* vs *installments* vs *partial*
is ticket 07's job - this is a pure financial-capacity calculation, independent of
the user's accepted payment methods.

WHY A RUNNING MINIMUM, NOT AN END-OF-DAY MINIMUM
The floor is checked after every movement, so a day whose debit dips below the
floor before its credit restores it is a breach. That is the only reading that
cannot certify a plan on the strength of salary landing before rent (D4).

WHY A LINEAR DATE WALK
`earliest_date_for_full_payment` walks every candidate day in the window and returns
the first safe one. The window is at most 91 days, so a full walk is cheap, and it
makes no assumption about the shape of `holds_floor(candidate)`. A binary search would
silently depend on that shape staying monotonic if the same-day ordering or floor rule
ever changes.

SAME-DAY ORDERING
`same_day_rank` is the one named, swappable sort key. All three candidate
conventions are present and ticket 14 scores them; the default is the inferred
"dataset debits -> dataset credits -> plan payment last" (CONTEXT.md section 7).

SCOPE
This module reports *capacity* only. Choosing wait vs installments vs partial, and
ranking those candidates, is ticket 07's job; nothing here reads the user's accepted
payment methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from .cash import (
    UNKNOWN_AMOUNT,
    CashPosition,
    UnresolvedAmountError,
)
from .money import ZERO, clamp, horizon_end as window_end
from .types import Config

# --- movement kinds ----------------------------------------------------------------

DATASET_DEBIT = "dataset_debit"
DATASET_CREDIT = "dataset_credit"
PLAN_PAYMENT = "plan_payment"

PLAN_PAYMENT_RESERVED = "PLAN_PAYMENT_RESERVED"


class ProjectionHorizonError(ValueError):
    """A simulation window reaches past the position's inferred-projection coverage.

    `simulate` extends its ledger to cover injected plan payments. Ticket 05 projects
    recurring streams only as far as a stated horizon, so past that point the ledger
    would contain explicit payments but no inferred recurring spend - an unsafe
    under-check. Callers that need a longer window build the position with
    `recurrence.with_projections(..., horizon_end=...)` first. Failing loudly here is
    the deliberate choice; silently certifying an unseen window is the dangerous one.
    """


# The three candidate same-day conventions, keyed by `Config.same_day_ordering`.
# Ticket 14 sweeps them; none is hard-coded into the ledger logic.
_SAME_DAY_ORDERINGS: Mapping[str, Mapping[str, int]] = {
    "debits_credits_payment": {DATASET_DEBIT: 0, DATASET_CREDIT: 1, PLAN_PAYMENT: 2},
    "credits_debits_payment": {DATASET_CREDIT: 0, DATASET_DEBIT: 1, PLAN_PAYMENT: 2},
    "payment_debits_credits": {PLAN_PAYMENT: 0, DATASET_DEBIT: 1, DATASET_CREDIT: 2},
}


def same_day_rank(kind: str, ordering: str) -> int:
    """Rank one movement kind within its day under a named ordering convention.

    This is the single place the same-day convention is expressed. The ledger sorts
    on `(date, same_day_rank(...), event_id)`, so swapping the convention is a
    config change, not a code change.
    """
    order = _SAME_DAY_ORDERINGS.get(ordering)
    if order is None:
        raise ValueError(
            f"unknown same_day_ordering {ordering!r}; "
            f"expected one of {sorted(_SAME_DAY_ORDERINGS)}"
        )
    rank = order.get(kind)
    if rank is None:
        raise ValueError(
            f"unknown movement kind {kind!r}; expected one of {sorted(order)}"
        )
    return rank


# --- ledger value types ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerStep:
    """One applied movement and the balance immediately after it."""

    when: date
    event_id: str
    kind: str
    amount: Decimal  # signed: negative leaves the account, positive arrives
    balance: Decimal  # balance after applying this movement
    reason_code: str
    state: str


@dataclass(frozen=True, slots=True)
class Ledger:
    """A projected balance series for one fixed window, with the floor test built in."""

    request_date: date
    horizon_end: date
    opening_balance: Decimal
    minimum_balance: Decimal
    steps: tuple[LedgerStep, ...]

    @property
    def minimum_projected_balance(self) -> Decimal:
        """The worst balance anywhere in the window, opening balance included."""
        floor = self.opening_balance
        for step in self.steps:
            if step.balance < floor:
                floor = step.balance
        return floor

    @property
    def holds_floor(self) -> bool:
        """True when the balance never drops below the minimum, at any step."""
        return self.minimum_projected_balance >= self.minimum_balance

    @property
    def opening_breached(self) -> bool:
        """True when the balance is already below the floor before any movement."""
        return self.opening_balance < self.minimum_balance

    @property
    def first_breach(self) -> LedgerStep | None:
        """The first applied movement that leaves the balance below the floor.

        An opening balance that is already below the floor has no movement to point
        at; `opening_breached` reports that case.
        """
        for step in self.steps:
            if step.balance < self.minimum_balance:
                return step
        return None


@dataclass(frozen=True, slots=True)
class _Movement:
    when: date
    kind: str
    event_id: str
    amount: Decimal
    reason_code: str
    state: str


# --- simulation --------------------------------------------------------------------


def simulate(
    position: CashPosition,
    config: Config,
    *,
    extra_debits: Sequence[tuple[date, Decimal]] = (),
) -> Ledger:
    """Apply every movement in date order and return the projected ledger.

    `extra_debits` is how ticket 07 re-checks a candidate plan: each `(date, amount)`
    is injected as a scheduled debit. If a plan payment falls beyond the base 90-day
    window the window is extended to cover it. The position must then already carry
    inferred projections across the extended window (`with_projections`), or a
    `ProjectionHorizonError` is raised rather than certifying a window whose recurring
    spend is unknown.
    """
    base_end = window_end(position.request_date, config.horizon_days)
    end = base_end
    if extra_debits:
        end = max(end, max(when for when, _ in extra_debits))
        if end > base_end:
            covered_until = position.projected_until
            if covered_until is None or end > covered_until:
                raise ProjectionHorizonError(
                    f"plan extends to {end.isoformat()}, beyond the position's "
                    f"projection coverage "
                    f"{covered_until.isoformat() if covered_until else 'explicit rows only'}; "
                    f"build the position with recurrence.with_projections(horizon_end=...)"
                )

    movements = _movements(position, extra_debits, end, config)
    balance = position.opening_balance
    steps: list[LedgerStep] = []
    for movement in movements:
        balance += movement.amount
        steps.append(
            LedgerStep(
                when=movement.when,
                event_id=movement.event_id,
                kind=movement.kind,
                amount=movement.amount,
                balance=balance,
                reason_code=movement.reason_code,
                state=movement.state,
            )
        )
    return Ledger(
        request_date=position.request_date,
        horizon_end=end,
        opening_balance=position.opening_balance,
        minimum_balance=position.minimum_balance,
        steps=tuple(steps),
    )


def amount_safe_to_pay(
    position: CashPosition,
    config: Config,
    requested_amount: Decimal,
) -> Decimal:
    """The largest amount safe to pay on `request_date`, capped at the request.

    The ticket defines this as the window's minimum projected balance minus
    `minimum_balance_to_keep`, clamped to `[0, requested_amount]`, before any
    spending change:

        min(projected balance) - minimum_balance_to_keep

    The plan payment is ordered last on `request_date`, so the window minimum is
    attained before it and can only understate capacity, never overstate it. That is
    the conservative direction for a floor test.
    """
    ledger = simulate(position, config)
    headroom = ledger.minimum_projected_balance - position.minimum_balance
    return clamp(headroom, ZERO, requested_amount)


def earliest_date_for_full_payment(
    position: CashPosition,
    config: Config,
    requested_amount: Decimal,
) -> date | None:
    """The first day a single full payment holds the floor; None if never.

    Walks every candidate day in the fixed window, request_date included. The walk
    assumes nothing about the shape of `holds_floor` - see the module docstring.
    """
    last = window_end(position.request_date, config.horizon_days)
    candidate = position.request_date
    while candidate <= last:
        ledger = simulate(
            position,
            config,
            extra_debits=((candidate, requested_amount),),
        )
        if ledger.holds_floor:
            return candidate
        candidate += timedelta(days=1)
    return None


# --- movement assembly -------------------------------------------------------------


def _movements(
    position: CashPosition,
    extra_debits: Sequence[tuple[date, Decimal]],
    end: date,
    config: Config,
) -> tuple[_Movement, ...]:
    movements: list[_Movement] = []
    for effect in position.effects:
        if effect.cash_date > end:
            continue
        if effect.state == UNKNOWN_AMOUNT:
            # No honest forecast exists while a real outflow is unpriced. Raise
            # rather than under-reserve; the shell degrades to a conservative row.
            raise UnresolvedAmountError(
                f"{effect.event_id} is a future outflow with no resolved amount "
                f"(due {effect.cash_date.isoformat()})"
            )
        amount = effect.signed_amount
        if amount == ZERO:
            continue
        movements.append(
            _Movement(
                when=effect.cash_date,
                # `signed_amount` is the single source of truth for direction, so the
                # kind cannot drift away from the cash state that produced the sign.
                kind=DATASET_DEBIT if amount < ZERO else DATASET_CREDIT,
                event_id=effect.event_id,
                amount=amount,
                reason_code=effect.reason_code,
                state=effect.state,
            )
        )

    for index, (when, amount) in enumerate(extra_debits):
        if when > end:
            continue
        movements.append(
            _Movement(
                when=when,
                kind=PLAN_PAYMENT,
                event_id=f"plan:payment:{index:04d}",
                amount=-amount,
                reason_code=PLAN_PAYMENT_RESERVED,
                state=PLAN_PAYMENT,
            )
        )

    return tuple(
        sorted(
            movements,
            key=lambda movement: (
                movement.when,
                same_day_rank(movement.kind, config.same_day_ordering),
                movement.event_id,
            ),
        )
    )

"""Hand-built value builders for the focused engine-primitive tests (Ticket 13).

Deliberately independent of `code/engine/tests/support.py`: ticket 13 owns `tests/**`,
and its whole point is an *independent* regression net over the seven high-risk
primitives. These builders describe only the fields a test is actually about, so a
failure points at the primitive rather than at a shared fixture.

stdlib `unittest` only - `pytest` stays out of the submission (CONTEXT.md D24).
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = REPO_ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from engine.cash import cash_position  # noqa: E402
from engine.recurrence import with_projections  # noqa: E402
from engine.types import (  # noqa: E402
    Config,
    Dataset,
    Event,
    PaymentOption,
    Profile,
    Request,
)


def make_event(
    event_id: str = "event_test",
    *,
    user_id: str = "user_test",
    event_type: str = "expense",
    description: str = "Test event",
    category: str = "shopping",
    direction: str = "debit",
    amount: str | None = "100",
    currency: str = "INR",
    event_date: str = "2025-01-10",
    settlement_date: str | None = "2025-01-10",
    status: str = "settled",
    linked_event_id: str | None = None,
    flexibility: str = "fixed",
    minimum_allowed_amount: str | None = None,
) -> Event:
    return Event(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=None if amount is None else Decimal(amount),
        currency=currency,
        event_date=date.fromisoformat(event_date),
        settlement_date=(
            None if settlement_date is None else date.fromisoformat(settlement_date)
        ),
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=(
            None if minimum_allowed_amount is None else Decimal(minimum_allowed_amount)
        ),
    )


def make_profile(
    *,
    user_id: str = "user_test",
    home_currency: str = "INR",
    balance: str = "10000",
    minimum: str = "2000",
    financial_priorities: tuple[str, ...] = (),
    protected_categories: tuple[str, ...] = (),
    reducible_categories: tuple[str, ...] = (),
    stoppable_categories: tuple[str, ...] = (),
    payment_methods_considered: tuple[str, ...] = ("full_payment",),
    max_installment_months: int | None = None,
) -> Profile:
    return Profile(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance=Decimal(balance),
        minimum_balance_to_keep=Decimal(minimum),
        financial_priorities=financial_priorities,
        protected_categories=protected_categories,
        reducible_categories=reducible_categories,
        stoppable_categories=stoppable_categories,
        payment_methods_considered=payment_methods_considered,
        max_installment_months=max_installment_months,
    )


def make_request(
    *,
    request_id: str = "request_test",
    user_id: str = "user_test",
    request_date: str = "2025-02-01",
    requested_amount: str = "5000",
    desired_completion_date: str = "2025-04-01",
    allows_partial_payment: bool = False,
) -> Request:
    return Request(
        request_id=request_id,
        user_id=user_id,
        request_date=date.fromisoformat(request_date),
        request_type="purchase",
        requested_amount=Decimal(requested_amount),
        desired_completion_date=date.fromisoformat(desired_completion_date),
        allows_partial_payment=allows_partial_payment,
        request_text="test",
    )


def make_payment_option(
    payment_option_id: str = "payment_option_01",
    *,
    request_id: str = "request_test",
    payment_method: str = "installments",
    payment_amount: str = "1000",
    number_of_payments: int = 3,
    first_payment_date: str = "2025-02-01",
    payment_frequency_days: int | None = 30,
    financing_fee: str = "0",
    total_payable_amount: str | None = None,
) -> PaymentOption:
    amount = Decimal(payment_amount)
    return PaymentOption(
        payment_option_id=payment_option_id,
        request_id=request_id,
        payment_method=payment_method,
        payment_amount=amount,
        number_of_payments=number_of_payments,
        first_payment_date=date.fromisoformat(first_payment_date),
        payment_frequency_days=payment_frequency_days,
        financing_fee=Decimal(financing_fee),
        total_payable_amount=(
            amount * number_of_payments
            if total_payable_amount is None
            else Decimal(total_payable_amount)
        ),
    )


def build_position(
    events,
    *,
    request: Request | None = None,
    profile: Profile | None = None,
    config: Config | None = None,
    rates=None,
    horizon_end: date | None = None,
):
    """The same merge the pipeline performs: explicit rows plus inferred streams."""
    request = request or make_request()
    profile = profile or make_profile()
    config = config or Config()
    events = tuple(events)
    position = cash_position(request, profile, events, rates or {})
    return with_projections(
        position,
        events,
        request,
        profile,
        config,
        rates or {},
        horizon_end=horizon_end,
    )


def build_dataset(
    *,
    request: Request | None = None,
    profile: Profile | None = None,
    events=(),
    options=(),
    rates=None,
) -> Dataset:
    """A one-request dataset, everything else empty. For end-to-end pipeline tests."""
    request = request or make_request()
    profile = profile or make_profile()
    events = tuple(events)
    options = tuple(options)
    return Dataset(
        requests=(request,),
        profiles={profile.user_id: profile},
        events_by_user={profile.user_id: events},
        options_by_request={request.request_id: options},
        rates=rates or {},
    )

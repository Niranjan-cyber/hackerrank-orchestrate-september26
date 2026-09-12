"""Shared helpers for the engine tests.

Stdlib `unittest` only - no pytest. CONTEXT.md D24 and `docs/agents/tooling.md` fix the
test runner as stdlib `unittest`, because that keeps the zero-dependency promise for a
grader who has nothing but Python. That reason holds whether or not pytest happens to
be importable on this machine.

Run the suite from the repo root:

    python -m unittest discover -s code/engine/tests -t code
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[2]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"

from engine.loaders import load_dataset, load_requests  # noqa: E402
from engine.types import Event, Profile, Request  # noqa: E402


# The real dataset is ~25k rows. Loading it once and sharing it read-only keeps the
# whole suite well under a second; loading per test would dominate the runtime.
@lru_cache(maxsize=1)
def shared_dataset():
    return load_dataset(DATASET_DIR)


@lru_cache(maxsize=1)
def sample_requests() -> tuple[Request, ...]:
    """The 25 solved samples, whose users are disjoint from the 250 evaluation users."""
    return load_requests(DATASET_DIR / "sample_requests.csv")


@lru_cache(maxsize=1)
def request_date_by_user() -> dict[str, date]:
    """request_date for all 275 users (250 evaluation + 25 sample). One per user."""
    mapping = {r.user_id: r.request_date for r in shared_dataset().requests}
    for request in sample_requests():
        mapping.setdefault(request.user_id, request.request_date)
    return mapping


@lru_cache(maxsize=1)
def all_events() -> tuple[Event, ...]:
    return tuple(
        event for events in shared_dataset().events_by_user.values() for event in events
    )


@lru_cache(maxsize=1)
def events_by_id() -> dict[str, Event]:
    return {event.event_id: event for event in all_events()}


# --- hand-built value builders ----------------------------------------------------
# Defaults describe the overwhelmingly common row: a settled domestic debit. Each test
# overrides only the fields it is actually about, so the intent stays readable.


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

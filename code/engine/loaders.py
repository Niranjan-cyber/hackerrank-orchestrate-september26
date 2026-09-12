"""Dataset loading - the I/O edge.

This module reads files, so it is shell-side machinery, not core. `run_pipeline` never
calls it; `code/main.py` calls it once and hands the resulting `Dataset` value to the
core. Keeping it out of the core is what makes the core testable with no filesystem.

Nothing here interprets finance. It parses and nothing more, so that a dataset quirk
surfaces as a parse decision in one place rather than as a mystery three modules deep.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .money import money, parse_date
from .types import Dataset, Event, PaymentOption, Profile, Request


def _rows(path: Path) -> list[dict[str, str]]:
    # utf-8-sig: the dataset is clean UTF-8 but a BOM would silently corrupt the first
    # header name, which would then look like a missing column much later on.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _pipe_list(raw: str) -> tuple[str, ...]:
    """Split a `a|b|c` profile field. Blank means empty, not `("",)`."""
    text = (raw or "").strip()
    if not text:
        return ()
    return tuple(part for part in text.split("|") if part)


def _opt_int(raw: str) -> int | None:
    text = (raw or "").strip()
    return int(text) if text else None


def load_profiles(path: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for row in _rows(path):
        profiles[row["user_id"]] = Profile(
            user_id=row["user_id"],
            home_currency=row["home_currency"],
            current_available_balance=money(row["current_available_balance"]),
            minimum_balance_to_keep=money(row["minimum_balance_to_keep"]),
            financial_priorities=_pipe_list(row["financial_priorities"]),
            protected_categories=_pipe_list(row["expense_categories_to_protect"]),
            reducible_categories=_pipe_list(
                row["expense_categories_user_is_willing_to_reduce"]
            ),
            stoppable_categories=_pipe_list(
                row["expense_categories_user_is_willing_to_stop"]
            ),
            payment_methods_considered=_pipe_list(
                row["payment_methods_user_will_consider"]
            ),
            # Blank exactly when installments are not an accepted method - verified
            # 119 blank / 156 set, zero off-diagonal across all 275 profiles.
            max_installment_months=_opt_int(row["max_installment_months"]),
        )
    return profiles


def load_requests(path: Path) -> tuple[Request, ...]:
    return tuple(
        Request(
            request_id=row["request_id"],
            user_id=row["user_id"],
            request_date=parse_date(row["request_date"]),
            request_type=row["request_type"],
            requested_amount=money(row["requested_amount"]),
            desired_completion_date=parse_date(row["desired_completion_date"]),
            allows_partial_payment=row["allows_partial_payment"].strip().lower()
            == "true",
            request_text=row["request_text"],
        )
        for row in _rows(path)
    )


def load_events(path: Path) -> dict[str, tuple[Event, ...]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for row in _rows(path):
        grouped[row["user_id"]].append(
            Event(
                event_id=row["event_id"],
                user_id=row["user_id"],
                event_type=row["event_type"],
                description=row["description"],
                category=row["category"],
                direction=row["direction"],
                # money() returns None for the 16 blank-amount rows. Downstream must
                # resolve those from the linked image, never default them to zero.
                amount=money(row["amount"]),
                currency=row["currency"],
                event_date=parse_date(row["event_date"]),
                settlement_date=parse_date(row["settlement_date"]),
                status=row["status"],
                linked_event_id=(row["linked_event_id"] or "").strip() or None,
                flexibility=row["flexibility"],
                minimum_allowed_amount=money(row["minimum_allowed_amount"]),
            )
        )
    # Sort by cash date then event_id: a stable total order, so nothing downstream can
    # depend on CSV row order or dict iteration order.
    return {
        user_id: tuple(sorted(events, key=lambda e: (e.cash_date, e.event_id)))
        for user_id, events in grouped.items()
    }


def load_payment_options(path: Path) -> dict[str, tuple[PaymentOption, ...]]:
    grouped: dict[str, list[PaymentOption]] = defaultdict(list)
    for row in _rows(path):
        grouped[row["request_id"]].append(
            PaymentOption(
                payment_option_id=row["payment_option_id"],
                request_id=row["request_id"],
                payment_method=row["payment_method"],
                payment_amount=money(row["payment_amount"]),
                number_of_payments=int(row["number_of_payments"]),
                first_payment_date=parse_date(row["first_payment_date"]),
                payment_frequency_days=_opt_int(row["payment_frequency_days"]),
                financing_fee=money(row["financing_fee"]) or money("0"),
                total_payable_amount=money(row["total_payable_amount"]),
            )
        )
    return {
        request_id: tuple(sorted(options, key=lambda o: o.payment_option_id))
        for request_id, options in grouped.items()
    }


def load_rates(path: Path) -> dict[tuple[str, str, str], object]:
    rates = {}
    for row in _rows(path):
        key = (row["rate_date"], row["from_currency"], row["to_currency"])
        rates[key] = money(row["rate"])
    return rates


def load_dataset(dataset_dir: Path) -> Dataset:
    """Read every participant-facing CSV and return one immutable value."""
    return Dataset(
        requests=load_requests(dataset_dir / "requests.csv"),
        profiles=load_profiles(dataset_dir / "financial_profiles.csv"),
        events_by_user=load_events(dataset_dir / "financial_events.csv"),
        options_by_request=load_payment_options(
            dataset_dir / "request_payment_options.csv"
        ),
        rates=load_rates(dataset_dir / "exchange_rates.csv"),
    )

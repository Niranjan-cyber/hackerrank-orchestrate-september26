"""Fact validation - enforce the 13 contract rules before a fact reaches the engine.

Validation is notification-style: failures are recorded and the fact is dropped,
never repaired. Callers receive a tuple of (valid_fact, violation) pairs.

The engine core applies the authority matrix and decides consequences; the extractor
only guarantees that every returned fact is structurally valid (V1-V13).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from engine.money import money, parse_date
from engine.types import CURRENCIES, Dataset, Event, Fact, Request

CONTRACT_VERSION = "1.0.0"

# 25 message-based fact types + image_amount. The contract counts the message-based
# enum as 25; image extraction adds one more value used only for blank-amount events.
FACT_TYPES = frozenset(
    {
        "salary_first",
        "salary_increase",
        "salary_decrease",
        "salary_temporary",
        "salary_confirmed_unchanged",
        "income_ended",
        "employment_ended",
        "one_time_arrears",
        "invoice_approved_pending",
        "refund_pending",
        "gig_payout_pending",
        "prize_claim_processing",
        "bonus_unconfirmed",
        "windfall_solicitation",
        "windfall_settled",
        "investment_sale_settled",
        "expense_reimbursement_settled",
        "unrealized_valuation_notice",
        "recurring_expense_increase",
        "payment_retry_pending",
        "disputed_duplicate_charge",
        "distinct_obligations",
        "receipt_amount_pointer",
        "foreign_currency_amount_pending",
        "internal_transfer",
        "image_amount",
    }
)

SOURCE_TYPES = frozenset(
    {"employer", "bank", "merchant", "service_provider", "financial_service"}
)

CONFIDENCES = frozenset({"high", "medium", "low"})

# Facts that may create or increase an inflow. Authority conditions in V9 apply.
INFLOW_INCREASE_TYPES = frozenset(
    {"salary_first", "salary_increase", "one_time_arrears"}
)

# Facts that must not carry an amount.
AMOUNT_FORBIDDEN_TYPES = frozenset(
    {
        "salary_confirmed_unchanged",
        "income_ended",
        "employment_ended",
        "internal_transfer",
        "unrealized_valuation_notice",
        "distinct_obligations",
        "invoice_approved_pending",
        "refund_pending",
        "gig_payout_pending",
        "prize_claim_processing",
        "bonus_unconfirmed",
        "windfall_solicitation",
        "windfall_settled",
        "investment_sale_settled",
        "expense_reimbursement_settled",
        "payment_retry_pending",
        "disputed_duplicate_charge",
        "receipt_amount_pointer",
        "foreign_currency_amount_pending",
    }
)

# Facts that must carry an amount.
AMOUNT_REQUIRED_TYPES = frozenset(
    {
        "salary_first",
        "salary_increase",
        "salary_decrease",
        "salary_temporary",
        "one_time_arrears",
        "image_amount",
    }
)

# Effective date is required for any fact that moves a stream or expense.
DATE_REQUIRED_TYPES = frozenset(
    {
        "salary_first",
        "salary_increase",
        "salary_decrease",
        "salary_temporary",
        "income_ended",
        "employment_ended",
        "one_time_arrears",
        "recurring_expense_increase",
    }
)


@dataclass(frozen=True, slots=True)
class Violation:
    """One reason a fact was dropped. Never raised."""

    rule: str
    subject: str
    fact_type: str
    detail: str


def _digits_of(value: Decimal) -> str:
    """All digits in a decimal amount, ignoring sign, decimal point and leading zeros."""
    return re.sub(r"[^0-9]", "", str(value))


def _normalize_separators(text: str, currency: str | None) -> str:
    """Strip grouping separators so IDR '12.500.000' can be matched to '12500000'.

    This is intentionally conservative: remove common grouping characters and spaces.
    It does not interpret locale.
    """
    # IDR uses '.' as thousands separator and no decimal fraction in the corpus.
    # INR/EUR/USD/ZAR use ',' as thousands separator.
    return text.replace(".", "").replace(",", "").replace(" ", "").replace("\u00a0", "")


def _digit_in_quote(
    amount: Decimal | None,
    quote: str,
    currency: str | None,
    image_verbatim: str | None = None,
) -> bool:
    """V5: every digit of amount appears in the verbatim quote after normalisation."""
    if amount is None:
        return True
    digits = _digits_of(amount)
    if not digits:
        return True
    source = image_verbatim if image_verbatim is not None else quote
    normalized = _normalize_separators(source, currency)
    for d in digits:
        if d not in normalized:
            return False
    return True


def _parse_amount(raw: str | None) -> Decimal | None:
    """Parse a decimal string, returning None for blank/invalid (V6 handled by caller)."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return money(text)
    except (InvalidOperation, ValueError):
        return None


def _looks_unconditional(text: str) -> bool:
    """V9 condition 3: reject conditional phrasing for inflow-increasing facts."""
    lowered = text.lower()
    conditional = (
        "subject to",
        "pending review",
        "may change",
        "expected",
        "estimated",
        "projected",
        "if approved",
        "tentative",
        "provisional",
    )
    return not any(phrase in lowered for phrase in conditional)


def validate_fact(
    fact: Fact,
    dataset: Dataset,
    messages_by_id: dict[str, dict[str, str]],
    images_by_id: dict[str, dict[str, str]],
    events_by_id: dict[str, Event],
    requests_by_user: dict[str, Request],
) -> tuple[Fact | None, tuple[Violation, ...]]:
    """Apply V1-V13 to a single fact.

    Returns the validated fact (possibly authority-downgraded) and a tuple of violations.
    A returned fact means it is safe for the engine; an empty violation tuple means it
    passed every rule cleanly.
    """
    violations: list[Violation] = []

    # V1: fact type is known.
    if fact.fact_type not in FACT_TYPES:
        violations.append(
            Violation(
                "V1",
                fact.subject,
                fact.fact_type,
                f"unknown fact_type {fact.fact_type}",
            )
        )
        return None, tuple(violations)

    # V2: subject resolves to a real message_id or image_id.
    if fact.fact_type == "image_amount":
        if fact.subject not in images_by_id:
            violations.append(
                Violation(
                    "V2", fact.subject, fact.fact_type, "subject not in images.csv"
                )
            )
    else:
        if fact.subject not in messages_by_id:
            violations.append(
                Violation(
                    "V2", fact.subject, fact.fact_type, "subject not in messages.csv"
                )
            )

    # V3: user exists; request_id / related_event_id exist when non-null.
    if fact.user_id not in dataset.profiles:
        violations.append(
            Violation(
                "V3", fact.subject, fact.fact_type, f"user_id {fact.user_id} not found"
            )
        )
    if (
        fact.request_id is not None
        and fact.request_id not in dataset.options_by_request
    ):
        violations.append(
            Violation(
                "V3",
                fact.subject,
                fact.fact_type,
                f"request_id {fact.request_id} not found",
            )
        )
    if fact.related_event_id is not None and fact.related_event_id not in events_by_id:
        violations.append(
            Violation(
                "V3",
                fact.subject,
                fact.fact_type,
                f"related_event_id {fact.related_event_id} not found",
            )
        )

    # V4: verbatim_quote is a contiguous substring of the source text.
    source_text = ""
    if fact.fact_type == "image_amount":
        source_text = fact.verbatim_amount_string or ""
    else:
        message_row = messages_by_id.get(fact.subject, {})
        source_text = message_row.get("message_text") or ""

    if fact.verbatim_quote and source_text and fact.verbatim_quote not in source_text:
        violations.append(
            Violation(
                "V4",
                fact.subject,
                fact.fact_type,
                "verbatim_quote not found in source text",
            )
        )

    # V6: amount parses and is positive.
    amount = fact.amount
    if amount is not None:
        if amount <= Decimal("0"):
            violations.append(
                Violation(
                    "V6",
                    fact.subject,
                    fact.fact_type,
                    f"amount {amount} is not positive",
                )
            )
            amount = None

    # V7: currency is known and either matches home currency or has a dated rate.
    if fact.currency is not None:
        if fact.currency not in CURRENCIES:
            violations.append(
                Violation(
                    "V7",
                    fact.subject,
                    fact.fact_type,
                    f"currency {fact.currency} not in {CURRENCIES}",
                )
            )
        else:
            profile = dataset.profiles.get(fact.user_id)
            if profile is not None and fact.currency != profile.home_currency:
                # The engine converts only with the exact directed rate
                # (date, from_currency, home_currency) and never inverts. Accepting
                # the inverse here would let a fact pass validation and then crash
                # the engine in cash.convert().
                rate_exists = any(
                    frm == fact.currency and to == profile.home_currency
                    for (_, frm, to) in dataset.rates
                )
                if not rate_exists:
                    violations.append(
                        Violation(
                            "V7",
                            fact.subject,
                            fact.fact_type,
                            f"no rate for {fact.currency}->{profile.home_currency}",
                        )
                    )

    # V8: effective_date is valid and within the evidence window.
    if fact.effective_date is not None:
        request = requests_by_user.get(fact.user_id)
        if request is not None:
            earliest = request.request_date - timedelta(days=400)
            latest = request.request_date + timedelta(days=90)
            if not (earliest <= fact.effective_date <= latest):
                violations.append(
                    Violation(
                        "V8",
                        fact.subject,
                        fact.fact_type,
                        f"effective_date {fact.effective_date} outside "
                        f"[{earliest}, {latest}]",
                    )
                )

    # V10: field presence matches per-type requirements.
    if fact.fact_type in AMOUNT_REQUIRED_TYPES and amount is None:
        violations.append(
            Violation(
                "V10",
                fact.subject,
                fact.fact_type,
                "amount is required for this fact_type",
            )
        )
    if fact.fact_type in AMOUNT_FORBIDDEN_TYPES and amount is not None:
        violations.append(
            Violation(
                "V10",
                fact.subject,
                fact.fact_type,
                "amount is forbidden for this fact_type",
            )
        )
    if fact.fact_type == "recurring_expense_increase":
        has_amount = amount is not None
        has_percent = fact.percent_change is not None
        if has_amount == has_percent:
            violations.append(
                Violation(
                    "V10",
                    fact.subject,
                    fact.fact_type,
                    "requires exactly one of amount or percent_change",
                )
            )
        if has_percent and fact.percent_change <= Decimal("0"):
            violations.append(
                Violation(
                    "V10",
                    fact.subject,
                    fact.fact_type,
                    "percent_change must be positive",
                )
            )
    if fact.fact_type == "salary_temporary" and fact.applies_to_cycles is None:
        violations.append(
            Violation(
                "V10",
                fact.subject,
                fact.fact_type,
                "applies_to_cycles is required for salary_temporary",
            )
        )
    if fact.fact_type in DATE_REQUIRED_TYPES and fact.effective_date is None:
        violations.append(
            Violation(
                "V10",
                fact.subject,
                fact.fact_type,
                "effective_date is required for this fact_type",
            )
        )

    # V11: image_amount facts point at an event with a blank amount.
    if fact.fact_type == "image_amount":
        event = (
            events_by_id.get(fact.related_event_id) if fact.related_event_id else None
        )
        if event is None:
            violations.append(
                Violation(
                    "V11",
                    fact.subject,
                    fact.fact_type,
                    "missing related_event_id for image_amount",
                )
            )
        elif event.amount is not None:
            violations.append(
                Violation(
                    "V11",
                    fact.subject,
                    fact.fact_type,
                    f"related_event {event.event_id} already has amount {event.amount}",
                )
            )

    # V5: digit-in-quote.
    image_verbatim = None
    if fact.fact_type == "image_amount":
        image_verbatim = fact.verbatim_amount_string or ""
    if not _digit_in_quote(amount, fact.verbatim_quote, fact.currency, image_verbatim):
        violations.append(
            Violation(
                "V5",
                fact.subject,
                fact.fact_type,
                "amount digits missing from verbatim quote",
            )
        )

    # V9: authority check for inflow-increasing facts.
    # The structurally correct fact is kept but its type is downgraded to a confirm-only
    # type so the engine cannot use it to create/increase an inflow.
    if fact.fact_type in INFLOW_INCREASE_TYPES and not violations:
        message_row = messages_by_id.get(fact.subject, {})
        text = message_row.get("message_text", "")
        conditions = [
            ("source_type", fact.source_type == "employer"),
            ("unconditional", _looks_unconditional(text)),
            ("amount_present", amount is not None),
            ("effective_date_present", fact.effective_date is not None),
            (
                "date_in_window",
                (
                    fact.effective_date is not None
                    and fact.effective_date
                    <= (
                        requests_by_user[fact.user_id].request_date + timedelta(days=90)
                    )
                )
                if fact.user_id in requests_by_user
                else True,
            ),
        ]
        failed = [name for name, ok in conditions if not ok]
        if failed:
            violations.append(
                Violation(
                    "V9",
                    fact.subject,
                    fact.fact_type,
                    f"authority downgrade: failed {', '.join(failed)}",
                )
            )
            # Downgrade to confirm-only so the engine sees a harmless fact rather than
            # a dropped one. This preserves auditability of the evidence.
            if fact.fact_type in {"salary_first", "salary_increase"}:
                fact = replace(fact, fact_type="salary_confirmed_unchanged")
            elif fact.fact_type == "one_time_arrears":
                # No confirm-only equivalent for a one-off; drop it.
                return None, tuple(violations)

    # V13: no field destined for an output column. The Fact schema has no such fields,
    # so this is structurally guaranteed. We keep the rule here as documentation.

    if violations:
        return None, tuple(violations)
    return fact, ()


def validate_facts(
    facts: Iterable[Fact],
    dataset: Dataset,
    messages_by_id: dict[str, dict[str, str]],
    images_by_id: dict[str, dict[str, str]],
    events_by_id: dict[str, Event],
    requests_by_user: dict[str, Request],
) -> tuple[tuple[Fact, ...], tuple[Violation, ...]]:
    """Validate many facts and collapse duplicates (V12)."""
    seen: dict[tuple[str, str], Fact] = {}
    violations: list[Violation] = []

    for fact in facts:
        valid, vs = validate_fact(
            fact, dataset, messages_by_id, images_by_id, events_by_id, requests_by_user
        )
        violations.extend(vs)
        if valid is None:
            continue
        key = (valid.subject, valid.fact_type)
        if key in seen:
            # V12: keep the lexicographically smallest fixture key for determinism.
            existing_key = (seen[key].extractor or {}).get("fixture_key", "")
            new_key = (valid.extractor or {}).get("fixture_key", "")
            if new_key < existing_key:
                violations.append(
                    Violation(
                        "V12",
                        valid.subject,
                        valid.fact_type,
                        f"duplicate collapsed; kept {new_key}",
                    )
                )
                seen[key] = valid
            else:
                violations.append(
                    Violation(
                        "V12",
                        valid.subject,
                        valid.fact_type,
                        f"duplicate collapsed; kept {existing_key}",
                    )
                )
        else:
            seen[key] = valid

    ordered = tuple(sorted(seen.values(), key=lambda f: (f.fact_type, f.subject)))
    return ordered, tuple(violations)

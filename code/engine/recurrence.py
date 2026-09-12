"""Recurrence detection and forward projection (Ticket 05).

The engine first classifies every event row into a `CashEffect` in `cash.py`. This
module then looks at the settled history *behind* those `IN_OPENING_BALANCE` effects,
groups rows into recurring streams, and projects additional occurrences into the
90-day forecast window anchored at `request_date`.

All recurrence parameters are injected through `Config`; no literal values live here.
"""

from __future__ import annotations

import re
import statistics
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Mapping

from .cash import (
    EXPECTED_CREDIT,
    IN_OPENING_BALANCE,
    PROJECTED_CREDIT,
    PROJECTED_DEBIT,
    PROJECTED_RECURRING_EXPENSE,
    PROJECTED_RECURRING_INCOME,
    PROJECTED_VARIABLE_SPENDING,
    RESERVED_DEBIT,
    CashEffect,
    CashPosition,
    convert,
)
from .money import ZERO
from .types import Config, Event, Profile, Request


# --- public API -------------------------------------------------------------------


def projected_effects(
    position: CashPosition,
    events: tuple[Event, ...],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
) -> tuple[CashEffect, ...]:
    """Return inferred recurring effects for the 90-day forecast window.

    `position` supplies the explicit open rows used for duplicate-date suppression.
    `events` supplies the original rows used to detect history. `rates` is required
    only when a stream contains a foreign-currency event.
    """
    rates = rates or {}
    streams = _detect_streams(position, events, request, profile, config, rates)
    explicit = _explicit_suppression_set(position)
    projected: list[CashEffect] = []
    horizon_end = request.request_date + timedelta(days=config.horizon_days)
    for stream in streams:
        projected.extend(
            _project_stream(stream, request.request_date, horizon_end, explicit, config)
        )
    return tuple(sorted(projected, key=lambda e: (e.cash_date, e.event_id)))


# --- stream value type --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RecurrenceStream:
    """One detected recurring stream ready for projection."""

    stream_key: tuple[str, str, str, str]
    # category, direction, event_type, normalized_description
    latest_event_id: str
    latest_amount: Decimal
    day_of_month: int
    direction: str
    category: str
    flexibility: str
    is_variable: bool
    variable_monthly_total: Decimal | None = None


# --- suppression ------------------------------------------------------------------


def _explicit_suppression_set(
    position: CashPosition,
) -> set[tuple[str, str, str, date]]:
    """Dates already covered by an explicit future row for a given category stream.

    The key is `(category, direction, event_type, cash_date)` so that a scheduled
    "Next confirmed salary" suppresses a projected salary on the same date even when
    the descriptions differ.
    """
    suppressed: set[tuple[str, str, str, date]] = set()
    for effect in position.effects:
        if effect.state not in (RESERVED_DEBIT, EXPECTED_CREDIT):
            continue
        suppressed.add(
            (effect.category, _direction_for_state(effect.state), "*", effect.cash_date)
        )
    return suppressed


def _direction_for_state(state: str) -> str:
    return "credit" if state == EXPECTED_CREDIT else "debit"


# --- detection --------------------------------------------------------------------


def _detect_streams(
    position: CashPosition,
    events: tuple[Event, ...],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> tuple[_RecurrenceStream, ...]:
    historical = _historical_events(position, events, request.request_date, config)
    fixed = _detect_fixed_streams(historical, profile, config, rates)
    variable = _detect_variable_streams(historical, profile, config, rates)
    return tuple(fixed + variable)


def _historical_events(
    position: CashPosition,
    events: tuple[Event, ...],
    request_date: date,
    config: Config,
) -> tuple[Event, ...]:
    """Settled history within the lookback window, in cash-date order."""
    opening_ids = {
        e.event_id for e in position.effects if e.state == IN_OPENING_BALANCE
    }
    lookback_start = request_date - timedelta(days=config.lookback_days)
    return tuple(
        sorted(
            (
                event
                for event in events
                if event.event_id in opening_ids
                and lookback_start <= event.cash_date < request_date
                and event.amount is not None
            ),
            key=lambda e: (e.cash_date, e.event_id),
        )
    )


def _detect_fixed_streams(
    historical: tuple[Event, ...],
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> list[_RecurrenceStream]:
    """Monthly fixed streams keyed on day of month.

    Semi-monthly patterns (e.g. 1st and 15th salary) naturally become two streams
    because we split by day-of-month inside the description group.
    """
    # Group by (category, direction, event_type), then cluster descriptions by similarity.
    by_base: dict[tuple[str, str, str], list[Event]] = {}
    for event in historical:
        if event.category in config.variable_spend_categories:
            continue
        key = (event.category, event.direction, event.event_type)
        by_base.setdefault(key, []).append(event)

    desc_groups: list[tuple[tuple[str, str, str, str], list[Event]]] = []
    for base_key, events in by_base.items():
        # Stable order so grouping is deterministic.
        ordered = sorted(events, key=lambda e: (e.cash_date, e.event_id))
        for event in ordered:
            norm = _normalize_description(event.description)
            for group_key, group in desc_groups:
                if group_key[:3] != base_key:
                    continue
                rep = group[0]
                if _similar_description(
                    event.description, rep.description, config.description_similarity
                ):
                    group.append(event)
                    break
            else:
                desc_groups.append((base_key + (norm,), [event]))

    streams: list[_RecurrenceStream] = []
    for key, group in desc_groups:
        by_day: dict[int, list[Event]] = {}
        for event in group:
            by_day.setdefault(event.cash_date.day, []).append(event)

        for day, day_group in by_day.items():
            # Sort newest first so the last observed amount is the reference.
            day_group_sorted = sorted(
                day_group, key=lambda e: e.cash_date, reverse=True
            )
            ref_event = day_group_sorted[0]
            ref_amount = _home_amount(ref_event, profile.home_currency, rates)
            tol = _amount_tolerance(ref_amount, config)
            tolerant = [
                e
                for e in day_group_sorted
                if abs(_home_amount(e, profile.home_currency, rates) - ref_amount)
                <= tol
            ]
            count = len(tolerant)
            category = key[0]
            if count >= config.min_occurrences:
                streams.append(
                    _stream_from_events(
                        key, tolerant, profile.home_currency, rates, is_variable=False
                    )
                )
            elif (
                count == 2
                and config.protected_two_occurrence_project
                and category in profile.protected_categories
            ):
                streams.append(
                    _stream_from_events(
                        key, tolerant, profile.home_currency, rates, is_variable=False
                    )
                )
    return streams


def _detect_variable_streams(
    historical: tuple[Event, ...],
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> list[_RecurrenceStream]:
    """Variable essential categories forecast as a conservative monthly total."""
    by_key: dict[tuple[str, str, str], list[Event]] = {}
    for event in historical:
        if event.category not in config.variable_spend_categories:
            continue
        key = (event.category, event.direction, event.event_type)
        by_key.setdefault(key, []).append(event)

    streams: list[_RecurrenceStream] = []
    for key, group in by_key.items():
        if not group:
            continue
        totals = _monthly_totals_ordered(group, profile.home_currency, rates)
        if not totals:
            continue
        estimator = _variable_estimator(config.variable_spend_estimator)
        monthly_total = estimator(totals)
        latest = max(group, key=lambda e: (e.cash_date, e.event_id))
        days = [e.cash_date.day for e in group]
        placement_day = _median_day(days)
        norm = _normalize_description(latest.description)
        streams.append(
            _RecurrenceStream(
                stream_key=(key[0], key[1], key[2], norm),
                latest_event_id=latest.event_id,
                latest_amount=monthly_total,
                day_of_month=placement_day,
                direction=key[1],
                category=key[0],
                flexibility=latest.flexibility,
                is_variable=True,
                variable_monthly_total=monthly_total,
            )
        )
    return streams


def _stream_from_events(
    key: tuple[str, str, str, str],
    events: list[Event],
    home_currency: str,
    rates: Mapping[tuple[str, str, str], Decimal],
    is_variable: bool,
    monthly_total: Decimal | None = None,
) -> _RecurrenceStream:
    latest = max(events, key=lambda e: (e.cash_date, e.event_id))
    amount = (
        monthly_total
        if monthly_total is not None
        else _home_amount(latest, home_currency, rates)
    )
    return _RecurrenceStream(
        stream_key=key,
        latest_event_id=latest.event_id,
        latest_amount=amount,
        day_of_month=latest.cash_date.day,
        direction=key[1],
        category=key[0],
        flexibility=latest.flexibility,
        is_variable=is_variable,
        variable_monthly_total=monthly_total,
    )


# --- projection -------------------------------------------------------------------


def _project_stream(
    stream: _RecurrenceStream,
    request_date: date,
    horizon_end: date,
    explicit: set[tuple[str, str, str, date]],
    config: Config,
) -> list[CashEffect]:
    """Generate monthly occurrences from request_date+1 up to horizon_end inclusive."""
    if stream.direction == "credit" and not config.project_income_beyond_confirmed:
        # When disabled we still rely on explicit future rows already in the position.
        return []

    effects: list[CashEffect] = []
    year, month = request_date.year, request_date.month
    # Move to the first candidate month; if request_date is before the stream day,
    # the same month may still produce a future occurrence.
    current = date(year, month, 1)

    while current <= horizon_end:
        candidate = _clamped_date(current.year, current.month, stream.day_of_month)
        candidate = _roll_weekend(candidate, stream.direction, config)
        if candidate > request_date and candidate <= horizon_end:
            if (
                stream.category,
                stream.direction,
                "*",
                candidate,
            ) not in explicit:
                state = (
                    PROJECTED_CREDIT
                    if stream.direction == "credit"
                    else PROJECTED_DEBIT
                )
                reason = (
                    PROJECTED_RECURRING_INCOME
                    if stream.direction == "credit"
                    else (
                        PROJECTED_VARIABLE_SPENDING
                        if stream.is_variable
                        else PROJECTED_RECURRING_EXPENSE
                    )
                )
                effects.append(
                    CashEffect(
                        event_id=f"projected:{stream.latest_event_id}:{candidate.isoformat()}",
                        state=state,
                        cash_date=candidate,
                        amount_home=stream.latest_amount,
                        reason_code=reason,
                        category=stream.category,
                        flexibility=stream.flexibility,
                    )
                )
        # advance one month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        current = date(year, month, 1)
    return effects


# --- helpers ----------------------------------------------------------------------


def _home_amount(
    event: Event, home_currency: str, rates: Mapping[tuple[str, str, str], Decimal]
) -> Decimal:
    if event.amount is None:
        return ZERO
    if event.currency == home_currency:
        return event.amount
    return convert(event.amount, event.currency, home_currency, event.cash_date, rates)


def _amount_tolerance(ref: Decimal, config: Config) -> Decimal:
    relative = (ref * config.amount_tolerance_pct).copy_abs()
    return max(relative, config.amount_tolerance_abs)


_CURRENCY_CODES = frozenset({"INR", "IDR", "EUR", "USD", "ZAR"})
_MONTHS = frozenset(
    {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
)


def _normalize_description(description: str) -> str:
    """Uppercase, strip digits, punctuation, currency codes and months, collapse whitespace.

    City tails are left in place; the similarity gate decides whether they are
    close enough to merge. This keeps meaningful 4-letter words like RENT intact.
    """
    text = description.upper()
    text = re.sub(r"[0-9]", "", text)
    text = re.sub(r"[^A-Z\s]", "", text)
    tokens = [t for t in text.split() if t not in _CURRENCY_CODES and t not in _MONTHS]
    text = " ".join(tokens)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _similar_description(a: str, b: str, threshold: Decimal) -> bool:
    """Description similarity gated on a 3-token prefix match."""
    norm_a = _normalize_description(a)
    norm_b = _normalize_description(b)
    if not norm_a or not norm_b:
        return norm_a == norm_b
    tokens_a = norm_a.split()
    tokens_b = norm_b.split()
    prefix_len = min(3, len(tokens_a), len(tokens_b))
    if tokens_a[:prefix_len] != tokens_b[:prefix_len]:
        return False
    ratio = Decimal(str(SequenceMatcher(None, norm_a, norm_b).ratio()))
    return ratio >= threshold


def _monthly_totals_ordered(
    events: list[Event],
    home_currency: str,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> list[Decimal]:
    """Monthly totals, most recent month first, for variable-spend estimation."""
    totals: dict[tuple[int, int], Decimal] = {}
    for event in events:
        key = (event.cash_date.year, event.cash_date.month)
        totals[key] = totals.get(key, ZERO) + _home_amount(event, home_currency, rates)
    return [totals[k] for k in sorted(totals.keys(), reverse=True)]


def _monthly_totals(events: tuple[Event, ...]) -> dict[tuple[int, int], Decimal]:
    """Test helper: raw monthly totals keyed by (year, month) in home currency.

    Only exposed for unit tests that build domestic events.
    """
    totals: dict[tuple[int, int], Decimal] = {}
    for event in events:
        if event.amount is None:
            continue
        key = (event.cash_date.year, event.cash_date.month)
        totals[key] = totals.get(key, ZERO) + event.amount
    return totals


def _variable_estimator(name: str):
    def _last(totals: list[Decimal]) -> Decimal:
        return totals[0] if totals else ZERO

    def _median3(totals: list[Decimal]) -> Decimal:
        recent = totals[:3]
        return statistics.median(recent) if recent else ZERO

    def _mean6(totals: list[Decimal]) -> Decimal:
        recent = totals[:6]
        return sum(recent, ZERO) / len(recent) if recent else ZERO

    def _max_median3_mean6(totals: list[Decimal]) -> Decimal:
        return max(_median3(totals), _mean6(totals))

    estimators = {
        "last_month": _last,
        "median3": _median3,
        "mean6": _mean6,
        "max_median3_mean6": _max_median3_mean6,
    }
    return estimators.get(name, _max_median3_mean6)


def _median_day(days: list[int]) -> int:
    sorted_days = sorted(days)
    n = len(sorted_days)
    if n == 0:
        return 1
    return sorted_days[n // 2]


def _clamped_date(year: int, month: int, day: int) -> date:
    last_day = monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _roll_weekend(candidate: date, direction: str, config: Config) -> date:
    if not config.project_weekend_rolls:
        return candidate
    if direction == "credit":
        return _previous_weekday(candidate)
    return _next_weekday(candidate)


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _previous_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

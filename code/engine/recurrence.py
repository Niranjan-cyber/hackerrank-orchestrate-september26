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
from dataclasses import dataclass, replace
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
from .money import ZERO, money_scale
from .types import Config, Event, Profile, Request


# --- public API -------------------------------------------------------------------


def default_horizon_end(request: Request, config: Config) -> date:
    """The base forecast horizon: `request_date` plus the configured window, inclusive."""
    return request.request_date + timedelta(days=config.horizon_days)


def monthly_occurrences(
    day_of_month: int,
    start: date,
    end: date,
    direction: str,
) -> tuple[date, ...]:
    """Every monthly occurrence of `day_of_month` inside `[start, end]`, inclusive.

    The one rule for *where* a monthly occurrence lands, and therefore public: ticket
    05 projects a detected stream with it, and ticket 10 creates a stream from a
    confirmed salary fact with it, so the two cannot drift apart. Two adjustments, in
    this order, both conservative:

      * a day the month does not have clamps to that month's last day, so a 31st
        commitment still lands in February rather than spilling into March;
      * a weekend date moves to the nearest weekday - a debit forward, a credit back -
        so money leaves no earlier than stated and arrives no later.

    The walk starts a month early because the roll can carry the previous month's
    occurrence into the range (a Sunday the 30th of November becomes the 1st of
    December), and a candidate that rolls out of the range is simply dropped.
    """
    occurrences: list[date] = []
    cursor = _month_start(start)
    cursor = _previous_month_start(cursor)
    last = _month_start(end)
    while cursor <= last:
        candidate = _roll_weekend(
            _clamped_date(cursor.year, cursor.month, day_of_month), direction
        )
        if start <= candidate <= end:
            occurrences.append(candidate)
        cursor = _next_month_start(cursor)
    return tuple(sorted(set(occurrences)))


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month_start(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def _previous_month_start(month_start: date) -> date:
    if month_start.month == 1:
        return date(month_start.year - 1, 12, 1)
    return date(month_start.year, month_start.month - 1, 1)


def detect_streams(
    position: CashPosition,
    events: tuple[Event, ...],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
) -> tuple[Stream, ...]:
    """The recurring streams inferred from settled history, as a value.

    Public because ticket 08 needs the streams themselves, not only the occurrences
    they project: a spending change may cite only an event that belongs to a detected
    stream, and the id it cites is the stream's most recent settled occurrence. The
    caller may hand the result back to `projected_effects` / `with_projections` so
    detection runs once per request rather than once per consumer.
    """
    return _detect_streams(position, events, request, profile, config, rates or {})


def projected_effects(
    position: CashPosition,
    events: tuple[Event, ...],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
    *,
    horizon_end: date | None = None,
    streams: tuple[Stream, ...] | None = None,
) -> tuple[CashEffect, ...]:
    """Return inferred recurring effects for the forecast window.

    `position` supplies the explicit open rows used for duplicate-date suppression.
    `events` supplies the original rows used to detect history. `rates` is required
    only when a stream contains a foreign-currency event. `horizon_end` overrides the
    default `request_date + config.horizon_days`, so a caller evaluating a plan that
    reaches past the base window can project recurring spend across the whole plan.
    `streams` supplies an already-detected set, so a caller that needs the streams for
    its own purposes does not pay for detection twice.
    """
    rates = rates or {}
    if streams is None:
        streams = _detect_streams(position, events, request, profile, config, rates)
    explicit = _explicit_suppression_set(position, events)
    projected: list[CashEffect] = []
    horizon_end = horizon_end or default_horizon_end(request, config)
    for stream in streams:
        projected.extend(
            _project_stream(stream, request.request_date, horizon_end, explicit, config)
        )
    return tuple(sorted(projected, key=lambda e: (e.cash_date, e.event_id)))


def with_projections(
    position: CashPosition,
    events: tuple[Event, ...],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
    *,
    horizon_end: date | None = None,
    streams: tuple[Stream, ...] | None = None,
) -> CashPosition:
    """Return a position with inferred streams merged in and its horizon recorded.

    This is the one place the engine layers Ticket 05 projections onto Ticket 04's
    explicit cash position. It stamps `projected_until` so `simulate` can tell how far
    the inferred coverage reaches; a caller that needs a longer window passes a larger
    `horizon_end` here rather than extending the simulator past its data.
    """
    horizon_end = horizon_end or default_horizon_end(request, config)
    projected = projected_effects(
        position,
        events,
        request,
        profile,
        config,
        rates,
        horizon_end=horizon_end,
        streams=streams,
    )
    if projected:
        position = replace(
            position,
            effects=tuple(
                sorted(
                    position.effects + projected,
                    key=lambda e: (e.cash_date, e.event_id),
                )
            ),
        )
    return replace(position, projected_until=horizon_end)


# --- stream value type --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stream:
    """One detected recurring stream ready for projection.

    `latest_event_id` is the stream's most recent settled occurrence before
    `request_date` - the id a spending change must cite (CONTEXT.md section 10).
    `latest_amount` is what one projected occurrence costs in home currency: the
    latest observed amount for a fixed stream, and for a variable one the share of
    the forecast monthly total this slot carries - the whole total under the
    `monthly_total` shape, one day's portion of it under `individual_events`.
    """

    stream_key: tuple[str, str, str, str]
    # category, direction, event_type, discriminator. The discriminator is the
    # normalized description for a fixed stream; a variable stream appends its
    # placement day, so the `individual_events` shape's per-day slots stay distinct.
    latest_event_id: str
    latest_amount: Decimal
    day_of_month: int
    direction: str
    category: str
    flexibility: str
    is_variable: bool


# --- suppression ------------------------------------------------------------------


def _explicit_suppression_set(
    position: CashPosition,
    events: tuple[Event, ...],
) -> set[tuple[str, str, str, date]]:
    """Dates already covered by an explicit future row for a given category stream.

    The key is `(category, direction, event_type, cash_date)` so that a scheduled
    "Next confirmed salary" suppresses a projected salary on the same date even when
    the descriptions differ.
    """
    event_type_by_id = {event.event_id: event.event_type for event in events}
    suppressed: set[tuple[str, str, str, date]] = set()
    for effect in position.effects:
        if effect.state not in (RESERVED_DEBIT, EXPECTED_CREDIT):
            continue
        event_type = event_type_by_id.get(effect.event_id, "")
        suppressed.add(
            (
                effect.category,
                _direction_for_state(effect.state),
                event_type,
                effect.cash_date,
            )
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
) -> tuple[Stream, ...]:
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
) -> list[Stream]:
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
                if _similar_description(event.description, rep.description, config):
                    group.append(event)
                    break
            else:
                desc_groups.append((base_key + (norm,), [event]))

    streams: list[Stream] = []
    for key, group in desc_groups:
        # Cluster observed days-of-month using the monthly tolerance ladder so that
        # a stream that lands on slightly different days still groups together.
        day_clusters = _cluster_days(
            [e.cash_date.day for e in group], config.monthly_date_tolerance_days
        )
        for cluster_days in day_clusters:
            day_group = [e for e in group if e.cash_date.day in cluster_days]
            if not day_group:
                continue
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
) -> list[Stream]:
    """Variable essential categories forecast as a conservative monthly total."""
    by_key: dict[tuple[str, str, str], list[Event]] = {}
    for event in historical:
        if event.category not in config.variable_spend_categories:
            continue
        key = (event.category, event.direction, event.event_type)
        by_key.setdefault(key, []).append(event)

    streams: list[Stream] = []
    for key, group in by_key.items():
        if len(group) < config.min_occurrences:
            continue
        totals = _monthly_totals_ordered(group, profile.home_currency, rates)
        if not totals:
            continue
        estimator = _variable_estimator(config.variable_spend_estimator)
        # A variable-spend forecast is a modelled home-currency money amount, so it is
        # normalised to the currency's 2dp scale before it enters the ledger. See
        # `money.money_scale` for why this is required for exact, order-independent
        # floor arithmetic and not merely presentation.
        monthly_total = money_scale(estimator(totals))
        latest = max(group, key=lambda e: (e.cash_date, e.event_id))
        norm = _normalize_description(latest.description)
        for placement_day, share in _variable_placements(
            group, monthly_total, profile.home_currency, rates, config
        ):
            streams.append(
                Stream(
                    stream_key=(key[0], key[1], key[2], f"{norm}@{placement_day}"),
                    latest_event_id=latest.event_id,
                    latest_amount=share,
                    day_of_month=placement_day,
                    direction=key[1],
                    category=key[0],
                    flexibility=latest.flexibility,
                    is_variable=True,
                )
            )
    return streams


def _variable_placements(
    group: list[Event],
    monthly_total: Decimal,
    home_currency: str,
    rates: Mapping[tuple[str, str, str], Decimal],
    config: Config,
) -> list[tuple[int, Decimal]]:
    """The (day-of-month, amount) slots one variable month is forecast into.

    Both shapes forecast the same `monthly_total`; they differ only in how it is
    placed inside the month. Ticket 14 sweeps the choice.
    """
    days = [event.cash_date.day for event in group]
    # Validated on every path, not only the one that consults it. `individual_events`
    # never asks for a placement day unless the history prices to nothing, so leaving
    # the check inside `_placement_day` would let a mistyped sweep value run the whole
    # dataset silently under the shipped shape.
    _check_placement(config)

    if config.variable_spend_shape == "monthly_total":
        return [(_placement_day(days, config), monthly_total)]

    if config.variable_spend_shape != "individual_events":
        raise ValueError(
            f"unknown variable_spend_shape {config.variable_spend_shape!r}; "
            "expected one of ['individual_events', 'monthly_total']"
        )

    # Split the estimated total across the observed days-of-month in proportion to
    # each day's share of the observed spend.
    by_day: dict[int, Decimal] = {}
    for event in group:
        by_day[event.cash_date.day] = by_day.get(
            event.cash_date.day, ZERO
        ) + _home_amount(event, home_currency, rates)
    observed_total = sum(by_day.values(), ZERO)
    if observed_total <= ZERO:
        return [(_placement_day(days, config), monthly_total)]

    # The remainder goes on the *last* day: a share can round to nothing while every
    # later day rounds up, and on the earliest day that drives the remainder negative
    # - a projected debit below zero is a phantom credit in the ledger. Clamped as
    # well as ordered, so rounding can only ever under-forecast by pennies.
    ordered_days = sorted(by_day)
    shares = [
        (day, money_scale(monthly_total * by_day[day] / observed_total))
        for day in ordered_days[:-1]
    ]
    last = monthly_total - sum((amount for _, amount in shares), ZERO)
    return [*shares, (ordered_days[-1], max(last, ZERO))]


def _check_placement(config: Config) -> None:
    if config.variable_spend_placement not in ("earliest", "median", "latest"):
        raise ValueError(
            f"unknown variable_spend_placement {config.variable_spend_placement!r}; "
            "expected one of ['earliest', 'latest', 'median']"
        )


def _placement_day(days: list[int], config: Config) -> int:
    """Which observed day-of-month a whole-month variable total is placed on."""
    _check_placement(config)
    if config.variable_spend_placement == "earliest":
        return min(days)
    if config.variable_spend_placement == "median":
        return _median_day(days)
    return max(days)


def _cluster_days(days: list[int], tolerance: int) -> list[list[int]]:
    """Group day-of-month values within `tolerance` of a canonical day.

    Each cluster is anchored at its smallest day and greedily collects all days
    within +/- `tolerance`. This keeps semi-monthly patterns split while allowing
    a fixed stream to wobble by a few days month to month.
    """
    if not days:
        return []
    remaining = sorted(days)
    clusters: list[list[int]] = []
    while remaining:
        canonical = remaining[0]
        cluster = [d for d in remaining if abs(d - canonical) <= tolerance]
        clusters.append(cluster)
        remaining = [d for d in remaining if d not in cluster]
    return clusters


def _stream_from_events(
    key: tuple[str, str, str, str],
    events: list[Event],
    home_currency: str,
    rates: Mapping[tuple[str, str, str], Decimal],
    is_variable: bool,
    monthly_total: Decimal | None = None,
) -> Stream:
    latest = max(events, key=lambda e: (e.cash_date, e.event_id))
    amount = (
        monthly_total
        if monthly_total is not None
        else _home_amount(latest, home_currency, rates)
    )
    return Stream(
        stream_key=key,
        latest_event_id=latest.event_id,
        latest_amount=amount,
        day_of_month=latest.cash_date.day,
        direction=key[1],
        category=key[0],
        flexibility=latest.flexibility,
        is_variable=is_variable,
    )


# --- projection -------------------------------------------------------------------


def _project_stream(
    stream: Stream,
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
    event_type = stream.stream_key[2]
    state = PROJECTED_CREDIT if stream.direction == "credit" else PROJECTED_DEBIT
    reason = (
        PROJECTED_RECURRING_INCOME
        if stream.direction == "credit"
        else (
            PROJECTED_VARIABLE_SPENDING
            if stream.is_variable
            else PROJECTED_RECURRING_EXPENSE
        )
    )
    for candidate in monthly_occurrences(
        stream.day_of_month,
        request_date + timedelta(days=1),
        horizon_end,
        stream.direction,
    ):
        if (stream.category, stream.direction, event_type, candidate) in explicit:
            continue
        effects.append(
            CashEffect(
                # The placement day is part of the id, not decoration: under
                # `individual_events` every slot of a variable stream carries the same
                # `latest_event_id`, and month-end clamping or a weekend roll lands
                # two placement days on one date often enough that ids collide
                # without it. Evidence amendment and the trace ledger address effects
                # by id, so a collision merges distinct occurrences.
                event_id=(
                    f"projected:{stream.latest_event_id}"
                    f":{stream.day_of_month:02d}:{candidate.isoformat()}"
                ),
                state=state,
                cash_date=candidate,
                amount_home=stream.latest_amount,
                reason_code=reason,
                category=stream.category,
                flexibility=stream.flexibility,
                source_event_id=stream.latest_event_id,
            )
        )
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
    """Uppercase, strip digits, punctuation, currency codes and months."""
    text = description.upper()
    text = re.sub(r"[0-9]", "", text)
    text = re.sub(r"[^A-Z\s]", "", text)
    tokens = [t for t in text.split() if t not in _CURRENCY_CODES and t not in _MONTHS]
    text = " ".join(tokens)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _similar_description(a: str, b: str, config: Config) -> bool:
    """Description similarity gated on a prefix-token match."""
    norm_a = _normalize_description(a)
    norm_b = _normalize_description(b)
    if not norm_a or not norm_b:
        return norm_a == norm_b
    tokens_a = norm_a.split()
    tokens_b = norm_b.split()
    prefix_len = min(config.description_prefix_tokens, len(tokens_a), len(tokens_b))
    if tokens_a[:prefix_len] != tokens_b[:prefix_len]:
        return False
    ratio = Decimal(str(SequenceMatcher(None, norm_a, norm_b).ratio()))
    return ratio >= config.description_similarity


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
    if name not in estimators:
        raise ValueError(
            f"unknown variable_spend_estimator {name!r}; "
            f"expected one of {sorted(estimators)}"
        )
    return estimators[name]


def _median_day(days: list[int]) -> int:
    sorted_days = sorted(days)
    n = len(sorted_days)
    if n == 0:
        return 1
    return sorted_days[n // 2]


def _clamped_date(year: int, month: int, day: int) -> date:
    last_day = monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _roll_weekend(candidate: date, direction: str) -> date:
    """Roll weekend dates to the nearest weekday: debits forward, credits back."""
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

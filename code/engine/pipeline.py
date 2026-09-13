"""The deterministic core.

    run_pipeline(dataset, extraction_facts, config) -> tuple[OutputRow, ...]

Pure by construction: it receives already-loaded dataset values and already-validated
facts, and touches no filesystem, no network and no clock. That is what lets the 25
solved samples run with no credential and no API.

BUILD STATE - READ THIS BEFORE JUDGING THE LOGIC
The seam signature and every output invariant are final, so each ticket replaces the
body of `_build_decision` without touching anything around it. Implemented so far:

  04  cash-state classification and the event lifecycle          (committed)
  05  recurrence detection and projection                        (committed)
  06  the 90-day simulation, amount_safe_to_pay, earliest date   (committed)
  07  candidate plans and lexicographic ranking                  (committed)
  08  spending changes and the pruning rule                      (committed)
  09  reason codes, rendered explanations and the trace ledger   (committed)
  10  evidence authority, conflict precedence, blank amounts       (this change)

Every ticket is now implemented; `extraction_facts` is read, and a row is decided from
the dataset *plus* whatever the evidence layer had the authority to change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from .cash import (
    UNKNOWN_AMOUNT,
    CashPosition,
    UnresolvedAmountError,
    cash_position,
)
from .evidence import apply_evidence, resolve_blank_amounts
from .money import ZERO, format_explanation_amount
from .plans import Plan, best_plan, candidate_plans, required_horizon_end
from .recurrence import default_horizon_end, detect_streams, with_projections
from .simulate import (
    Ledger,
    amount_safe_to_pay,
    earliest_date_for_full_payment,
    simulate,
)
from .spending import eligible_changes
from .types import Config, Dataset, Fact, OutputRow, Reason


def _facts_by_user(
    extraction_facts: tuple[Fact, ...],
) -> dict[str, tuple[Fact, ...]]:
    grouped: dict[str, list[Fact]] = {}
    for fact in extraction_facts:
        grouped.setdefault(fact.user_id, []).append(fact)
    return {user_id: tuple(facts) for user_id, facts in grouped.items()}


def run_pipeline(
    dataset: Dataset,
    extraction_facts: tuple[Fact, ...],
    config: Config,
) -> tuple[OutputRow, ...]:
    """Decide every request. One output row per request, in dataset order."""
    facts_by_user = _facts_by_user(extraction_facts)
    return tuple(
        _decide(
            request=request,
            dataset=dataset,
            facts=facts_by_user.get(request.user_id, ()),
            config=config,
        )
        for request in dataset.requests
    )


@dataclass(frozen=True, slots=True)
class RequestTrace:
    """Everything Ticket 09 needs to explain one request: the published row and the
    day-by-day ledger it was certified against.

    The ledger is the winning plan's own (`Plan.ledger` - already computed once by
    `plans._certify`, and reused rather than re-simulated, so a trace can never
    disagree with the certification that produced the row). When nothing was safe,
    there is no plan to point at, so this falls back to the base position with no
    payment injected - the same ledger the pruning reasons in `row.reasons` refer to.
    """

    row: OutputRow
    ledger: Ledger


def trace_request(
    request_id: str,
    dataset: Dataset,
    extraction_facts: tuple[Fact, ...],
    config: Config,
) -> RequestTrace:
    """Rebuild one request's decision, paired with its certifying ledger.

    A developer-facing entry point, not one the deterministic core calls itself -
    `code/main.py` is the only caller. Recomputes rather than caching, because a
    per-request trace is asked for occasionally, not on every run, and `_build_decision`
    is pure and cheap for one request.
    """
    request = next((r for r in dataset.requests if r.request_id == request_id), None)
    if request is None:
        raise ValueError(f"no such request: {request_id}")

    facts = _facts_by_user(extraction_facts).get(request.user_id, ())
    decision = _build_decision(
        request=request, dataset=dataset, facts=facts, config=config
    )
    if decision.chosen is not None and decision.chosen.ledger is not None:
        ledger = decision.chosen.ledger
    else:
        try:
            ledger = simulate(decision.position, config)
        except UnresolvedAmountError:
            # The same still-blank future outflow that made `_build_decision` degrade
            # to `safe=ZERO, earliest=None` (see FORECAST_INCOMPLETE_UNRESOLVED_AMOUNT
            # in `decision.row.reasons`) makes an honest ledger impossible here too.
            # A trace is a diagnostic, not a certification, so it drops the unresolved
            # rows rather than crashing - the reason already on the row is what tells a
            # developer the ledger below is incomplete and why.
            resolvable = replace(
                decision.position,
                effects=tuple(
                    effect
                    for effect in decision.position.effects
                    if effect.state != UNKNOWN_AMOUNT
                ),
            )
            ledger = simulate(resolvable, config)
    return RequestTrace(row=decision.row, ledger=ledger)


@dataclass(frozen=True, slots=True)
class _Decision:
    """Internal: everything `_decide` computes, kept around for `trace_request`."""

    row: OutputRow
    position: CashPosition
    chosen: Plan | None


def _decide(
    request, dataset: Dataset, facts: tuple[Fact, ...], config: Config
) -> OutputRow:
    return _build_decision(
        request=request, dataset=dataset, facts=facts, config=config
    ).row


def _build_decision(
    request, dataset: Dataset, facts: tuple[Fact, ...], config: Config
) -> _Decision:
    profile = dataset.profiles[request.user_id]
    reasons: list[Reason] = []
    events = dataset.events_by_user.get(request.user_id, ())

    # Ticket 10, first half: price the blank amounts *before* anything is classified,
    # because a blank amount decides how its own row is classified. The receipt image
    # first, then deterministic imputation; never zero, and never a repair of a figure
    # the evidence did not supply.
    blanks = resolve_blank_amounts(
        events, facts, request, profile, config, dataset.rates
    )
    reasons.extend(blanks.reasons)

    # Ticket 04: the real cash position - every event row classified, foreign amounts
    # converted at their dated rate, lifecycle resolved. Ticket 05 layers inferred
    # recurring streams on top of this, and ticket 06 simulates it forward.
    position = cash_position(
        request,
        profile,
        events,
        dataset.rates,
        blanks.overrides,
    )

    # Ticket 05: layer inferred recurring streams onto the cash position. Ticket 06's
    # simulator is the consumer; keeping the two steps separate means a projection bug
    # cannot hide inside the simulator.
    #
    # The window is stretched to cover the longest installment schedule ticket 07 could
    # certify. `simulate` refuses to certify a plan that reaches past the projected
    # window - rightly, since it would be testing the floor across days whose recurring
    # spend was never forecast - so without this a long option is dropped as
    # PLAN_BEYOND_PROJECTION_HORIZON before its eligibility is ever considered. Under
    # the shipped config this changes nothing: an eligible option finishes on or before
    # `desired_completion_date`, and the furthest deadline in the dataset is 86 days out.
    options = dataset.options_by_request.get(request.request_id, ())
    horizon = default_horizon_end(request, config)
    needed = required_horizon_end(request, profile, options, config)
    if needed is not None and needed > horizon:
        horizon = needed
    # Detected once and used twice: ticket 05 projects the streams forward, and ticket
    # 08 needs the streams themselves, because only an event that belongs to one may
    # be cited as a spending change.
    streams = detect_streams(position, events, request, profile, config, dataset.rates)
    position = with_projections(
        position,
        events,
        request,
        profile,
        config,
        dataset.rates,
        horizon_end=horizon,
        streams=streams,
    )

    # --- Ticket 10, second half: evidence amends the projections -------------------
    # Here rather than earlier because every amendment is expressed against projected
    # occurrences, and here rather than later because the simulator must see the
    # amended streams. A fact may always make the forecast more conservative; it may
    # make it less conservative only when it is a confirmed employer salary passing all
    # five authority conditions, which is what keeps the scam traps inert.
    #
    # `streams` above is deliberately *not* re-derived from the amended position:
    # ticket 08 measures a spending change on the dataset event it cites, and a
    # commitment the user may stop is a commitment the dataset recorded, not one
    # evidence inferred.
    applied = apply_evidence(
        position, facts, request, profile, config, events, dataset.rates
    )
    position = applied.position
    reasons.extend(applied.reasons)

    # --- Ticket 06: simulate the fixed 90-day window ------------------------------
    reasons.append(
        Reason(
            code="OPENING_BALANCE",
            amount=position.opening_balance,
            detail="balance as of request_date",
        )
    )
    reasons.append(
        Reason(code="MINIMUM_BALANCE_FLOOR", amount=position.minimum_balance)
    )

    # `safe` and `earliest` are pure capacity figures. The simulator applies the
    # same-day ordering and the per-event floor test, and knows nothing about
    # spending changes or the user's payment-method preferences.
    try:
        safe = amount_safe_to_pay(position, config, request.requested_amount)
        earliest = earliest_date_for_full_payment(
            position, config, request.requested_amount
        )
    except UnresolvedAmountError:
        # A future outflow is still unpriced (a blank amount awaiting ticket 12's
        # image extraction). There is no honest safe figure, so degrade to the
        # conservative "cannot prove it is safe" row rather than under-reserve.
        safe = ZERO
        earliest = None
        reasons.append(
            Reason(
                code="FORECAST_INCOMPLETE_UNRESOLVED_AMOUNT",
                detail="a future outflow has no resolved amount",
            )
        )

    for effect in position.reserved_debits:
        reasons.append(
            Reason(
                code=effect.reason_code,
                event_id=effect.event_id,
                amount=effect.amount_home,
                detail=f"due {effect.cash_date.isoformat()}",
            )
        )
    for effect in position.unknown_amounts:
        # A future outflow of unknown size. Recorded rather than assumed to be zero;
        # ticket 12 resolves these from their linked receipt images.
        reasons.append(
            Reason(
                code=effect.reason_code,
                event_id=effect.event_id,
                detail=f"amount unresolved, due {effect.cash_date.isoformat()}",
            )
        )
    for effect in position.projected_debits:
        reasons.append(
            Reason(
                code=effect.reason_code,
                event_id=effect.event_id,
                amount=effect.amount_home,
                detail=f"projected recurring expense due {effect.cash_date.isoformat()}",
            )
        )
    for effect in position.projected_credits:
        reasons.append(
            Reason(
                code=effect.reason_code,
                event_id=effect.event_id,
                amount=effect.amount_home,
                detail=f"projected recurring income due {effect.cash_date.isoformat()}",
            )
        )
    reasons.append(
        Reason(
            code="WINDOW_MINIMUM_HEADROOM",
            amount=safe,
            detail="largest amount safe to pay on request_date",
        )
    )
    if earliest is None:
        reasons.append(
            Reason(
                code="NO_SAFE_FULL_PAYMENT_DATE",
                detail="full payment never holds the floor within the window",
            )
        )
    else:
        reasons.append(
            Reason(code="EARLIEST_FULL_PAYMENT_DATE", detail=earliest.isoformat())
        )

    # --- Ticket 07: enumerate, prune, certify, rank --------------------------------
    # Everything method-specific lives in `plans`. This shell only hands it the
    # request, the certified position and the two capacity figures, and renders
    # whatever comes back. Adding ticket 08's spending-change variants means adding
    # candidates there, not another branch here.
    # --- Ticket 08: what the user has permitted themselves to change ---------------
    # Offered to the generator, not applied here. `plans` decides whether the request
    # needs them at all, and the ledger still certifies whatever comes back.
    changes = eligible_changes(
        streams,
        {event.event_id: event for event in events},
        profile,
        dataset.rates,
    )

    candidates = candidate_plans(
        request,
        profile,
        options,
        position,
        config,
        safe=safe,
        earliest=earliest,
        changes=changes,
    )
    reasons.extend(candidates.reasons)
    chosen = best_plan(candidates.plans)

    if chosen is None:
        reasons.append(
            Reason(
                code="NO_SAFE_ELIGIBLE_PLAN",
                detail="no eligible payment method survived the floor test",
            )
        )
        reasons_tuple = tuple(reasons)
        row = OutputRow(
            request_id=request.request_id,
            amount_safe_to_pay=safe,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan=(),
            # `earliest` is a capacity figure, not a plan field: it is reported
            # whenever the full amount becomes safe within the window, independent of
            # whether any eligible plan was found. The problem statement blanks it only
            # when the full amount is never safe in the forecast period.
            earliest_date_for_full_payment=earliest,
            spending_changes_needed=(),
            decision_explanation=_explain_not_recommended(
                request, profile, safe, reasons_tuple
            ),
            reasons=reasons_tuple,
        )
        return _Decision(row=row, position=position, chosen=None)

    reasons.extend(chosen.reasons)
    reasons_tuple = tuple(reasons)
    row = OutputRow(
        request_id=request.request_id,
        amount_safe_to_pay=safe,
        affordability_status=chosen.status,
        recommended_payment_method=chosen.method,
        payment_plan=chosen.payments,
        earliest_date_for_full_payment=earliest,
        spending_changes_needed=chosen.spending_changes,
        decision_explanation=_explain(chosen, request, profile, reasons_tuple),
        reasons=reasons_tuple,
    )
    return _Decision(row=row, position=position, chosen=chosen)


# --- explanation templates --------------------------------------------------------
# Deterministic and rendered from engine state, never model-written (D14). Amounts and
# the minimum-balance floor are read off the `reasons` this same request already
# collected (D11: "rendered from reason codes") rather than re-derived, so an
# explanation can never cite a number the row's own provenance disagrees with. The
# shape is the sample style: "Pay ZAR 25,256 today. This leaves at least ZAR 18,000
# available over the next 90 days." - comma-grouped, unlike the graded CSV columns.


def _floor(reasons: Sequence[Reason], profile) -> Decimal:
    for reason in reasons:
        if reason.code == "MINIMUM_BALANCE_FLOOR" and reason.amount is not None:
            return reason.amount
    return profile.minimum_balance_to_keep  # defensive: always present in practice


def _explain_now(request, profile, reasons: Sequence[Reason]) -> str:
    return (
        f"Pay {profile.home_currency} "
        f"{format_explanation_amount(request.requested_amount)} "
        f"on {request.request_date.isoformat()}. This keeps at least "
        f"{profile.home_currency} {format_explanation_amount(_floor(reasons, profile))} "
        f"available."
    )


def _explain_wait(request, profile, earliest, reasons: Sequence[Reason]) -> str:
    return (
        f"Wait until {earliest.isoformat()} before paying "
        f"{profile.home_currency} "
        f"{format_explanation_amount(request.requested_amount)}. "
        f"That is the earliest date the full payment keeps at least "
        f"{profile.home_currency} {format_explanation_amount(_floor(reasons, profile))} "
        f"available."
    )


def _explain_not_recommended(request, profile, safe, reasons: Sequence[Reason]) -> str:
    return (
        f"Paying {profile.home_currency} "
        f"{format_explanation_amount(request.requested_amount)} is not safe on "
        f"{request.request_date.isoformat()}. At most {profile.home_currency} "
        f"{format_explanation_amount(safe)} can be paid while keeping "
        f"{profile.home_currency} {format_explanation_amount(_floor(reasons, profile))} "
        f"available."
    )


def _change_clauses(plan, profile) -> str:
    """ "Stop the online backup subscription and reduce the streaming subscription..."

    The commitment names and amounts come off the plan's own `SPENDING_CHANGE_*`
    reason codes (D11), never re-derived from the dataset, so the sentence can only
    ever describe a change the plan actually carries in `spending_changes_needed`.
    """
    clauses = []
    for reason in plan.reasons:
        name = reason.detail.strip().lower() or "this recurring expense"
        if reason.code == "SPENDING_CHANGE_STOP":
            clauses.append(f"stop the {name}")
        elif reason.code == "SPENDING_CHANGE_REDUCE":
            # The target is quoted in the commitment's own currency, not the home one:
            # it is the dataset's `minimum_allowed_amount` for that event, and the
            # output column cites the same number.
            clauses.append(
                f"reduce the {name} to {reason.currency or profile.home_currency} "
                f"{format_explanation_amount(reason.amount)}"
            )
    if not clauses:  # defensive: a change plan always carries its change reasons
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return ", ".join(clauses[:-1]) + f" and {clauses[-1]}"


def _explain(plan, request, profile, reasons: Sequence[Reason]) -> str:
    """Render the winning plan from its own reason codes (D11).

    A spending-change plan is the method's own sentence with the changes in front of
    it - "Stop the family streaming plan, then pay EUR 620.40 on 2026-01-03." - rather
    than a sentence of its own, so that the method still describes itself. An
    installment plan funded by a change therefore still says it is three payments,
    which a single hard-coded "then pay X today" tail would have got wrong.
    """
    body = _explain_method(plan, request, profile, reasons)
    if not plan.needs_spending_changes:
        return body
    changes = _change_clauses(plan, profile)
    if not changes:
        return body
    return f"{changes[0].upper()}{changes[1:]}, then {body[0].lower()}{body[1:]}"


def _explain_method(plan, request, profile, reasons: Sequence[Reason]) -> str:
    if plan.method == "full_payment":
        return _explain_now(request, profile, reasons)
    if plan.method == "wait":
        return _explain_wait(request, profile, plan.start_date, reasons)
    if plan.method == "partial_payment":
        return _explain_partial(request, profile, plan, reasons)
    return _explain_installments(request, profile, plan, reasons)


def _explain_partial(request, profile, plan, reasons: Sequence[Reason]) -> str:
    (_, today), (later_date, later) = plan.payments
    return (
        f"Pay {profile.home_currency} {format_explanation_amount(today)} on "
        f"{request.request_date.isoformat()} and the remaining "
        f"{profile.home_currency} {format_explanation_amount(later)} on "
        f"{later_date.isoformat()}. This completes the full request and keeps "
        f"{profile.home_currency} "
        f"{format_explanation_amount(_floor(reasons, profile))} protected."
    )


def _explain_installments(request, profile, plan, reasons: Sequence[Reason]) -> str:
    amount = plan.payments[0][1]
    return (
        f"Use {plan.payment_count} installments of {profile.home_currency} "
        f"{format_explanation_amount(amount)}, starting {plan.start_date.isoformat()}. "
        f"This keeps at least {profile.home_currency} "
        f"{format_explanation_amount(_floor(reasons, profile))} available."
    )

"""The deterministic core.

    run_pipeline(dataset, extraction_facts, config) -> tuple[OutputRow, ...]

Pure by construction: it receives already-loaded dataset values and already-validated
facts, and touches no filesystem, no network and no clock. That is what lets the 25
solved samples run with no credential and no API.

BUILD STATE - READ THIS BEFORE JUDGING THE LOGIC
The seam signature and every output invariant are final, so each ticket replaces the
body of `_decide` without touching anything around it. Implemented so far:

  04  cash-state classification and the event lifecycle          (committed)
  05  recurrence detection and projection                        (committed)
  06  the 90-day simulation, amount_safe_to_pay, earliest date   (committed)
  07  candidate plans and lexicographic ranking                  (committed)
  08  spending changes and the pruning rule                      (this change)

Still to come, and deliberately absent here:

  09  reason codes and rendered explanations - the `_explain*` renderers at the foot
      of this file are the placeholder. Ticket 08 left two things for it: the amounts
      are printed without the thousands separators the samples use ("INR 50200", not
      "INR 50,200"), and the change clauses are rendered from `SPENDING_CHANGE_*`
      reason codes, which is the shape ticket 09 generalises.
  10  evidence authority and conflict precedence
"""

from __future__ import annotations

from .cash import UnresolvedAmountError, cash_position
from .money import ZERO, format_plan_amount
from .plans import best_plan, candidate_plans, required_horizon_end
from .recurrence import default_horizon_end, detect_streams, with_projections
from .simulate import amount_safe_to_pay, earliest_date_for_full_payment
from .spending import eligible_changes
from .types import Config, Dataset, Fact, OutputRow, Reason


def run_pipeline(
    dataset: Dataset,
    extraction_facts: tuple[Fact, ...],
    config: Config,
) -> tuple[OutputRow, ...]:
    """Decide every request. One output row per request, in dataset order."""
    facts_by_user: dict[str, list[Fact]] = {}
    for fact in extraction_facts:
        facts_by_user.setdefault(fact.user_id, []).append(fact)

    return tuple(
        _decide(
            request=request,
            dataset=dataset,
            facts=tuple(facts_by_user.get(request.user_id, ())),
            config=config,
        )
        for request in dataset.requests
    )


def _decide(
    request, dataset: Dataset, facts: tuple[Fact, ...], config: Config
) -> OutputRow:
    profile = dataset.profiles[request.user_id]
    reasons: list[Reason] = []

    # Ticket 04: the real cash position - every event row classified, foreign amounts
    # converted at their dated rate, lifecycle resolved. Ticket 05 layers inferred
    # recurring streams on top of this, and ticket 06 simulates it forward.
    position = cash_position(
        request,
        profile,
        dataset.events_by_user.get(request.user_id, ()),
        dataset.rates,
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
    events = dataset.events_by_user.get(request.user_id, ())
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
        return OutputRow(
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
            decision_explanation=_explain_not_recommended(request, profile, safe),
            reasons=tuple(reasons),
        )

    reasons.extend(chosen.reasons)
    return OutputRow(
        request_id=request.request_id,
        amount_safe_to_pay=safe,
        affordability_status=chosen.status,
        recommended_payment_method=chosen.method,
        payment_plan=chosen.payments,
        earliest_date_for_full_payment=earliest,
        spending_changes_needed=chosen.spending_changes,
        decision_explanation=_explain(chosen, request, profile),
        reasons=tuple(reasons),
    )


# --- explanation templates --------------------------------------------------------
# Deterministic and rendered from engine state, never model-written (D14). Ticket 09
# replaces these with reason-code rendering; the shape is already the sample style:
# "Pay ZAR 25,256 today. This leaves at least ZAR 18,000 available over the next 90 days."


def _explain_now(request, profile) -> str:
    return (
        f"Pay {profile.home_currency} {format_plan_amount(request.requested_amount)} "
        f"on {request.request_date.isoformat()}. This keeps at least "
        f"{profile.home_currency} {format_plan_amount(profile.minimum_balance_to_keep)} "
        f"available."
    )


def _explain_wait(request, profile, earliest) -> str:
    return (
        f"Wait until {earliest.isoformat()} before paying "
        f"{profile.home_currency} {format_plan_amount(request.requested_amount)}. "
        f"That is the earliest date the full payment keeps at least "
        f"{profile.home_currency} {format_plan_amount(profile.minimum_balance_to_keep)} "
        f"available."
    )


def _explain_not_recommended(request, profile, safe) -> str:
    return (
        f"Paying {profile.home_currency} "
        f"{format_plan_amount(request.requested_amount)} is not safe on "
        f"{request.request_date.isoformat()}. At most {profile.home_currency} "
        f"{format_plan_amount(safe)} can be paid while keeping "
        f"{profile.home_currency} {format_plan_amount(profile.minimum_balance_to_keep)} "
        f"available."
    )


def _change_clauses(plan, profile) -> str:
    """ "Stop the online backup subscription and reduce the streaming subscription..."

    The commitment names come off the plan's own reason codes, so the sentence is
    rendered from engine state rather than re-derived from the dataset (D11/D14).
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
                f"{format_plan_amount(reason.amount)}"
            )
    if not clauses:  # defensive: a change plan always carries its change reasons
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return ", ".join(clauses[:-1]) + f" and {clauses[-1]}"


def _explain(plan, request, profile) -> str:
    """Render the winning plan. Ticket 09 replaces this with reason-code rendering.

    A spending-change plan is the method's own sentence with the changes in front of
    it - "Stop the family streaming plan, then pay EUR 620.40 on 2026-01-03." - rather
    than a sentence of its own, so that the method still describes itself. An
    installment plan funded by a change therefore still says it is three payments,
    which a single hard-coded "then pay X today" tail would have got wrong.
    """
    body = _explain_method(plan, request, profile)
    if not plan.needs_spending_changes:
        return body
    changes = _change_clauses(plan, profile)
    if not changes:
        return body
    return f"{changes[0].upper()}{changes[1:]}, then {body[0].lower()}{body[1:]}"


def _explain_method(plan, request, profile) -> str:
    if plan.method == "full_payment":
        return _explain_now(request, profile)
    if plan.method == "wait":
        return _explain_wait(request, profile, plan.start_date)
    if plan.method == "partial_payment":
        return _explain_partial(request, profile, plan)
    return _explain_installments(request, profile, plan)


def _explain_partial(request, profile, plan) -> str:
    (_, today), (later_date, later) = plan.payments
    return (
        f"Pay {profile.home_currency} {format_plan_amount(today)} on "
        f"{request.request_date.isoformat()} and the remaining "
        f"{profile.home_currency} {format_plan_amount(later)} on "
        f"{later_date.isoformat()}. This completes the full request and keeps "
        f"{profile.home_currency} "
        f"{format_plan_amount(profile.minimum_balance_to_keep)} protected."
    )


def _explain_installments(request, profile, plan) -> str:
    amount = plan.payments[0][1]
    return (
        f"Use {plan.payment_count} installments of {profile.home_currency} "
        f"{format_plan_amount(amount)}, starting {plan.start_date.isoformat()}. "
        f"This keeps at least {profile.home_currency} "
        f"{format_plan_amount(profile.minimum_balance_to_keep)} available."
    )

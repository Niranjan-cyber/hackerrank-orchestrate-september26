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
  06  the 90-day simulation, amount_safe_to_pay, earliest date   (this change)

Still to come, and deliberately absent here:

  07  candidate plans and lexicographic ranking - this is why the only plan emitted
      below is the one-candidate `wait` case; partial, installments and spending
      changes are not generated yet
  08  spending changes and the pruning rule
  09  reason codes and rendered explanations
  10  evidence authority and conflict precedence
"""

from __future__ import annotations

from dataclasses import replace

from .cash import UnresolvedAmountError, cash_position
from .money import ZERO, format_plan_amount
from .recurrence import projected_effects
from .simulate import amount_safe_to_pay, earliest_date_for_full_payment
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
    projected = projected_effects(
        position,
        dataset.events_by_user.get(request.user_id, ()),
        request,
        profile,
        config,
        dataset.rates,
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

    accepts_full = "full_payment" in profile.payment_methods_considered

    # `affordable_now` needs BOTH the full amount safe today AND full_payment among the
    # user's accepted methods. Sample request_12 proves the second half: it has
    # capacity today but does not accept full payment, so it is affordable_with_plan.
    if safe == request.requested_amount and accepts_full:
        return OutputRow(
            request_id=request.request_id,
            amount_safe_to_pay=safe,
            affordability_status="affordable_now",
            recommended_payment_method="full_payment",
            payment_plan=((request.request_date, request.requested_amount),),
            earliest_date_for_full_payment=request.request_date,
            spending_changes_needed=(),
            decision_explanation=_explain_now(request, profile),
            reasons=tuple(reasons),
        )

    # `wait` is the one safe plan ticket 06 can certify end to end: when the user
    # accepts full payment and the simulator proves it becomes safe later, that date
    # is the plan. It is safe by construction - earliest is the first date the full
    # payment holds the floor.
    if (
        accepts_full
        and earliest is not None
        and earliest <= request.desired_completion_date
    ):
        return OutputRow(
            request_id=request.request_id,
            amount_safe_to_pay=safe,
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            payment_plan=((earliest, request.requested_amount),),
            earliest_date_for_full_payment=earliest,
            spending_changes_needed=(),
            decision_explanation=_explain_wait(request, profile, earliest),
            reasons=tuple(reasons),
        )

    # Fallback until tickets 07/08 generate partial, installment and spending-change
    # plans. No safe, eligible plan is provable yet.
    reasons.append(
        Reason(
            code="NO_SAFE_ELIGIBLE_PLAN",
            detail="no safe plan provable before tickets 07/08 candidates",
        )
    )
    return OutputRow(
        request_id=request.request_id,
        amount_safe_to_pay=safe,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan=(),
        earliest_date_for_full_payment=None,
        spending_changes_needed=(),
        decision_explanation=_explain_not_recommended(request, profile, safe),
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

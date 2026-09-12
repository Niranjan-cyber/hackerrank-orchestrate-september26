"""The deterministic core.

    run_pipeline(dataset, extraction_facts, config) -> tuple[OutputRow, ...]

Pure by construction: it receives already-loaded dataset values and already-validated
facts, and touches no filesystem, no network and no clock. That is what lets the 25
solved samples run with no credential and no API.

TICKET 01 SCOPE - READ THIS BEFORE JUDGING THE LOGIC
The decision rule below is deliberately naive: today's headroom, no forward
projection, no evidence, no plan generation. Ticket 01 exists to prove the whole path
holds the output contract end to end. Real behaviour arrives in tickets 04-10:

  04  cash-state classification and the event lifecycle
  05  recurrence detection and projection
  06  the 90-day simulation, amount_safe_to_pay and earliest_date_for_full_payment
  07  candidate plans and lexicographic ranking
  08  spending changes and the pruning rule
  09  reason codes and rendered explanations
  10  evidence authority and conflict precedence

The seam signature and every output invariant are already final, so those tickets
replace the body of `_decide` without touching anything around it.
"""

from __future__ import annotations

from .money import ZERO, clamp, format_plan_amount
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


def _decide(request, dataset: Dataset, facts: tuple[Fact, ...], config: Config) -> OutputRow:
    profile = dataset.profiles[request.user_id]
    reasons: list[Reason] = []

    # --- TICKET 01 placeholder rule: today's headroom, nothing projected ----------
    headroom = profile.current_available_balance - profile.minimum_balance_to_keep
    safe = clamp(headroom, ZERO, request.requested_amount)

    reasons.append(
        Reason(
            code="OPENING_BALANCE",
            amount=profile.current_available_balance,
            detail="balance as of request_date",
        )
    )
    reasons.append(
        Reason(
            code="MINIMUM_BALANCE_FLOOR",
            amount=profile.minimum_balance_to_keep,
        )
    )
    reasons.append(
        Reason(code="NAIVE_HEADROOM_ONLY", detail="ticket 01: no 90-day projection yet")
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

    # Fallback: nothing eligible and safe is provable with the placeholder rule.
    reasons.append(
        Reason(
            code="NO_SAFE_ELIGIBLE_PLAN",
            detail="placeholder rule found no safe full payment today",
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


def _explain_not_recommended(request, profile, safe) -> str:
    return (
        f"Paying {profile.home_currency} "
        f"{format_plan_amount(request.requested_amount)} is not safe on "
        f"{request.request_date.isoformat()}. At most {profile.home_currency} "
        f"{format_plan_amount(safe)} can be paid while keeping "
        f"{profile.home_currency} {format_plan_amount(profile.minimum_balance_to_keep)} "
        f"available."
    )

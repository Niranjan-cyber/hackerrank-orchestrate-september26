"""Candidate plan generation and the six-level ranking (Ticket 07).

Every request is answered by enumerating *every* way the user could proceed - pay in
full today, pay part today and the rest later, take one of the seller's installment
options, or wait - discarding the ones that are ineligible or unsafe, and then picking
the winner with a single lexicographic sort key.

THREE PROPERTIES THIS MODULE EXISTS TO GUARANTEE

1. **Pruning happens before ranking.** An option the user cannot or will not take is
   never a candidate, so it can never be picked by a tie-break. `candidate_plans`
   returns only plans that passed both eligibility and the ledger's floor test.

2. **The ranking is lexicographic, never a weighted score.** A weighted score can buy
   a cost saving with a missed deadline, which the spec forbids. `rank_key` returns a
   tuple and `best_plan` is `min` over it - there is no arithmetic anywhere in the
   comparison.

3. **No method is preferred by name.** `wait` beats installments on all 250 requests
   because it pays exactly `requested_amount` while all 515 supplied installment
   options carry a financing fee - level 3 decides it. Nothing here knows that `wait`
   is "better" than `installments`; swap in a fee-free option and installments win on
   the earlier start. `test_rank.py` pins both directions.

SAFETY IS THE LEDGER'S JOB, NOT THIS MODULE'S
Every generated candidate is re-simulated with its payments injected as debits, and
kept only if `Ledger.holds_floor`. `amount_safe_to_pay` and
`earliest_date_for_full_payment` are used to *generate* candidates, never to certify
them: the ledger is the sole safety predicate (CONTEXT.md section 7).

SCOPE - WHAT IS DELIBERATELY ABSENT
Spending changes are ticket 08. Every plan this module builds has
`needs_spending_changes = False`; level 2 of the key is already present and load-
bearing, so ticket 08 adds change-variant candidates and changes nothing here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from .cash import CashPosition, UnresolvedAmountError
from .money import ZERO
from .simulate import ProjectionHorizonError, simulate
from .types import Config, PaymentOption, Profile, Reason, Request

# Status for each method. `affordable_now` is reachable only through `full_payment`,
# whose eligibility already carries both halves of the rule: the full amount safe
# today AND `full_payment` among the user's accepted methods (CONTEXT.md section 8).
STATUS_FOR_METHOD = {
    "full_payment": "affordable_now",
    "partial_payment": "affordable_with_plan",
    "installments": "affordable_with_plan",
    "wait": "affordable_later",
}

_TRAILING_NUMBER = re.compile(r"(\d+)$")


@dataclass(frozen=True, slots=True)
class Plan:
    """One safe, eligible way to complete the request. A ranking candidate."""

    request_id: str
    method: str
    status: str
    payments: tuple[tuple[date, Decimal], ...]
    total_paid: Decimal  # fee-inclusive: the option's total_payable_amount
    completes_by_deadline: bool
    needs_spending_changes: bool
    payment_option_id: str | None = None
    spending_changes: tuple[str, ...] = ()
    reasons: tuple[Reason, ...] = field(default_factory=tuple)

    @property
    def start_date(self) -> date:
        """The first payment's date. Every plan has at least one payment."""
        return self.payments[0][0]

    @property
    def payment_count(self) -> int:
        return len(self.payments)


@dataclass(frozen=True, slots=True)
class Candidates:
    """The surviving plans, plus why the others did not survive.

    Pruning provenance is returned rather than discarded because it is often the whole
    explanation: "every installment option needed more payments than you will consider"
    is the real answer for samples 03, 05, 23 and 25.
    """

    plans: tuple[Plan, ...]
    reasons: tuple[Reason, ...] = field(default_factory=tuple)


# --- ranking ----------------------------------------------------------------------


def option_sort_key(payment_option_id: str | None) -> tuple[int, int, str]:
    """Order option ids by their number, not their spelling.

    The supplied ids are zero-padded to two digits but run to `payment_option_790`, so
    a plain string sort ranks `payment_option_100` below `payment_option_33`. Plans
    with no option - full payment, partial payment, wait - sort ahead of every option,
    and an unparseable id sorts behind every option rather than raising: level 6 is a
    tie-break, and a malformed id must not take down the batch.
    """
    if payment_option_id is None:
        return (0, 0, "")
    match = _TRAILING_NUMBER.search(payment_option_id)
    if match is None:
        return (2, 0, payment_option_id)
    return (1, int(match.group(1)), payment_option_id)


def rank_key(plan: Plan) -> tuple:
    """The challenge's priority order as one lexicographic tuple.

    Levels 1-6 are `problem_statement.md:191` verbatim and in order. `request_id` is
    appended so that the order is *total*: two candidates alike in all six levels
    still compare deterministically, which is what makes the output byte-stable.

    Booleans are mapped so that the preferred value is the smaller number, because
    `best_plan` takes the minimum.
    """
    return (
        0 if plan.completes_by_deadline else 1,  # 1. complete by the deadline
        1 if plan.needs_spending_changes else 0,  # 2. no spending changes
        plan.total_paid,  # 3. lowest fee-inclusive total
        plan.start_date,  # 4. start earlier
        plan.payment_count,  # 5. fewer payments
        option_sort_key(plan.payment_option_id),  # 6. lowest payment_option_id
        plan.request_id,  # 7. total order (not in the spec)
    )


def best_plan(plans: Sequence[Plan]) -> Plan | None:
    """The winning candidate, or None when nothing was eligible and safe."""
    if not plans:
        return None
    return min(plans, key=rank_key)


# --- schedules ---------------------------------------------------------------------


def installment_schedule(option: PaymentOption) -> tuple[tuple[date, Decimal], ...]:
    """The option's payment dates: `first_payment_date + k * payment_frequency_days`.

    Verified against `request_02` (3 x 30d), `request_07` (3 x 28d), `request_12`
    (3 x 31d) and `request_22` (3 x 28d). A one-payment option carries no frequency,
    so `k` never leaves zero and the missing value is never read.
    """
    frequency = option.payment_frequency_days or 0
    return tuple(
        (
            option.first_payment_date + timedelta(days=frequency * index),
            option.payment_amount,
        )
        for index in range(option.number_of_payments)
    )


@dataclass(frozen=True, slots=True)
class ScreenedOption:
    """A supplied option that passed every gate except the ledger's floor test."""

    option: PaymentOption
    schedule: tuple[tuple[date, Decimal], ...]
    completes_by_deadline: bool

    @property
    def final_payment_date(self) -> date:
        return self.schedule[-1][0]


def screen_options(
    request: Request,
    profile: Profile,
    options: Sequence[PaymentOption],
    config: Config,
) -> tuple[tuple[ScreenedOption, ...], tuple[Reason, ...]]:
    """Apply every non-ledger eligibility gate to the supplied installment options.

    One function so that the two callers cannot drift: `candidate_plans` certifies
    what comes back, and `required_horizon_end` sizes the projection window around it.
    A gate that lived in only one of them would either drop a candidate the window was
    sized for or size the window for a candidate that is never built.

    Returns the survivors in `payment_option_id` order, plus one `Reason` per prune.
    """
    reasons: list[Reason] = []

    def note(code: str, detail: str = "") -> None:
        reasons.append(Reason(code=code, detail=detail))

    installment_options = tuple(
        option for option in options if option.payment_method == "installments"
    )
    if not installment_options:
        return (), ()
    if "installments" not in profile.payment_methods_considered:
        note("INSTALLMENTS_NOT_AN_ACCEPTED_METHOD")
        return (), tuple(reasons)
    maximum = profile.max_installment_months
    if maximum is None:
        # A blank `max_installment_months` means the user will not consider
        # installments at all (AGENTS.md section 6.1). Across all 275 profiles this is
        # blank exactly when `installments` is absent from the accepted methods, so the
        # two checks agree; both are kept because either column could change.
        note("INSTALLMENTS_NOT_CONSIDERED")
        return (), tuple(reasons)

    screened: list[ScreenedOption] = []
    for option in sorted(
        installment_options, key=lambda o: option_sort_key(o.payment_option_id)
    ):
        if option.number_of_payments < 1:
            # A malformed supplied row. `loaders` parses the count with a bare `int`,
            # so a literal 0 loads cleanly and would reach `schedule[-1]` on an empty
            # tuple. Prune it the way `option_sort_key` handles a malformed id: one
            # bad row must not take down the batch.
            note("OPTION_HAS_NO_PAYMENTS", option.payment_option_id)
            continue
        if option.number_of_payments > maximum:
            note(
                "OPTION_EXCEEDS_MAX_INSTALLMENTS",
                f"{option.payment_option_id} needs {option.number_of_payments} "
                f"payments, the user will consider {maximum}",
            )
            continue
        if option.number_of_payments > 1 and not option.payment_frequency_days:
            # Without a frequency the dates cannot be reconstructed, and guessing one
            # would invent a schedule the seller never offered.
            note("OPTION_SCHEDULE_UNRESOLVED", option.payment_option_id)
            continue

        schedule = installment_schedule(option)
        final = schedule[-1][0]
        completes = final <= request.desired_completion_date
        if not completes and config.installments_must_complete_by_deadline:
            note(
                "OPTION_COMPLETES_AFTER_DEADLINE",
                f"{option.payment_option_id} ends {final.isoformat()}",
            )
            continue
        screened.append(
            ScreenedOption(
                option=option, schedule=schedule, completes_by_deadline=completes
            )
        )

    return tuple(screened), tuple(reasons)


def required_horizon_end(
    request: Request,
    profile: Profile,
    options: Sequence[PaymentOption],
    config: Config,
) -> date | None:
    """The last date any certifiable installment plan reaches, or None if there is none.

    The caller builds the cash position with at least this horizon, so that ticket 05
    has projected recurring spend across every day a candidate touches. Without it
    `simulate` raises `ProjectionHorizonError` and the candidate is silently pruned
    before `Config.installments_must_complete_by_deadline` is ever consulted - which
    would make that knob inert in the one direction it exists for. Under the shipped
    default this returns a date on or before the deadline, so it never extends the
    window: the longest `desired_completion_date` in the dataset is 86 days out.
    """
    screened, _ = screen_options(request, profile, options, config)
    if not screened:
        return None
    return max(entry.final_payment_date for entry in screened)


# --- generation --------------------------------------------------------------------


def candidate_plans(
    request: Request,
    profile: Profile,
    options: Sequence[PaymentOption],
    position: CashPosition,
    config: Config,
    *,
    safe: Decimal,
    earliest: date | None,
) -> Candidates:
    """Every eligible, ledger-certified way to complete this request.

    `safe` and `earliest` are the capacity figures from ticket 06. They are inputs to
    *generation* - they say which candidates are worth building - and are computed once
    per request, before and independently of any payment-method preference. They are
    never used as a safety proof: every candidate below is re-simulated.

    The four generators run in a fixed order and each option is visited in
    `payment_option_id` order, so the candidate tuple is identical run to run.
    """
    accepted = frozenset(profile.payment_methods_considered)
    builder = _CandidateBuilder(request, profile, position, config)

    builder.add_full_payment(accepted, safe)
    builder.add_partial_payment(accepted, safe, earliest)
    builder.add_installments(accepted, options)
    builder.add_wait(accepted, earliest)

    return builder.result()


class _CandidateBuilder:
    """Accumulates candidates and pruning provenance for one request."""

    def __init__(
        self,
        request: Request,
        profile: Profile,
        position: CashPosition,
        config: Config,
    ) -> None:
        self._request = request
        self._profile = profile
        self._position = position
        self._config = config
        self._plans: list[Plan] = []
        self._reasons: list[Reason] = []

    def result(self) -> Candidates:
        return Candidates(plans=tuple(self._plans), reasons=tuple(self._reasons))

    # --- the four generators -------------------------------------------------------

    def add_full_payment(self, accepted: frozenset[str], safe: Decimal) -> None:
        """Pay everything on `request_date`.

        `safe` is already clamped to `requested_amount`, so `safe == requested_amount`
        is exactly "the whole request fits under the headroom available today". Both
        halves of the `affordable_now` rule live here: without `full_payment` among the
        accepted methods no candidate is built, which is what makes `request_12` -
        capacity today, full payment not accepted - resolve to installments instead.
        """
        request = self._request
        if "full_payment" not in accepted:
            return
        if safe != request.requested_amount:
            self._note("NO_FULL_PAYMENT_CAPACITY_TODAY", amount=safe)
            return
        self._certify(
            method="full_payment",
            payments=((request.request_date, request.requested_amount),),
            # Every one of the 275 supplied `full_payment` options carries a zero
            # financing fee and a `total_payable_amount` equal to `requested_amount`,
            # so the principal is the fee-inclusive total here.
            total_paid=request.requested_amount,
            completes_by_deadline=True,
        )

    def add_partial_payment(
        self, accepted: frozenset[str], safe: Decimal, earliest: date | None
    ) -> None:
        """Pay `safe` today and the remainder on `earliest` - exactly two payments.

        All four conditions of `problem_statement.md:146` are gates, not tie-breaks.
        Samples 10, 14 and 24 are the interesting prune: `0 < safe < requested` holds,
        but `earliest` is empty, so no second payment can be scheduled.
        """
        request = self._request
        if "partial_payment" not in accepted or not request.allows_partial_payment:
            return
        if not (ZERO < safe < request.requested_amount):
            return
        if earliest is None:
            self._note("PARTIAL_NEEDS_A_FULL_PAYMENT_DATE")
            return
        if earliest > request.desired_completion_date:
            self._note("PARTIAL_COMPLETES_AFTER_DEADLINE", detail=earliest.isoformat())
            return
        self._certify(
            method="partial_payment",
            payments=(
                (request.request_date, safe),
                (earliest, request.requested_amount - safe),
            ),
            total_paid=request.requested_amount,
            completes_by_deadline=True,
        )

    def add_installments(
        self, accepted: frozenset[str], options: Sequence[PaymentOption]
    ) -> None:
        """Take one of the seller's supplied schedules, unchanged.

        An installment plan may never be invented: it must reproduce a supplied option
        exactly, which is why the schedule comes from `screen_options` and the amount
        from the option rather than from any calculation of ours. `accepted` is read
        inside the screen, from the same `Profile`.
        """
        screened, reasons = screen_options(
            self._request, self._profile, options, self._config
        )
        self._reasons.extend(reasons)
        for entry in screened:
            self._certify(
                method="installments",
                payments=entry.schedule,
                total_paid=entry.option.total_payable_amount,
                completes_by_deadline=entry.completes_by_deadline,
                payment_option_id=entry.option.payment_option_id,
            )

    def add_wait(self, accepted: frozenset[str], earliest: date | None) -> None:
        """Pay the whole amount on the first date it is safe.

        Generated only when that date is *later* than `request_date`: when the full
        amount is already safe today, `wait` and `full_payment` would be the same
        single payment on the same day, and two identical candidates would make the
        ranking's winner depend on generation order.

        Deliberately not gated on the deadline by default - see
        `Config.wait_must_complete_by_deadline`.
        """
        request = self._request
        if "full_payment" not in accepted or earliest is None:
            return
        if earliest <= request.request_date:
            return
        completes = earliest <= request.desired_completion_date
        if not completes and self._config.wait_must_complete_by_deadline:
            self._note("WAIT_COMPLETES_AFTER_DEADLINE", detail=earliest.isoformat())
            return
        self._certify(
            method="wait",
            payments=((earliest, request.requested_amount),),
            total_paid=request.requested_amount,
            completes_by_deadline=completes,
        )

    # --- certification -------------------------------------------------------------

    def _certify(
        self,
        *,
        method: str,
        payments: tuple[tuple[date, Decimal], ...],
        total_paid: Decimal,
        completes_by_deadline: bool,
        payment_option_id: str | None = None,
    ) -> None:
        """Re-simulate the candidate and keep it only if the ledger holds the floor.

        This is the step that makes "pruned before ranking" true rather than a comment.
        """
        label = payment_option_id or method
        try:
            ledger = simulate(self._position, self._config, extra_debits=payments)
        except ProjectionHorizonError:
            # The plan runs past the window ticket 05 projected recurring spend across,
            # so certifying it would mean certifying an unseen stretch of the forecast.
            self._note("PLAN_BEYOND_PROJECTION_HORIZON", detail=label)
            return
        except UnresolvedAmountError:
            # A future outflow is still unpriced; no honest floor test exists.
            self._note("PLAN_FORECAST_UNRESOLVED_AMOUNT", detail=label)
            return

        if not ledger.holds_floor:
            breach = ledger.first_breach
            self._note(
                "PLAN_BREACHES_MINIMUM_BALANCE",
                amount=ledger.minimum_projected_balance,
                detail=(
                    f"{label} dips below the floor"
                    + (f" on {breach.when.isoformat()}" if breach else " at the open")
                ),
            )
            return

        self._plans.append(
            Plan(
                request_id=self._request.request_id,
                method=method,
                status=STATUS_FOR_METHOD[method],
                payments=payments,
                total_paid=total_paid,
                completes_by_deadline=completes_by_deadline,
                # Ticket 08 owns spending changes. Until then a candidate either needs
                # none or is not generated, and level 2 of the key is set explicitly so
                # that adding change-variants changes nothing in this module.
                needs_spending_changes=False,
                payment_option_id=payment_option_id,
                reasons=_plan_reasons(method, total_paid, ledger, payment_option_id),
            )
        )

    def _note(
        self, code: str, *, amount: Decimal | None = None, detail: str = ""
    ) -> None:
        self._reasons.append(Reason(code=code, amount=amount, detail=detail))


def _plan_reasons(
    method: str,
    total_paid: Decimal,
    ledger,
    payment_option_id: str | None,
) -> tuple[Reason, ...]:
    """Provenance carried by a candidate. Ticket 09 renders the winner's entries."""
    reasons: list[Reason] = []
    if payment_option_id is not None:
        reasons.append(
            Reason(code="PLAN_MATCHES_SUPPLIED_OPTION", detail=payment_option_id)
        )
    reasons.append(Reason(code="PLAN_TOTAL_PAYABLE", amount=total_paid, detail=method))
    reasons.append(
        Reason(
            code="PLAN_MINIMUM_PROJECTED_BALANCE",
            amount=ledger.minimum_projected_balance,
            detail=f"lowest balance through {ledger.horizon_end.isoformat()}",
        )
    )
    return tuple(reasons)

"""Candidate generation, eligibility pruning and certification (Ticket 07).

The invariant under test throughout: **a plan that reaches the ranking is already
eligible and already certified safe**. Nothing is ranked and then rejected. Each test
below removes exactly one eligibility condition and asserts the candidate disappears.

The eligibility table is CONTEXT.md section 8, itself derived from
`problem_statement.md:146,189` and from the 25 solved samples.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from engine.cash import cash_position
from engine.pipeline import run_pipeline
from engine.plans import (
    candidate_plans,
    installment_schedule,
    required_horizon_end,
)
from engine.recurrence import with_projections
from engine.simulate import amount_safe_to_pay, earliest_date_for_full_payment, simulate
from engine.types import Config, Dataset

from .support import make_event, make_payment_option, make_profile, make_request


def build_position(request, profile, events, config):
    position = cash_position(request, profile, events, {})
    return with_projections(position, events, request, profile, config, {})


def candidates(request, profile, events=(), options=(), config=None):
    """Build the real cash position, then generate candidates exactly as _decide does."""
    config = config or Config()
    events = tuple(events)
    position = build_position(request, profile, events, config)
    safe = amount_safe_to_pay(position, config, request.requested_amount)
    earliest = earliest_date_for_full_payment(
        position, config, request.requested_amount
    )
    return candidate_plans(
        request, profile, options, position, config, safe=safe, earliest=earliest
    )


def methods(result) -> list[str]:
    return sorted(plan.method for plan in result.plans)


def plan_for(result, method):
    matches = [plan for plan in result.plans if plan.method == method]
    assert len(matches) <= 1, f"{len(matches)} {method} candidates, expected at most 1"
    return matches[0] if matches else None


# A user with capacity today and no events at all, so the ledger is a flat line.
RICH = dict(balance="100000", minimum="2000")

# 8000 on hand against a 2000 floor, a 4000 debit on the 5th and 10000 of salary on
# the 15th: 2000 is safe today, and the full 5000 is safe from the 15th onwards.
SALARY_ON_THE_15TH = (
    dict(
        event_id="one_off_debit",
        status="scheduled",
        direction="debit",
        amount="4000",
        settlement_date="2025-02-05",
    ),
    dict(
        event_id="salary",
        event_type="income",
        status="scheduled",
        direction="credit",
        amount="10000",
        settlement_date="2025-02-15",
    ),
)


def salary_events(*, include_debit: bool = True):
    rows = SALARY_ON_THE_15TH if include_debit else SALARY_ON_THE_15TH[1:]
    return tuple(make_event(**row) for row in rows)


class InstallmentScheduleTest(unittest.TestCase):
    def test_schedule_is_first_payment_date_plus_k_times_frequency(self):
        """Verified against the real request_12 option: 2026-04-19 + k * 31 days."""
        option = make_payment_option(
            payment_amount="22590.19",
            number_of_payments=3,
            first_payment_date="2026-04-19",
            payment_frequency_days=31,
        )
        self.assertEqual(
            installment_schedule(option),
            (
                (date(2026, 4, 19), Decimal("22590.19")),
                (date(2026, 5, 20), Decimal("22590.19")),
                (date(2026, 6, 20), Decimal("22590.19")),
            ),
        )

    def test_a_single_payment_option_needs_no_frequency(self):
        option = make_payment_option(
            payment_method="full_payment",
            payment_amount="500",
            number_of_payments=1,
            first_payment_date="2025-02-01",
            payment_frequency_days=None,
        )
        self.assertEqual(
            installment_schedule(option), ((date(2025, 2, 1), Decimal("500")),)
        )


class InstallmentEligibilityTest(unittest.TestCase):
    """Each test drops one condition from CONTEXT.md section 8 and expects a prune."""

    def setUp(self):
        self.request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-04-10",
        )
        self.option = make_payment_option(
            "payment_option_07",
            payment_amount="1100",
            number_of_payments=3,
            first_payment_date="2025-02-05",
            payment_frequency_days=30,
            financing_fee="300",
        )
        self.profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )

    def test_an_eligible_option_becomes_a_candidate(self):
        result = candidates(self.request, self.profile, options=(self.option,))
        plan = plan_for(result, "installments")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, "affordable_with_plan")
        self.assertEqual(plan.payment_option_id, "payment_option_07")
        self.assertEqual(plan.payment_count, 3)
        self.assertTrue(plan.completes_by_deadline)

    def test_total_paid_is_the_fee_inclusive_total_not_the_principal(self):
        result = candidates(self.request, self.profile, options=(self.option,))
        plan = plan_for(result, "installments")
        self.assertEqual(plan.total_paid, Decimal("3300"))
        self.assertNotEqual(plan.total_paid, self.request.requested_amount)

    def test_installments_not_among_accepted_methods_is_pruned(self):
        profile = make_profile(
            **RICH,
            payment_methods_considered=("full_payment",),
            max_installment_months=6,
        )
        result = candidates(self.request, profile, options=(self.option,))
        self.assertNotIn("installments", methods(result))

    def test_more_payments_than_max_installment_months_is_pruned(self):
        profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=2,
        )
        result = candidates(self.request, profile, options=(self.option,))
        self.assertNotIn("installments", methods(result))

    def test_a_blank_max_installment_months_prunes_every_option(self):
        """A blank value means the user will not consider installments at all."""
        profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=None,
        )
        result = candidates(self.request, profile, options=(self.option,))
        self.assertNotIn("installments", methods(result))

    def test_a_final_payment_after_the_deadline_is_pruned(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-04-05",  # one day before the last payment
        )
        result = candidates(request, self.profile, options=(self.option,))
        self.assertNotIn("installments", methods(result))

    def test_an_option_that_breaches_the_floor_is_pruned(self):
        """Eligible on paper, but the ledger says a payment dips under the minimum."""
        poor = make_profile(
            balance="2500",
            minimum="2000",
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )
        result = candidates(self.request, poor, options=(self.option,))
        self.assertNotIn("installments", methods(result))

    def test_a_full_payment_option_row_never_becomes_an_installment_plan(self):
        option = make_payment_option(
            "payment_option_08",
            payment_method="full_payment",
            payment_amount="3000",
            number_of_payments=1,
            first_payment_date="2025-02-01",
            payment_frequency_days=None,
        )
        result = candidates(self.request, self.profile, options=(option,))
        self.assertNotIn("installments", methods(result))

    def test_pruning_is_recorded_as_a_reason(self):
        profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=2,
        )
        result = candidates(self.request, profile, options=(self.option,))
        codes = {reason.code for reason in result.reasons}
        self.assertIn("OPTION_EXCEEDS_MAX_INSTALLMENTS", codes)


class PartialPaymentEligibilityTest(unittest.TestCase):
    """Partial needs four conditions at once (problem_statement.md:146)."""

    def setUp(self):
        self.request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
            allows_partial_payment=True,
        )
        self.profile = make_profile(
            balance="8000",
            minimum="2000",
            payment_methods_considered=("partial_payment",),
        )
        self.events = salary_events()

    def test_partial_is_two_payments_summing_to_the_requested_amount(self):
        result = candidates(self.request, self.profile, self.events)
        plan = plan_for(result, "partial_payment")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, "affordable_with_plan")
        self.assertEqual(
            plan.payments,
            ((date(2025, 2, 1), Decimal("2000")), (date(2025, 2, 15), Decimal("3000"))),
        )
        self.assertEqual(
            sum(amount for _, amount in plan.payments), self.request.requested_amount
        )
        self.assertEqual(plan.total_paid, self.request.requested_amount)
        self.assertIsNone(plan.payment_option_id)

    def test_a_request_that_disallows_partial_payment_is_pruned(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
            allows_partial_payment=False,
        )
        result = candidates(request, self.profile, self.events)
        self.assertNotIn("partial_payment", methods(result))

    def test_partial_not_among_accepted_methods_is_pruned(self):
        profile = make_profile(
            balance="8000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        result = candidates(self.request, profile, self.events)
        self.assertNotIn("partial_payment", methods(result))

    def test_a_zero_safe_amount_is_pruned(self):
        profile = make_profile(
            balance="2000",
            minimum="2000",
            payment_methods_considered=("partial_payment",),
        )
        result = candidates(self.request, profile, self.events)
        self.assertNotIn("partial_payment", methods(result))

    def test_a_safe_amount_equal_to_the_request_is_pruned(self):
        """0 < safe < requested is strict at both ends: this one is a full payment."""
        profile = make_profile(
            balance="100000",
            minimum="2000",
            payment_methods_considered=("partial_payment", "full_payment"),
        )
        result = candidates(self.request, profile)
        self.assertNotIn("partial_payment", methods(result))

    def test_an_earliest_date_after_the_deadline_is_pruned(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-02-10",  # before the 15th
            allows_partial_payment=True,
        )
        result = candidates(request, self.profile, self.events)
        self.assertNotIn("partial_payment", methods(result))

    def test_a_blank_earliest_date_is_pruned(self):
        """Samples 10, 14 and 24: 0 < safe < requested but earliest never arrives."""
        profile = make_profile(
            balance="3000",
            minimum="2000",
            payment_methods_considered=("partial_payment",),
        )
        result = candidates(self.request, profile)
        self.assertNotIn("partial_payment", methods(result))


class FullPaymentAndWaitTest(unittest.TestCase):
    def test_full_payment_needs_both_capacity_today_and_the_accepted_method(self):
        request = make_request(requested_amount="5000")
        accepted = make_profile(**RICH, payment_methods_considered=("full_payment",))
        plan = plan_for(candidates(request, accepted), "full_payment")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, "affordable_now")
        self.assertEqual(plan.payments, ((request.request_date, Decimal("5000")),))
        self.assertEqual(plan.total_paid, Decimal("5000"))

        not_accepted = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )
        self.assertNotIn("full_payment", methods(candidates(request, not_accepted)))

    def test_full_payment_without_capacity_today_is_pruned(self):
        request = make_request(requested_amount="5000")
        profile = make_profile(
            balance="4000", minimum="2000", payment_methods_considered=("full_payment",)
        )
        self.assertNotIn("full_payment", methods(candidates(request, profile)))

    def test_wait_is_one_payment_on_the_earliest_date_for_the_full_amount(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
        )
        profile = make_profile(
            balance="1000", minimum="1000", payment_methods_considered=("full_payment",)
        )
        plan = plan_for(
            candidates(request, profile, salary_events(include_debit=False)), "wait"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, "affordable_later")
        self.assertEqual(plan.payments, ((date(2025, 2, 15), Decimal("5000")),))
        self.assertTrue(plan.completes_by_deadline)

    def test_wait_needs_full_payment_among_accepted_methods(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
        )
        profile = make_profile(
            balance="1000",
            minimum="1000",
            payment_methods_considered=("partial_payment",),
        )
        result = candidates(request, profile, salary_events(include_debit=False))
        self.assertNotIn("wait", methods(result))

    def test_capacity_today_produces_full_payment_and_not_a_duplicate_wait(self):
        """wait means "not safe today"; emitting both would be two identical plans."""
        request = make_request(requested_amount="5000")
        profile = make_profile(**RICH, payment_methods_considered=("full_payment",))
        self.assertEqual(methods(candidates(request, profile)), ["full_payment"])

    def test_a_wait_past_the_deadline_is_still_a_candidate_but_does_not_complete(self):
        """Level 1 of the ranking is only load-bearing if such a candidate exists.

        This is the samples 06/11/21 shape - `earliest` falls after the deadline - and
        it is what ticket 08's change-variants must beat at level 1. CONTEXT.md
        section 8 gates `partial` and `installments` on the deadline, both on explicit
        instruction, but gates `wait` only on `earliest` being non-empty.
        """
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="1000", minimum="1000", payment_methods_considered=("full_payment",)
        )
        plan = plan_for(
            candidates(request, profile, salary_events(include_debit=False)), "wait"
        )
        self.assertIsNotNone(plan)
        self.assertFalse(plan.completes_by_deadline)

    def test_no_eligible_plan_yields_no_candidates(self):
        request = make_request(requested_amount="5000")
        profile = make_profile(
            balance="2500", minimum="2000", payment_methods_considered=("full_payment",)
        )
        self.assertEqual(candidates(request, profile).plans, ())


class EveryPlanIsCertifiedTest(unittest.TestCase):
    def test_every_generated_plan_holds_the_floor_when_re_simulated(self):
        """The ledger, not the capacity figures, is the safety predicate."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
            allows_partial_payment=True,
        )
        profile = make_profile(
            balance="8000",
            minimum="2000",
            payment_methods_considered=(
                "full_payment",
                "partial_payment",
                "installments",
            ),
            max_installment_months=6,
        )
        events = (
            make_event(
                event_id="rent",
                status="scheduled",
                direction="debit",
                amount="3000",
                settlement_date="2025-02-20",
            ),
            make_event(
                event_id="salary",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="9000",
                settlement_date="2025-03-01",
            ),
        )
        options = (
            make_payment_option(
                "payment_option_01",
                payment_amount="1800",
                number_of_payments=3,
                first_payment_date="2025-02-01",
                payment_frequency_days=30,
                financing_fee="400",
            ),
        )
        config = Config()
        position = build_position(request, profile, events, config)
        result = candidates(request, profile, events, options, config)
        self.assertTrue(result.plans, "expected at least one candidate to certify")
        for plan in result.plans:
            ledger = simulate(position, config, extra_debits=plan.payments)
            self.assertTrue(
                ledger.holds_floor,
                f"{plan.method} was generated but breaches the floor",
            )


class RequiredHorizonTest(unittest.TestCase):
    """The projection horizon must cover the longest candidate, or the knob is inert.

    `simulate` refuses to certify a plan that runs past the window ticket 05 projected
    recurring spend across. Ticket 05 projects to `request_date + horizon_days` by
    default, so without this every long option is dropped as
    `PLAN_BEYOND_PROJECTION_HORIZON` before `installments_must_complete_by_deadline`
    is ever consulted - and the ticket-14 sweep of that knob would measure nothing.
    Of the 434 supplied options that finish after their deadline, 428 also finish
    after day 90.
    """

    def setUp(self):
        self.request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-03-01",
        )
        self.profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )
        # 4 payments, 40 days apart: the last lands 2025-06-15, past both the deadline
        # and the 2025-05-02 default horizon.
        self.long_option = make_payment_option(
            "payment_option_11",
            payment_amount="800",
            number_of_payments=4,
            first_payment_date="2025-02-15",
            payment_frequency_days=40,
        )
        self.late_config = replace(
            Config(), installments_must_complete_by_deadline=False
        )

    def test_it_reports_the_latest_final_payment_across_screened_options(self):
        self.assertEqual(
            required_horizon_end(
                self.request, self.profile, (self.long_option,), self.late_config
            ),
            date(2025, 6, 15),
        )

    def test_an_option_the_gates_already_reject_does_not_extend_the_horizon(self):
        """Only a candidate that could actually be built is worth projecting for."""
        self.assertIsNone(
            required_horizon_end(
                self.request, self.profile, (self.long_option,), Config()
            )
        )
        self.assertIsNone(
            required_horizon_end(self.request, self.profile, (), self.late_config)
        )

    def test_without_the_extended_horizon_the_option_is_dropped_not_ranked(self):
        result = candidates(
            self.request,
            self.profile,
            options=(self.long_option,),
            config=self.late_config,
        )
        self.assertEqual(result.plans, ())
        self.assertIn(
            "PLAN_BEYOND_PROJECTION_HORIZON", {r.code for r in result.reasons}
        )

    def test_with_the_extended_horizon_the_late_option_reaches_the_ranking(self):
        config = self.late_config
        events = ()
        position = cash_position(self.request, self.profile, events, {})
        position = with_projections(
            position,
            events,
            self.request,
            self.profile,
            config,
            {},
            horizon_end=required_horizon_end(
                self.request, self.profile, (self.long_option,), config
            ),
        )
        result = candidate_plans(
            self.request,
            self.profile,
            (self.long_option,),
            position,
            config,
            safe=amount_safe_to_pay(position, config, self.request.requested_amount),
            earliest=earliest_date_for_full_payment(
                position, config, self.request.requested_amount
            ),
        )
        plan = plan_for(result, "installments")
        self.assertIsNotNone(plan)
        # It reaches the ranking and loses at level 1, which is the point of the knob.
        self.assertFalse(plan.completes_by_deadline)

    def test_the_pipeline_extends_the_horizon_for_the_options_it_will_certify(self):
        """End to end: the knob has to be live in `run_pipeline`, not just in `plans`."""
        dataset = Dataset(
            requests=(self.request,),
            profiles={self.profile.user_id: self.profile},
            events_by_user={self.profile.user_id: ()},
            options_by_request={self.request.request_id: (self.long_option,)},
            rates={},
        )
        row = run_pipeline(dataset, (), self.late_config)[0]
        self.assertEqual(row.recommended_payment_method, "installments")
        self.assertEqual(row.payment_plan[-1][0], date(2025, 6, 15))

        # ... and stays off under the shipped default.
        default_row = run_pipeline(dataset, (), Config())[0]
        self.assertEqual(default_row.recommended_payment_method, "not_recommended")


class MalformedOptionTest(unittest.TestCase):
    """A bad row in a supplied CSV must not take the whole batch down.

    `option_sort_key` is already defensive about an unparseable id; a non-positive
    `number_of_payments` deserves the same treatment. `loaders.load_payment_options`
    does a bare `int(...)`, so a literal `0` loads cleanly and would then reach
    `schedule[-1]` on an empty tuple.
    """

    def test_a_non_positive_payment_count_is_pruned_rather_than_raising(self):
        request = make_request(requested_amount="3000")
        profile = make_profile(
            **RICH,
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )
        broken = make_payment_option(
            "payment_option_12",
            payment_amount="1000",
            number_of_payments=0,
            first_payment_date="2025-02-05",
            payment_frequency_days=30,
            total_payable_amount="0",
        )
        result = candidates(request, profile, options=(broken,))
        self.assertNotIn("installments", methods(result))
        self.assertIn("OPTION_HAS_NO_PAYMENTS", {r.code for r in result.reasons})


if __name__ == "__main__":
    unittest.main()

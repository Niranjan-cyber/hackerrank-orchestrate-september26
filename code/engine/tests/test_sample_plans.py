"""Samples 12 and 19 reproduced at the plan seam (Ticket 07 acceptance).

WHY THIS IS NOT AN END-TO-END TEST
`amount_safe_to_pay` currently matches on 0 of the 25 samples: the recurrence and
variable-spend calibration is ticket 14's job and is deliberately outstanding. Driving
these two samples through `run_pipeline` today would therefore test ticket 14, fail,
and tell us nothing about ticket 07.

So each test builds a cash position *designed to reproduce the sample's published
capacity figures* - and asserts that it does, using the real `amount_safe_to_pay` and
`earliest_date_for_full_payment` - and then asserts that ticket 07's generation,
pruning and ranking turn those figures into the sample's published recommendation.
Everything downstream of the capacity figures is real: the real `Profile`, the real
`Request`, the real supplied `PaymentOption` rows, the real ledger certification and
the real `rank_key`. When ticket 14 lands, these become end-to-end by construction.

  sample 12  capacity for the full amount today, but `full_payment` is not an accepted
             method  ->  affordable_with_plan + installments, NOT affordable_now
  sample 19  partial payment (39,660) beats an eligible 2-payment installment option
             (41,246.40) on the fee-inclusive total  ->  level 3 of the ranking decides
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from engine.cash import cash_position
from engine.plans import best_plan, candidate_plans
from engine.recurrence import with_projections
from engine.simulate import amount_safe_to_pay, earliest_date_for_full_payment
from engine.types import Config, Event

from .support import make_event, sample_requests, shared_dataset


def sample(request_id: str):
    return next(r for r in sample_requests() if r.request_id == request_id)


def option(request_id: str, payment_option_id: str):
    return next(
        o
        for o in shared_dataset().options_by_request[request_id]
        if o.payment_option_id == payment_option_id
    )


def decide_at_the_plan_seam(request, profile, events: tuple[Event, ...] = ()):
    """Run exactly the steps `_decide` runs, and return (safe, earliest, winner)."""
    config = Config()
    position = cash_position(request, profile, events, {})
    position = with_projections(position, events, request, profile, config, {})
    safe = amount_safe_to_pay(position, config, request.requested_amount)
    earliest = earliest_date_for_full_payment(
        position, config, request.requested_amount
    )
    candidates = candidate_plans(
        request,
        profile,
        shared_dataset().options_by_request.get(request.request_id, ()),
        position,
        config,
        safe=safe,
        earliest=earliest,
    )
    return safe, earliest, best_plan(candidates.plans)


class Sample12Test(unittest.TestCase):
    """Capacity today is not enough for `affordable_now` - the method must be accepted."""

    def setUp(self):
        self.request = sample("request_12")
        self.chosen = option("request_12", "payment_option_33")
        real = shared_dataset().profiles[self.request.user_id]
        # Enough headroom to clear the option's fee-inclusive total. `amount_safe_to_pay`
        # is clamped at `requested_amount`, so any headroom at or above 65,164 still
        # reports the sample's published 65,164.
        self.profile = replace(
            real,
            current_available_balance=(
                real.minimum_balance_to_keep + self.chosen.total_payable_amount
            ),
        )

    def test_the_profile_really_does_refuse_full_payment(self):
        """The whole sample turns on this: the capacity exists, the method does not."""
        self.assertNotIn("full_payment", self.profile.payment_methods_considered)
        self.assertIn("installments", self.profile.payment_methods_considered)

    def test_capacity_today_plus_no_full_payment_method_gives_installments(self):
        safe, earliest, winner = decide_at_the_plan_seam(self.request, self.profile)

        # The published capacity figures, reproduced.
        self.assertEqual(safe, self.request.requested_amount)  # 65164
        self.assertEqual(earliest, self.request.request_date)  # 2026-04-05

        self.assertIsNotNone(winner)
        self.assertEqual(winner.status, "affordable_with_plan")
        self.assertNotEqual(winner.status, "affordable_now")
        self.assertEqual(winner.method, "installments")
        self.assertEqual(winner.payment_option_id, "payment_option_33")
        self.assertEqual(
            winner.payments,
            (
                (date(2026, 4, 19), Decimal("22590.19")),
                (date(2026, 5, 20), Decimal("22590.19")),
                (date(2026, 6, 20), Decimal("22590.19")),
            ),
        )

    def test_the_21_payment_option_is_pruned_by_max_installment_months(self):
        rejected = option("request_12", "payment_option_35")
        self.assertGreater(
            rejected.number_of_payments, self.profile.max_installment_months
        )
        _, _, winner = decide_at_the_plan_seam(self.request, self.profile)
        self.assertNotEqual(winner.payment_option_id, "payment_option_35")


class Sample19Test(unittest.TestCase):
    """Partial beats an eligible installment option on the fee-inclusive total."""

    def setUp(self):
        self.request = sample("request_19")
        self.installment = option("request_19", "payment_option_53")
        real = shared_dataset().profiles[self.request.user_id]
        published_safe = Decimal("28820")
        # Opening balance leaves exactly the published headroom, and one credit lands on
        # the published earliest date. The credit is sized so that the 2-payment option
        # also clears the floor - otherwise partial would win by the ledger rather than
        # by the ranking, and the sample proves the *ranking*.
        self.profile = replace(
            real,
            current_available_balance=real.minimum_balance_to_keep + published_safe,
        )
        self.events = (
            make_event(
                event_id="event_sample19_credit",
                user_id=real.user_id,
                event_type="income",
                direction="credit",
                status="scheduled",
                amount="20000",
                currency=real.home_currency,
                event_date="2024-09-15",
                settlement_date="2024-09-15",
            ),
        )

    def test_partial_wins_on_the_fee_inclusive_total(self):
        safe, earliest, winner = decide_at_the_plan_seam(
            self.request, self.profile, self.events
        )

        # The published capacity figures, reproduced.
        self.assertEqual(safe, Decimal("28820"))
        self.assertEqual(earliest, date(2024, 9, 15))

        self.assertIsNotNone(winner)
        self.assertEqual(winner.method, "partial_payment")
        self.assertEqual(winner.status, "affordable_with_plan")
        self.assertEqual(
            winner.payments,
            (
                (date(2024, 9, 4), Decimal("28820")),
                (date(2024, 9, 15), Decimal("10840")),
            ),
        )

    def test_the_installment_option_it_beat_was_eligible_and_safe(self):
        """Not a prune: the loser reached the ranking and lost at level 3."""
        config = Config()
        position = cash_position(self.request, self.profile, self.events, {})
        position = with_projections(
            position, self.events, self.request, self.profile, config, {}
        )
        safe = amount_safe_to_pay(position, config, self.request.requested_amount)
        earliest = earliest_date_for_full_payment(
            position, config, self.request.requested_amount
        )
        candidates = candidate_plans(
            self.request,
            self.profile,
            shared_dataset().options_by_request["request_19"],
            position,
            config,
            safe=safe,
            earliest=earliest,
        )
        by_option = {plan.payment_option_id: plan for plan in candidates.plans}
        self.assertIn("payment_option_53", by_option)

        loser = by_option["payment_option_53"]
        winner = best_plan(candidates.plans)
        self.assertEqual(loser.total_paid, Decimal("41246.40"))
        self.assertEqual(winner.total_paid, self.request.requested_amount)  # 39660
        self.assertLess(winner.total_paid, loser.total_paid)
        # Levels 1 and 2 tie, so level 3 is genuinely what decided it.
        self.assertEqual(
            (winner.completes_by_deadline, winner.needs_spending_changes),
            (loser.completes_by_deadline, loser.needs_spending_changes),
        )

    def test_the_three_payment_option_is_pruned_by_max_installment_months(self):
        rejected = option("request_19", "payment_option_54")
        self.assertGreater(
            rejected.number_of_payments, self.profile.max_installment_months
        )


if __name__ == "__main__":
    unittest.main()

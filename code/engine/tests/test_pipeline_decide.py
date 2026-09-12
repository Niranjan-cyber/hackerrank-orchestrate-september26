"""Pipeline-level decision behaviour for `earliest_date_for_full_payment` (finding I1).

The governing contract is `problem_statement.md`:

  * `earliest_date_for_full_payment` is "the earliest date when the full amount is
    forecast to be safe as a single payment" (line 103);
  * it must be left empty only "when the full amount is not expected to become safe
    within the forecast period" (line 113);
  * it "measures financial capacity independently of the user's payment-method
    preferences" and "may equal request_date even when the selected recommendation is
    installments because the user has chosen not to consider full payment" (line 163).

So it is a capacity metric, not a plan field: the pipeline must carry the simulator's
value through every branch, and the validator must not force it blank for
`not_recommended`.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.pipeline import run_pipeline
from engine.types import Config, Dataset

from .support import make_event, make_profile, make_request


def decide(request, profile, events=(), config=None):
    dataset = Dataset(
        requests=(request,),
        profiles={profile.user_id: profile},
        events_by_user={profile.user_id: tuple(events)},
        options_by_request={},
        rates={},
    )
    rows = run_pipeline(dataset, (), config or Config())
    return rows[0]


class EarliestPropagationTest(unittest.TestCase):
    def test_full_payment_safe_later_within_deadline_recommends_wait(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
        )
        profile = make_profile(
            balance="6000", minimum="2000", payment_methods_considered=("full_payment",)
        )
        events = (
            make_event(
                event_id="one_off_debit",
                status="scheduled",
                direction="debit",
                amount="4000",
                settlement_date="2025-02-05",
            ),
            make_event(
                event_id="salary",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="10000",
                settlement_date="2025-02-15",
            ),
        )
        row = decide(request, profile, events)
        self.assertEqual(row.recommended_payment_method, "wait")
        self.assertEqual(row.affordability_status, "affordable_later")
        self.assertEqual(row.earliest_date_for_full_payment, date(2025, 2, 15))

    def test_full_payment_only_safe_after_the_deadline_still_reports_earliest(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="6000", minimum="2000", payment_methods_considered=("full_payment",)
        )
        events = (
            make_event(
                event_id="one_off_debit",
                status="scheduled",
                direction="debit",
                amount="4000",
                settlement_date="2025-02-05",
            ),
            make_event(
                event_id="salary",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="10000",
                settlement_date="2025-02-15",
            ),
        )
        row = decide(request, profile, events)
        # `wait` is ineligible past the deadline, but capacity is not method-dependent.
        self.assertEqual(row.recommended_payment_method, "not_recommended")
        self.assertEqual(row.affordability_status, "not_affordable")
        self.assertEqual(row.earliest_date_for_full_payment, date(2025, 2, 15))

    def test_installments_only_user_reports_earliest_independent_of_method(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
        )
        profile = make_profile(
            balance="10000",
            minimum="2000",
            payment_methods_considered=("installments",),
        )
        row = decide(request, profile)
        # The full amount is safe today, but full_payment is not an accepted method, so
        # the recommendation is not affordable_now; the capacity figure still reports it.
        self.assertEqual(row.affordability_status, "not_affordable")
        self.assertEqual(row.recommended_payment_method, "not_recommended")
        self.assertEqual(row.earliest_date_for_full_payment, request.request_date)

    def test_no_safe_full_payment_within_the_horizon_leaves_earliest_blank(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
        )
        profile = make_profile(
            balance="3000", minimum="2000", payment_methods_considered=("full_payment",)
        )
        row = decide(request, profile)
        self.assertIsNone(row.earliest_date_for_full_payment)
        self.assertEqual(row.affordability_status, "not_affordable")
        self.assertEqual(row.recommended_payment_method, "not_recommended")

    def test_immediate_capacity_with_full_payment_accepted_is_affordable_now(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
        )
        profile = make_profile(
            balance="10000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        row = decide(request, profile)
        self.assertEqual(row.affordability_status, "affordable_now")
        self.assertEqual(row.recommended_payment_method, "full_payment")
        self.assertEqual(row.earliest_date_for_full_payment, request.request_date)


if __name__ == "__main__":
    unittest.main()

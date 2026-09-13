"""Focused primitives: same-day ordering and the 90-day window (Ticket 13).

All three candidate same-day conventions, window boundaries, month-end clamping and
leap years. Assertions are on ledger step order, on `horizon_end`, and on the dates
`monthly_occurrences` returns.

Run: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE.parent / "code", _HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from engine.recurrence import monthly_occurrences  # noqa: E402
from engine.simulate import (  # noqa: E402
    earliest_date_for_full_payment,
    same_day_rank,
    simulate,
)
from engine.types import Config  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_position,
    make_event,
    make_profile,
    make_request,
)

REQUEST_DATE = date(2025, 2, 1)
HORIZON_END = date(2025, 5, 2)  # request_date + 90 inclusive


def same_day_events():
    return (
        make_event(
            event_id="same_day_out",
            status="scheduled",
            direction="debit",
            amount="3000",
            settlement_date="2025-02-05",
        ),
        make_event(
            event_id="same_day_in",
            event_type="income",
            status="scheduled",
            direction="credit",
            amount="3000",
            settlement_date="2025-02-05",
        ),
    )


class SameDayOrderingPrimitivesTest(unittest.TestCase):
    def test_all_three_candidate_conventions_produce_distinct_orders(self):
        kinds = ("dataset_debit", "dataset_credit", "plan_payment")
        orderings = (
            "debits_credits_payment",
            "credits_debits_payment",
            "payment_debits_credits",
        )
        signatures = {
            tuple(sorted(kinds, key=lambda kind: same_day_rank(kind, ordering)))
            for ordering in orderings
        }

        self.assertEqual(len(signatures), 3)

    def test_the_shipped_default_is_debits_before_credits(self):
        ordering = Config().same_day_ordering
        self.assertLess(
            same_day_rank("dataset_debit", ordering),
            same_day_rank("dataset_credit", ordering),
        )
        self.assertLess(
            same_day_rank("dataset_credit", ordering),
            same_day_rank("plan_payment", ordering),
        )

    def _kinds_on_the_fifth(self, ordering):
        position = build_position(
            same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
            config=Config(same_day_ordering=ordering),
        )
        ledger = simulate(
            position,
            Config(same_day_ordering=ordering),
            extra_debits=((date(2025, 2, 5), Decimal("1000")),),
        )
        return [step.kind for step in ledger.steps if step.when == date(2025, 2, 5)]

    def test_each_convention_orders_the_same_day_differently(self):
        self.assertEqual(
            self._kinds_on_the_fifth("debits_credits_payment"),
            ["dataset_debit", "dataset_credit", "plan_payment"],
        )
        self.assertEqual(
            self._kinds_on_the_fifth("credits_debits_payment"),
            ["dataset_credit", "dataset_debit", "plan_payment"],
        )
        self.assertEqual(
            self._kinds_on_the_fifth("payment_debits_credits"),
            ["plan_payment", "dataset_debit", "dataset_credit"],
        )

    def test_the_floor_is_checked_after_each_movement_not_at_day_end(self):
        position = build_position(
            same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
        )
        ledger = simulate(position, Config())

        # End of day is 4000, but the debit lands first and dips to 1000.
        self.assertFalse(ledger.holds_floor)
        self.assertEqual(ledger.minimum_projected_balance, Decimal("1000"))

    def test_credits_first_can_keep_the_same_day_above_the_floor(self):
        position = build_position(
            same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
            config=Config(same_day_ordering="credits_debits_payment"),
        )
        ledger = simulate(position, Config(same_day_ordering="credits_debits_payment"))
        self.assertTrue(ledger.holds_floor)

    def test_an_unknown_convention_is_rejected(self):
        with self.assertRaises(ValueError):
            same_day_rank("dataset_debit", "not-a-convention")


class WindowBoundaryPrimitivesTest(unittest.TestCase):
    def _position(self):
        events = (
            make_event(
                event_id="on_request_date",
                status="scheduled",
                direction="debit",
                amount="100",
                settlement_date=REQUEST_DATE.isoformat(),
            ),
            make_event(
                event_id="on_horizon_end",
                status="scheduled",
                direction="debit",
                amount="200",
                settlement_date=HORIZON_END.isoformat(),
            ),
            make_event(
                event_id="past_horizon_end",
                status="scheduled",
                direction="debit",
                amount="400",
                settlement_date="2025-05-03",
            ),
        )
        return build_position(
            events, profile=make_profile(balance="5000", minimum="2000")
        )

    def test_the_window_is_inclusive_of_day_zero_and_day_ninety(self):
        ledger = simulate(self._position(), Config())
        dates = {step.when for step in ledger.steps}

        self.assertEqual(ledger.horizon_end, HORIZON_END)
        self.assertIn(REQUEST_DATE, dates)
        self.assertIn(HORIZON_END, dates)
        self.assertNotIn(date(2025, 5, 3), dates)

    def test_a_settled_event_on_request_date_is_already_in_the_balance(self):
        """The other half of the day-zero boundary: settled rows are not movements."""
        event = make_event(
            event_id="settled_today",
            status="settled",
            direction="debit",
            amount="100",
            settlement_date=REQUEST_DATE.isoformat(),
        )
        position = build_position(
            (event,), profile=make_profile(balance="5000", minimum="2000")
        )
        ledger = simulate(position, Config())

        self.assertNotIn("settled_today", {step.event_id for step in ledger.steps})
        self.assertEqual(ledger.opening_balance, Decimal("5000"))

    def test_a_full_payment_can_only_land_on_day_ninety_at_the_latest(self):
        events = (
            make_event(
                event_id="day_90_income",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="3000",
                settlement_date=HORIZON_END.isoformat(),
            ),
        )
        position = build_position(
            events, profile=make_profile(balance="3000", minimum="2000")
        )

        self.assertEqual(
            earliest_date_for_full_payment(position, Config(), Decimal("4000")),
            HORIZON_END,
        )
        self.assertIsNone(
            earliest_date_for_full_payment(
                build_position(
                    (
                        make_event(
                            event_id="day_91_income",
                            event_type="income",
                            status="scheduled",
                            direction="credit",
                            amount="3000",
                            settlement_date="2025-05-03",
                        ),
                    ),
                    profile=make_profile(balance="3000", minimum="2000"),
                ),
                Config(),
                Decimal("4000"),
            )
        )


class MonthEndAndLeapYearPrimitivesTest(unittest.TestCase):
    def test_a_day_the_month_does_not_have_clamps_to_month_end(self):
        self.assertEqual(
            monthly_occurrences(31, date(2025, 2, 1), date(2025, 2, 28), "debit"),
            (date(2025, 2, 28),),
        )
        self.assertEqual(
            monthly_occurrences(30, date(2025, 4, 1), date(2025, 4, 30), "debit"),
            (date(2025, 4, 30),),
        )

    def test_a_leap_year_gets_february_29(self):
        self.assertEqual(
            monthly_occurrences(29, date(2024, 2, 1), date(2024, 2, 29), "debit"),
            (date(2024, 2, 29),),
        )

    def test_a_non_leap_year_clamps_february_29_to_the_28th(self):
        self.assertEqual(
            monthly_occurrences(29, date(2025, 2, 1), date(2025, 2, 28), "debit"),
            (date(2025, 2, 28),),
        )

    def test_the_29th_is_present_in_a_leap_year_and_absent_otherwise(self):
        leap = monthly_occurrences(29, date(2024, 1, 1), date(2024, 12, 31), "debit")
        non_leap = monthly_occurrences(
            29, date(2025, 1, 1), date(2025, 12, 31), "debit"
        )

        self.assertEqual([d for d in leap if d.month == 2], [date(2024, 2, 29)])
        self.assertEqual([d for d in non_leap if d.month == 2], [date(2025, 2, 28)])

    def test_the_horizon_spans_a_leap_day(self):
        request = make_request(request_date="2024-01-15", requested_amount="1000")
        ledger = simulate(
            build_position((), request=request, profile=make_profile()),
            Config(),
        )

        self.assertEqual(ledger.horizon_end, date(2024, 4, 14))
        self.assertLess(request.request_date, date(2024, 2, 29))
        self.assertLess(date(2024, 2, 29), ledger.horizon_end)


if __name__ == "__main__":
    unittest.main()

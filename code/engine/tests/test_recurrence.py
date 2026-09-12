"""Recurrence detection and forward projection.

These tests pin the behaviour added in Ticket 05: streams are detected from
settled history, classified as fixed or variable, and projected into the 90-day
forecast window as `CashEffect` values that the simulator (Ticket 06) will apply.

The pipeline still carries the Ticket 01 naive placeholder for `amount_safe_to_pay`,
so these tests assert on the projected effects directly rather than on final rows.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.cash import (
    PROJECTED_CREDIT,
    PROJECTED_DEBIT,
    EXPECTED_CREDIT,
    RESERVED_DEBIT,
    cash_position,
)
from engine.recurrence import (
    _monthly_totals,
    _normalize_description,
    projected_effects,
)
from engine.types import Config

from .support import make_event, make_profile, make_request


REQUEST_DATE = date(2025, 2, 1)
HORIZON_END = date(2025, 5, 2)  # 90 days inclusive


def effects_by_id(effects):
    return {e.event_id: e for e in effects}


class NormalizeDescriptionTest(unittest.TestCase):
    def test_strips_digits_references_dates_and_currency_codes(self):
        self.assertEqual(
            _normalize_description("Rent payment #123 for Jan 2025, Mumbai"),
            "RENT PAYMENT FOR MUMBAI",
        )

    def test_collapses_whitespace(self):
        self.assertEqual(
            _normalize_description("  Electricity   bill  *HOME  "),
            "ELECTRICITY BILL HOME",
        )

    def test_uppercases_and_removes_punctuation(self):
        self.assertEqual(
            _normalize_description("Netflix subscription - premium"),
            "NETFLIX SUBSCRIPTION PREMIUM",
        )


class MonthlyTotalsTest(unittest.TestCase):
    def test_sums_events_by_year_month(self):
        events = (
            make_event(
                event_id="g1",
                category="groceries",
                amount="50",
                settlement_date="2024-12-05",
            ),
            make_event(
                event_id="g2",
                category="groceries",
                amount="70",
                settlement_date="2024-12-20",
            ),
            make_event(
                event_id="g3",
                category="groceries",
                amount="60",
                settlement_date="2025-01-10",
            ),
        )
        totals = _monthly_totals(events)
        self.assertEqual(totals[(2024, 12)], Decimal("120"))
        self.assertEqual(totals[(2025, 1)], Decimal("60"))


class FixedStreamDetectionTest(unittest.TestCase):
    def test_monthly_rent_is_projected_on_its_day_of_month(self):
        events = (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-09-03",
                status="settled",
            ),
            make_event(
                event_id="rent_2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-10-03",
                status="settled",
            ),
            make_event(
                event_id="rent_3",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-11-03",
                status="settled",
            ),
            make_event(
                event_id="rent_4",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-12-03",
                status="settled",
            ),
            make_event(
                event_id="rent_5",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2025-01-03",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertIn(date(2025, 2, 3), dates)
        self.assertIn(date(2025, 3, 3), dates)
        self.assertIn(date(2025, 4, 3), dates)
        # Horizon ends on 2025-05-02, so May 3 is outside the window.
        self.assertNotIn(date(2025, 5, 3), dates)

    def test_projected_amount_is_last_observed_amount(self):
        events = (
            make_event(
                event_id="sub_1",
                category="streaming",
                description="Netflix subscription",
                event_type="subscription",
                amount="11",
                settlement_date="2024-10-15",
                status="settled",
                flexibility="stoppable",
            ),
            make_event(
                event_id="sub_2",
                category="streaming",
                description="Netflix subscription",
                event_type="subscription",
                amount="12",
                settlement_date="2024-11-15",
                status="settled",
                flexibility="stoppable",
            ),
            make_event(
                event_id="sub_3",
                category="streaming",
                description="Netflix subscription",
                event_type="subscription",
                amount="13",
                settlement_date="2024-12-15",
                status="settled",
                flexibility="stoppable",
            ),
            make_event(
                event_id="sub_4",
                category="streaming",
                description="Netflix subscription",
                event_type="subscription",
                amount="14",
                settlement_date="2025-01-15",
                status="settled",
                flexibility="stoppable",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        amounts = [e.amount_home for e in projected if e.state == PROJECTED_DEBIT]
        self.assertTrue(all(a == Decimal("14") for a in amounts), amounts)

    def test_semi_monthly_is_two_monthly_streams(self):
        # Salary on the 1st and 15th of each month, Oct-Jan.
        base_dates = (
            ("2024-10-01", "2024-10-15"),
            ("2024-11-01", "2024-11-15"),
            ("2024-12-01", "2024-12-15"),
            ("2025-01-01", "2025-01-15"),
        )
        events = tuple(
            make_event(
                event_id=f"sal_{d}_{i}",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date=d,
                status="settled",
            )
            for i, (first, mid) in enumerate(base_dates, start=1)
            for d in (first, mid)
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        credit_dates = sorted(
            {e.cash_date for e in projected if e.state == PROJECTED_CREDIT}
        )
        # Feb 1 2025 is Saturday -> rolled backward to Jan 31 (before request_date, excluded).
        # Mar 1 2025 is Saturday -> rolled backward to Feb 28.
        # Feb 15 2025 is Saturday -> rolled backward to Feb 14.
        self.assertIn(date(2025, 2, 14), credit_dates)
        self.assertIn(date(2025, 2, 28), credit_dates)
        self.assertIn(date(2025, 3, 14), credit_dates)
        self.assertIn(date(2025, 4, 1), credit_dates)

    def test_two_occurrences_projects_for_protected_category(self):
        events = (
            make_event(
                event_id="util_1",
                category="utilities",
                description="Electricity bill",
                amount="120",
                settlement_date="2024-12-10",
                status="settled",
            ),
            make_event(
                event_id="util_2",
                category="utilities",
                description="Electricity bill",
                amount="120",
                settlement_date="2025-01-10",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000", protected_categories=("utilities",))
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        self.assertTrue(
            any(e.cash_date == date(2025, 2, 10) for e in projected),
            [e.cash_date for e in projected],
        )

    def test_two_occurrences_ignored_for_non_protected_category(self):
        events = (
            make_event(
                event_id="shop_1",
                category="shopping",
                description="Online shopping",
                amount="200",
                settlement_date="2024-12-10",
                status="settled",
            ),
            make_event(
                event_id="shop_2",
                category="shopping",
                description="Online shopping",
                amount="200",
                settlement_date="2025-01-10",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        self.assertEqual(projected, ())

    def test_different_descriptions_do_not_merge(self):
        events = (
            make_event(
                event_id="r1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-12-03",
                status="settled",
            ),
            make_event(
                event_id="r2",
                category="rent",
                description="Parking fee",
                amount="100",
                settlement_date="2025-01-03",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        self.assertEqual(projected, ())

    def test_amount_tolerance_groups_similar_amounts(self):
        events = tuple(
            make_event(
                event_id=f"rent_{i}",
                category="rent",
                description="Monthly rent",
                amount=str(1000 + i),
                settlement_date=f"2024-{m:02d}-03",
                status="settled",
            )
            for i, m in enumerate((9, 10, 11, 12, 1), start=1)
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        self.assertTrue(
            any(e.cash_date == date(2025, 2, 3) for e in projected),
            [e.cash_date for e in projected],
        )

    def test_amount_outside_tolerance_splits_stream(self):
        events = (
            make_event(
                event_id="r1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-09-03",
                status="settled",
            ),
            make_event(
                event_id="r2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-10-03",
                status="settled",
            ),
            make_event(
                event_id="r3",
                category="rent",
                description="Monthly rent",
                amount="5000",
                settlement_date="2024-11-03",
                status="settled",
            ),
            make_event(
                event_id="r4",
                category="rent",
                description="Monthly rent",
                amount="5000",
                settlement_date="2024-12-03",
                status="settled",
            ),
            make_event(
                event_id="r5",
                category="rent",
                description="Monthly rent",
                amount="5000",
                settlement_date="2025-01-03",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        amounts = {e.amount_home for e in projected if e.state == PROJECTED_DEBIT}
        self.assertEqual(amounts, {Decimal("5000")})


class MonthEndClampTest(unittest.TestCase):
    def test_january_31_clamps_to_february_28_in_non_leap_year(self):
        # Three months with 31 days within the lookback window.
        events = (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-08-31",
                status="settled",
            ),
            make_event(
                event_id="rent_2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-10-31",
                status="settled",
            ),
            make_event(
                event_id="rent_3",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-12-31",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-01-15")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertIn(date(2025, 2, 28), dates)
        self.assertTrue(
            all(d.day <= 28 for d in dates if d.month == 2 and d.year == 2025)
        )

    def test_january_31_clamps_to_february_29_in_leap_year(self):
        events = (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2023-08-31",
                status="settled",
            ),
            make_event(
                event_id="rent_2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2023-10-31",
                status="settled",
            ),
            make_event(
                event_id="rent_3",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2023-12-31",
                status="settled",
            ),
        )
        request = make_request(request_date="2024-01-15")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertIn(date(2024, 2, 29), dates)


class WeekendRollTest(unittest.TestCase):
    def test_expense_rolls_forward_to_next_weekday(self):
        # Rent due on the 1st; Mar 1 2025 is Saturday -> project Monday Mar 3.
        events = (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-11-01",
                status="settled",
            ),
            make_event(
                event_id="rent_2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-12-01",
                status="settled",
            ),
            make_event(
                event_id="rent_3",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2025-01-01",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertIn(date(2025, 3, 3), dates)
        self.assertNotIn(date(2025, 3, 1), dates)

    def test_income_rolls_backward_to_previous_weekday(self):
        # Salary on the 1st; Mar 1 2025 is Saturday -> project Friday Feb 28.
        events = (
            make_event(
                event_id="sal_1",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-11-01",
                status="settled",
            ),
            make_event(
                event_id="sal_2",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-12-01",
                status="settled",
            ),
            make_event(
                event_id="sal_3",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2025-01-01",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_CREDIT]
        self.assertIn(date(2025, 2, 28), dates)
        self.assertNotIn(date(2025, 3, 1), dates)


class ExplicitSuppressionTest(unittest.TestCase):
    def test_scheduled_rent_suppresses_projected_occurrence_on_same_date(self):
        settled = (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-11-03",
                status="settled",
            ),
            make_event(
                event_id="rent_2",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2024-12-03",
                status="settled",
            ),
            make_event(
                event_id="rent_3",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2025-01-03",
                status="settled",
            ),
        )
        scheduled = (
            make_event(
                event_id="rent_scheduled",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date="2025-02-03",
                status="scheduled",
            ),
        )
        events = settled + scheduled
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        projected_dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertNotIn(date(2025, 2, 3), projected_dates)
        self.assertIn(date(2025, 3, 3), projected_dates)


class VariableSpendTest(unittest.TestCase):
    def test_groceries_projected_as_monthly_total(self):
        events = (
            # December 2024 total = 150
            make_event(
                event_id="g1",
                category="groceries",
                amount="50",
                settlement_date="2024-12-05",
                status="settled",
            ),
            make_event(
                event_id="g2",
                category="groceries",
                amount="100",
                settlement_date="2024-12-20",
                status="settled",
            ),
            # January 2025 total = 180
            make_event(
                event_id="g3",
                category="groceries",
                amount="80",
                settlement_date="2025-01-05",
                status="settled",
            ),
            make_event(
                event_id="g4",
                category="groceries",
                amount="100",
                settlement_date="2025-01-20",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        grocery_projected = [e for e in projected if e.category == "groceries"]
        self.assertTrue(grocery_projected)
        # One projected debit per month in the horizon (placed on the earliest observed
        # day, the 5th, so May 5 is outside the horizon ending on May 2).
        months = sorted({e.cash_date.month for e in grocery_projected})
        self.assertEqual(months, [2, 3, 4])

    def test_variable_estimator_selectable(self):
        events = (
            make_event(
                event_id="g0",
                category="groceries",
                amount="300",
                settlement_date="2024-11-05",
                status="settled",
            ),
            make_event(
                event_id="g1",
                category="groceries",
                amount="100",
                settlement_date="2024-12-05",
                status="settled",
            ),
            make_event(
                event_id="g2",
                category="groceries",
                amount="200",
                settlement_date="2025-01-05",
                status="settled",
            ),
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})

        cfg_last = Config(variable_spend_estimator="last_month")
        projected_last = projected_effects(position, events, request, profile, cfg_last)
        amounts_last = [e.amount_home for e in projected_last]
        # Last observed month is 200; projected on the 5th for Feb, Mar, Apr.
        self.assertEqual(amounts_last, [Decimal("200"), Decimal("200"), Decimal("200")])


class IncomeProjectionTest(unittest.TestCase):
    def test_salary_stream_projected_beyond_explicit_row_when_enabled(self):
        settled = (
            make_event(
                event_id="sal_1",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-10-15",
                status="settled",
            ),
            make_event(
                event_id="sal_2",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-11-15",
                status="settled",
            ),
            make_event(
                event_id="sal_3",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-12-15",
                status="settled",
            ),
            make_event(
                event_id="sal_4",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2025-01-15",
                status="settled",
            ),
        )
        scheduled = (
            make_event(
                event_id="sal_confirmed",
                category="salary",
                description="Next confirmed salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2025-02-15",
                status="scheduled",
            ),
        )
        events = settled + scheduled
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})

        cfg_on = Config(project_income_beyond_confirmed=True)
        projected_on = projected_effects(position, events, request, profile, cfg_on)
        credit_dates = sorted(
            e.cash_date for e in projected_on if e.state == PROJECTED_CREDIT
        )
        # March (15th is Saturday -> rolled to 14th) and April projected;
        # February is covered by explicit scheduled row.
        # Horizon ends 2025-05-02, so May 15 is outside the window.
        self.assertIn(date(2025, 3, 14), credit_dates)
        self.assertIn(date(2025, 4, 15), credit_dates)
        self.assertNotIn(date(2025, 2, 15), credit_dates)

    def test_salary_stream_not_projected_beyond_explicit_row_when_disabled(self):
        settled = (
            make_event(
                event_id="sal_1",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-10-15",
                status="settled",
            ),
            make_event(
                event_id="sal_2",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-11-15",
                status="settled",
            ),
            make_event(
                event_id="sal_3",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2024-12-15",
                status="settled",
            ),
            make_event(
                event_id="sal_4",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2025-01-15",
                status="settled",
            ),
        )
        scheduled = (
            make_event(
                event_id="sal_confirmed",
                category="salary",
                description="Next confirmed salary",
                event_type="income",
                direction="credit",
                amount="2000",
                settlement_date="2025-02-15",
                status="scheduled",
            ),
        )
        events = settled + scheduled
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})

        cfg_off = Config(project_income_beyond_confirmed=False)
        projected_off = projected_effects(position, events, request, profile, cfg_off)
        self.assertEqual([e for e in projected_off if e.state == PROJECTED_CREDIT], [])


class HorizonBoundaryTest(unittest.TestCase):
    def test_occurrences_on_request_date_are_excluded(self):
        # Five rent events on the 1st form a monthly stream; Feb 1 == request_date.
        events = tuple(
            make_event(
                event_id=f"rent_{i}",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date=f"2025-01-01",
                status="settled",
            )
            for i in range(5)
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        self.assertFalse(
            any(e.cash_date == date(2025, 2, 1) for e in projected),
            "occurrences on request_date are already in the balance",
        )

    def test_occurrences_up_to_horizon_end_are_included(self):
        # Five rent events on the 2nd form a monthly stream; May 2 == horizon_end.
        events = tuple(
            make_event(
                event_id=f"rent_{i}",
                category="rent",
                description="Monthly rent",
                amount="1000",
                settlement_date=f"2025-01-02",
                status="settled",
            )
            for i in range(5)
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        dates = [e.cash_date for e in projected if e.state == PROJECTED_DEBIT]
        self.assertIn(date(2025, 5, 2), dates)


if __name__ == "__main__":
    unittest.main()

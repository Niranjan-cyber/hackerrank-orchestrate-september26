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
        """The carve-out is for *protected* categories only.

        Pinned at `min_occurrences=3` because the rule is only observable above the
        frozen threshold of 2 (ticket 14), at which two occurrences are a stream in
        every category and `protected_two_occurrence_project` never fires.
        """
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
        projected = projected_effects(
            position, events, request, profile, Config(min_occurrences=3)
        )

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

    def test_variable_monthly_total_is_normalised_to_the_2dp_money_scale(self):
        # Monthly totals 100, 100, 101 -> mean-of-6 is 100.333... (non-terminating).
        # The modelled amount must enter the ledger at the currency scale: this keeps
        # every ledger value terminating so floor arithmetic is exact and reproducible
        # (CONTEXT.md D5 carve-out). Regression for the amount_safe_to_pay boundary bug.
        events = tuple(
            make_event(
                event_id=f"g{index}",
                category="groceries",
                amount=str(amount),
                settlement_date=settled_on,
                status="settled",
            )
            for index, (settled_on, amount) in enumerate(
                (
                    ("2024-11-05", 100),
                    ("2024-12-05", 100),
                    ("2025-01-05", 101),
                )
            )
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())
        amounts = {e.amount_home for e in projected if e.category == "groceries"}
        self.assertTrue(amounts)
        for amount in amounts:
            self.assertEqual(
                amount.as_tuple().exponent,
                -2,
                f"modelled amount {amount} is not on the 2dp money scale",
            )
            self.assertEqual(amount, Decimal("100.33"))

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


class VariableSpendPlacementTest(unittest.TestCase):
    """Where in the month a whole-month variable forecast lands.

    `variable_spend_placement` shapes the `monthly_total` lump, so every case here
    names that shape: ticket 14 froze the default at `individual_events`, under which
    a month is split across its observed days and there is no single lump to place.

    Days 7, 14 and 21 are weekdays in every projected month (Feb-May 2025), so the
    weekend roll is a no-op and the asserted dates are plain calendar facts.
    """

    HISTORY = (("2024-11-07", 7), ("2024-12-14", 14), ("2025-01-21", 21))

    def _projected_dates(self, placement: str):
        events = tuple(
            make_event(
                event_id=f"g{day}",
                category="groceries",
                amount="100",
                settlement_date=settled_on,
                status="settled",
            )
            for settled_on, day in self.HISTORY
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(
            position,
            events,
            request,
            profile,
            Config(
                variable_spend_shape="monthly_total",
                variable_spend_placement=placement,
            ),
        )
        return [e.cash_date for e in projected]

    def test_earliest_observed_day_is_the_default_placement(self):
        self.assertEqual(
            self._projected_dates("earliest"),
            [date(2025, 2, 7), date(2025, 3, 7), date(2025, 4, 7)],
        )
        self.assertEqual(Config().variable_spend_placement, "earliest")

    def test_median_observed_day_places_the_total_mid_month(self):
        self.assertEqual(
            self._projected_dates("median"),
            [date(2025, 2, 14), date(2025, 3, 14), date(2025, 4, 14)],
        )

    def test_latest_observed_day_places_the_total_late(self):
        self.assertEqual(
            self._projected_dates("latest"),
            [date(2025, 2, 21), date(2025, 3, 21), date(2025, 4, 21)],
        )

    def test_an_unknown_placement_is_refused_rather_than_silently_defaulted(self):
        with self.assertRaises(ValueError):
            self._projected_dates("whenever")


class VariableSpendShapeTest(unittest.TestCase):
    """Ticket 14 sweeps one monthly lump against per-day individual events.

    Both shapes forecast the *same* monthly total, so the sweep isolates placement
    within the month rather than confounding it with a different amount. History is
    300 on the 7th and 100 on the 21st for three months: the estimator's monthly
    total is 400, and the day-of-month shares are 900/1200 and 300/1200 of it.
    """

    def _events(self):
        rows = []
        for month, (day7, day21) in (
            ("2024-11", ("300", "100")),
            ("2024-12", ("300", "100")),
            ("2025-01", ("300", "100")),
        ):
            rows.append(
                make_event(
                    event_id=f"g{month}a",
                    category="groceries",
                    amount=day7,
                    settlement_date=f"{month}-07",
                    status="settled",
                )
            )
            rows.append(
                make_event(
                    event_id=f"g{month}b",
                    category="groceries",
                    amount=day21,
                    settlement_date=f"{month}-21",
                    status="settled",
                )
            )
        return tuple(rows)

    def _projected(self, shape: str):
        events = self._events()
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(
            position,
            events,
            request,
            profile,
            Config(variable_spend_shape=shape),
        )
        return [(e.cash_date, e.amount_home) for e in projected]

    def test_monthly_total_shape_places_the_whole_month_on_one_day(self):
        self.assertEqual(
            self._projected("monthly_total"),
            [
                (date(2025, 2, 7), Decimal("400.00")),
                (date(2025, 3, 7), Decimal("400.00")),
                (date(2025, 4, 7), Decimal("400.00")),
            ],
        )

    def test_individual_events_shape_splits_the_same_total_across_observed_days(self):
        self.assertEqual(
            self._projected("individual_events"),
            [
                (date(2025, 2, 7), Decimal("300.00")),
                (date(2025, 2, 21), Decimal("100.00")),
                (date(2025, 3, 7), Decimal("300.00")),
                (date(2025, 3, 21), Decimal("100.00")),
                (date(2025, 4, 7), Decimal("300.00")),
                (date(2025, 4, 21), Decimal("100.00")),
            ],
        )

    def test_both_shapes_forecast_the_same_monthly_total(self):
        lump = sum(amount for _, amount in self._projected("monthly_total"))
        split = sum(amount for _, amount in self._projected("individual_events"))
        self.assertEqual(lump, split)

    def test_an_unknown_shape_is_refused_rather_than_silently_defaulted(self):
        with self.assertRaises(ValueError):
            self._projected("whatever")


class VariableSplitInvariantTest(unittest.TestCase):
    """Splitting a month must never manufacture a credit or lose the total."""

    # Found by randomized search over the real projection path. The earliest day
    # carries a hundredth against months whose totals differ enough that the
    # estimator's monthly total is not the observed sum, so every later day's share
    # rounds up and the remainder left for the 2nd goes below zero.
    NEGATIVE_REMAINDER_HISTORY = {
        10: (
            (2, "0.01"),
            (5, "250.00"),
            (9, "0.02"),
            (17, "1.11"),
            (24, "13.33"),
            (25, "99.99"),
        ),
        11: (
            (2, "0.01"),
            (5, "250.00"),
            (9, "99.99"),
            (17, "0.03"),
            (24, "250.00"),
            (25, "0.02"),
        ),
        12: (
            (2, "0.02"),
            (5, "1.11"),
            (9, "99.99"),
            (17, "0.02"),
            (24, "99.99"),
            (25, "0.03"),
        ),
    }

    def _projected(self, per_month):
        events = tuple(
            make_event(
                event_id=f"g{month}_{day}",
                category="groceries",
                amount=amount,
                settlement_date=f"2024-{month:02d}-{day:02d}",
                status="settled",
            )
            for month, rows in per_month.items()
            for day, amount in rows
        )
        request = make_request(request_date="2025-01-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        return projected_effects(position, events, request, profile, Config())

    def test_no_slot_is_ever_negative(self):
        """A share rounding to nothing must not push the remainder below zero.

        The earliest slot carries the rounding remainder, so a tiny share on that day
        against later days that all round up is the case that can drive it negative -
        and a `PROJECTED_DEBIT` with a negative amount is a phantom credit in the
        ledger, money the user never had.
        """
        projected = self._projected(self.NEGATIVE_REMAINDER_HISTORY)
        self.assertTrue(projected)
        for effect in projected:
            self.assertGreaterEqual(
                effect.amount_home,
                Decimal("0"),
                f"{effect.event_id} projects {effect.amount_home}",
            )

    def test_an_unknown_placement_is_refused_under_the_shipped_shape_too(self):
        """The guard must not be reachable only from the shape that no longer ships."""
        events = tuple(
            make_event(
                event_id=f"g{index}",
                category="groceries",
                amount="100",
                settlement_date=settled_on,
                status="settled",
            )
            for index, settled_on in enumerate(
                ("2024-11-07", "2024-12-07", "2025-01-07")
            )
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        with self.assertRaises(ValueError):
            projected_effects(
                position,
                events,
                request,
                profile,
                Config(
                    variable_spend_shape="individual_events",
                    variable_spend_placement="whenever",
                ),
            )


class ProjectedEventIdTest(unittest.TestCase):
    def test_two_placement_days_landing_on_one_date_keep_distinct_ids(self):
        """Projected ids must stay unique when the calendar collapses two slots.

        Under `individual_events` every slot of a variable stream shares the cited
        `latest_event_id`, and month-end clamping or a weekend roll routinely lands
        two placement days on the same date - days 29, 30 and 31 all clamp to
        2025-02-28. Evidence amendment and the trace ledger address effects by id, so
        a collision makes several distinct occurrences look like one.
        """
        events = tuple(
            make_event(
                event_id=f"g{index}",
                category="groceries",
                amount="100",
                settlement_date=settled_on,
                status="settled",
            )
            for index, settled_on in enumerate(
                (
                    "2024-11-29",
                    "2024-11-30",
                    "2024-12-29",
                    "2024-12-30",
                    "2025-01-29",
                    "2025-01-30",
                )
            )
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        projected = projected_effects(position, events, request, profile, Config())

        february = [e for e in projected if e.cash_date.month == 2]
        self.assertGreater(len(february), 1)
        self.assertEqual(len({e.cash_date for e in february}), 1)
        self.assertEqual(
            len({e.event_id for e in projected}),
            len(projected),
            "projected effect ids collided",
        )


class VariableEstimatorNameTest(unittest.TestCase):
    def test_an_unknown_estimator_is_refused_rather_than_silently_defaulted(self):
        """A sweep that mistypes a name must fail, not quietly score the default."""
        events = tuple(
            make_event(
                event_id=f"g{index}",
                category="groceries",
                amount="100",
                settlement_date=settled_on,
                status="settled",
            )
            for index, settled_on in enumerate(
                ("2024-11-07", "2024-12-07", "2025-01-07")
            )
        )
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000")
        position = cash_position(request, profile, events, {})
        with self.assertRaises(ValueError):
            projected_effects(
                position,
                events,
                request,
                profile,
                Config(variable_spend_estimator="mean12"),
            )


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
                settlement_date="2025-01-01",
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
                settlement_date="2025-01-02",
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

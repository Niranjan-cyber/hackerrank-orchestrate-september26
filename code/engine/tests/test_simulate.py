"""The 90-day simulator: amount_safe_to_pay and earliest_date_for_full_payment.

Ticket 06. The simulator is the only place the same-day ordering rule and the
per-event floor test live. Everything here asserts on the ledger and on the two
derived figures, never on how the module is structured.

Seams under test (all pure, all in `code/engine/simulate.py`):

    simulate(position, config, *, extra_debits=()) -> Ledger
    amount_safe_to_pay(position, config, requested_amount) -> Decimal
    earliest_date_for_full_payment(position, config, requested_amount) -> date | None
    same_day_rank(kind, ordering) -> int

The two derived figures are computed **without** spending changes: the simulator
has no notion of a spending change at all, which is the property being relied on.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.cash import UnresolvedAmountError, cash_position
from engine.recurrence import with_projections
from engine.simulate import (
    amount_safe_to_pay,
    earliest_date_for_full_payment,
    same_day_rank,
    simulate,
)
from engine.types import Config

from .support import make_event, make_profile, make_request, shared_dataset

REQUEST_DATE = date(2025, 2, 1)
HORIZON_END = date(2025, 5, 2)  # request_date + 90 inclusive


def build(
    events, *, request=None, profile=None, config=None, rates=None, horizon_end=None
):
    """A CashPosition with ticket 05's inferred streams merged in, as the pipeline does."""
    request = request or make_request(request_date=REQUEST_DATE.isoformat())
    profile = profile or make_profile()
    config = config or Config()
    position = cash_position(request, profile, events, rates or {})
    position = with_projections(
        position,
        events,
        request,
        profile,
        config,
        rates or {},
        horizon_end=horizon_end,
    )
    return position, request, profile, config


class AmountSafeBoundaryTest(unittest.TestCase):
    """The smallest boundaries, where an off-by-one flips a graded value."""

    def test_current_balance_exactly_at_minimum_is_safe_for_nothing(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="2000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("5000")), Decimal("0")
        )

    def test_amount_that_leaves_exactly_the_minimum_is_safe(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="10000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("8000")), Decimal("8000")
        )

    def test_amount_that_breaches_by_one_unit_is_clamped_back(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="10000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("8001")), Decimal("8000")
        )

    def test_safe_amount_is_capped_at_the_requested_amount(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="10000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("500")), Decimal("500")
        )

    def test_safe_amount_never_goes_negative_when_opening_is_below_the_floor(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="1000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("5000")), Decimal("0")
        )

    def test_opening_below_the_floor_is_reported_as_an_opening_breach(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="1000", minimum="2000")
        )
        ledger = simulate(position, config)
        self.assertTrue(ledger.opening_breached)
        self.assertFalse(ledger.holds_floor)
        # There is no movement to blame, so `first_breach` is empty by design.
        self.assertIsNone(ledger.first_breach)


class ImmediateAndNeverTest(unittest.TestCase):
    def test_immediate_safe_date_is_request_date(self):
        position, request, _, config = build(
            (), profile=make_profile(balance="10000", minimum="2000")
        )
        self.assertEqual(
            earliest_date_for_full_payment(position, config, Decimal("5000")),
            request.request_date,
        )

    def test_no_safe_date_within_90_days_returns_none(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="3000", minimum="2000")
        )
        self.assertIsNone(
            earliest_date_for_full_payment(position, config, Decimal("5000"))
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("5000")), Decimal("1000")
        )


class PendingTreatmentTest(unittest.TestCase):
    def test_pending_debit_is_reserved_and_reduces_the_safe_amount(self):
        events = (
            make_event(
                event_id="pending_out",
                status="pending",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-10",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="5000", minimum="2000")
        )
        # 5000 - 1500 - 2000 = 1500. Ignoring the reserve would report 3000.
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("9000")), Decimal("1500")
        )
        self.assertIsNone(
            earliest_date_for_full_payment(position, config, Decimal("9000"))
        )

    def test_pending_credit_is_never_counted(self):
        events = (
            make_event(
                event_id="pending_in",
                status="pending",
                direction="credit",
                event_type="refund",
                amount="5000",
                settlement_date="2025-02-10",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="3000", minimum="2000")
        )
        # Counting the refund would report a safe amount of 4000 and an earliest date
        # of request_date. Neither is true until the money settles.
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("4000")), Decimal("1000")
        )
        self.assertIsNone(
            earliest_date_for_full_payment(position, config, Decimal("4000"))
        )


class FutureCashTest(unittest.TestCase):
    def test_future_salary_counts_on_its_settlement_date_not_before(self):
        events = (
            make_event(
                event_id="salary",
                category="salary",
                description="Next confirmed salary",
                event_type="income",
                direction="credit",
                amount="3000",
                status="scheduled",
                settlement_date="2025-02-15",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="3000", minimum="2000")
        )
        self.assertEqual(
            earliest_date_for_full_payment(position, config, Decimal("4000")),
            date(2025, 2, 15),
        )
        # The day before the salary lands, the full payment still breaches the floor.
        day_before = simulate(
            position,
            config,
            extra_debits=((date(2025, 2, 14), Decimal("4000")),),
        )
        self.assertFalse(day_before.holds_floor)

    def test_request_becomes_safe_exactly_on_a_projected_income_date(self):
        # Four settled monthly salaries on the 15th -> ticket 05 projects Sep 15.
        settled = tuple(
            make_event(
                event_id=f"sal_{month}",
                category="salary",
                description="Salary",
                event_type="income",
                direction="credit",
                amount="3000",
                status="settled",
                settlement_date=f"2025-{month:02d}-15",
            )
            for month in (5, 6, 7, 8)
        )
        request = make_request(request_date="2025-09-01")
        position, _, _, config = build(
            settled,
            request=request,
            profile=make_profile(balance="3000", minimum="2000"),
        )
        earliest = earliest_date_for_full_payment(position, config, Decimal("4000"))
        self.assertEqual(earliest, date(2025, 9, 15))
        self.assertFalse(
            simulate(
                position,
                config,
                extra_debits=((date(2025, 9, 14), Decimal("4000")),),
            ).holds_floor
        )


class FutureExpenseTest(unittest.TestCase):
    def test_essential_recurring_expense_creates_a_future_breach(self):
        # Four settled rents of 3000 on the 3rd -> projected Feb/Mar/Apr 3.
        settled = tuple(
            make_event(
                event_id=f"rent_{month}",
                category="rent",
                description="Monthly rent",
                amount="3000",
                status="settled",
                settlement_date=f"2024-{month:02d}-03",
            )
            for month in (10, 11, 12)
        ) + (
            make_event(
                event_id="rent_1",
                category="rent",
                description="Monthly rent",
                amount="3000",
                status="settled",
                settlement_date="2025-01-03",
            ),
        )
        position, _, _, config = build(
            settled, profile=make_profile(balance="10000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("5000")), Decimal("0")
        )
        self.assertIsNone(
            earliest_date_for_full_payment(position, config, Decimal("5000"))
        )

    def test_request_becomes_safe_after_a_future_expense_clears(self):
        events = (
            make_event(
                event_id="one_off_expense",
                status="scheduled",
                direction="debit",
                amount="3000",
                settlement_date="2025-02-20",
            ),
            make_event(
                event_id="later_income",
                event_type="income",
                direction="credit",
                amount="4000",
                status="scheduled",
                settlement_date="2025-02-25",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="10000", minimum="2000")
        )
        earliest = earliest_date_for_full_payment(position, config, Decimal("9000"))
        self.assertEqual(earliest, date(2025, 2, 25))
        self.assertGreater(earliest, date(2025, 2, 20))
        # On the expense date the full payment is not yet safe.
        self.assertFalse(
            simulate(
                position,
                config,
                extra_debits=((date(2025, 2, 20), Decimal("9000")),),
            ).holds_floor
        )


class SameDayOrderingTest(unittest.TestCase):
    def test_rank_is_the_single_named_swappable_key(self):
        debit_credit_payment = Config(same_day_ordering="debits_credits_payment")
        self.assertLess(
            same_day_rank("dataset_debit", debit_credit_payment.same_day_ordering),
            same_day_rank("dataset_credit", debit_credit_payment.same_day_ordering),
        )
        self.assertLess(
            same_day_rank("dataset_credit", debit_credit_payment.same_day_ordering),
            same_day_rank("plan_payment", debit_credit_payment.same_day_ordering),
        )

    def test_unknown_ordering_or_kind_raises_value_error(self):
        with self.assertRaises(ValueError):
            same_day_rank("dataset_debit", "not-a-convention")
        with self.assertRaises(ValueError):
            same_day_rank("not-a-kind", "debits_credits_payment")

    def _same_day_events(self):
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
                status="scheduled",
                direction="credit",
                event_type="income",
                amount="3000",
                settlement_date="2025-02-05",
            ),
        )

    def _kinds_on(self, ordering):
        position, _, _, config = build(
            self._same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
            config=Config(same_day_ordering=ordering),
        )
        ledger = simulate(
            position,
            config,
            extra_debits=((date(2025, 2, 5), Decimal("1000")),),
        )
        return [step.kind for step in ledger.steps if step.when == date(2025, 2, 5)]

    def test_each_convention_orders_a_day_differently(self):
        self.assertEqual(
            self._kinds_on("debits_credits_payment"),
            ["dataset_debit", "dataset_credit", "plan_payment"],
        )
        self.assertEqual(
            self._kinds_on("credits_debits_payment"),
            ["dataset_credit", "dataset_debit", "plan_payment"],
        )
        self.assertEqual(
            self._kinds_on("payment_debits_credits"),
            ["plan_payment", "dataset_debit", "dataset_credit"],
        )

    def test_debits_before_credits_is_conservative_enough_to_refuse(self):
        # The same-day debit dips to 1000 before the credit restores 4000, so the
        # dataset itself breaches the floor and no payment date can repair it.
        position, _, _, config = build(
            self._same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
            config=Config(same_day_ordering="debits_credits_payment"),
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("2000")), Decimal("0")
        )
        self.assertIsNone(
            earliest_date_for_full_payment(position, config, Decimal("2000"))
        )

    def test_credits_before_debits_waves_the_same_day_through(self):
        position, _, _, config = build(
            self._same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
            config=Config(same_day_ordering="credits_debits_payment"),
        )
        self.assertEqual(
            earliest_date_for_full_payment(position, config, Decimal("2000")),
            REQUEST_DATE,
        )

    def test_floor_is_checked_after_every_event_not_only_at_day_end(self):
        position, _, _, config = build(
            self._same_day_events(),
            profile=make_profile(balance="4000", minimum="2000"),
        )
        ledger = simulate(position, config)
        # End-of-day balance is 4000, but the debit lands first and dips to 1000.
        self.assertFalse(ledger.holds_floor)
        self.assertEqual(ledger.minimum_projected_balance, Decimal("1000"))
        self.assertEqual(ledger.first_breach.when, date(2025, 2, 5))
        self.assertEqual(ledger.first_breach.event_id, "same_day_out")


class HorizonBoundaryTest(unittest.TestCase):
    def test_events_on_request_date_and_on_the_horizon_end_are_included(self):
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
        position, _, _, config = build(
            events, profile=make_profile(balance="5000", minimum="2000")
        )
        ledger = simulate(position, config)
        dates = {step.when for step in ledger.steps}
        self.assertIn(REQUEST_DATE, dates)
        self.assertIn(HORIZON_END, dates)
        self.assertNotIn(date(2025, 5, 3), dates)

    def test_the_90_day_window_is_inclusive_of_day_90(self):
        position, _, _, config = build((), profile=make_profile())
        self.assertEqual(simulate(position, config).horizon_end, HORIZON_END)

    def test_amount_safe_accounts_for_an_open_event_exactly_on_request_date(self):
        # A pending/scheduled debit dated on request_date has not settled, so it is
        # not inside the opening balance and must be reserved on the day itself.
        events = (
            make_event(
                event_id="same_day_reserve",
                status="pending",
                direction="debit",
                amount="500",
                settlement_date=REQUEST_DATE.isoformat(),
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="5000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("9000")), Decimal("2500")
        )

    def test_earliest_can_land_exactly_on_the_horizon_end(self):
        # Income on day 90 is the first thing that makes a full payment safe. A day
        # later is outside the window and must never be invented.
        events = (
            make_event(
                event_id="day_90_income",
                event_type="income",
                direction="credit",
                amount="3000",
                status="scheduled",
                settlement_date=HORIZON_END.isoformat(),
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="3000", minimum="2000")
        )
        self.assertEqual(
            earliest_date_for_full_payment(position, config, Decimal("4000")),
            HORIZON_END,
        )


class EarliestIndependenceTest(unittest.TestCase):
    def test_earliest_ignores_the_users_payment_method_preferences(self):
        profile = make_profile(
            balance="10000",
            minimum="2000",
            payment_methods_considered=("installments",),
        )
        self.assertNotIn("full_payment", profile.payment_methods_considered)
        position, request, _, config = build((), profile=profile)
        self.assertEqual(
            earliest_date_for_full_payment(position, config, Decimal("5000")),
            request.request_date,
        )


class PlanInjectionTest(unittest.TestCase):
    def test_a_plan_payments_are_injected_and_rechecked(self):
        position, _, _, config = build(
            (), profile=make_profile(balance="5000", minimum="2000")
        )
        safe_plan = simulate(
            position,
            config,
            extra_debits=(
                (REQUEST_DATE, Decimal("1000")),
                (REQUEST_DATE, Decimal("1500")),
            ),
        )
        self.assertTrue(safe_plan.holds_floor)
        self.assertEqual(safe_plan.minimum_projected_balance, Decimal("2500"))

        unsafe_plan = simulate(
            position, config, extra_debits=((REQUEST_DATE, Decimal("4000")),)
        )
        self.assertFalse(unsafe_plan.holds_floor)

    def test_plan_payments_beyond_the_base_horizon_extend_the_recheck(self):
        # The position must be projected across the extended window first, or the
        # simulator refuses (see test_simulate_horizon.py).
        position, _, _, config = build(
            (),
            profile=make_profile(balance="5000", minimum="2000"),
            horizon_end=date(2025, 5, 10),
        )
        # A payment after day 90 must still be re-checked, not silently ignored.
        ledger = simulate(
            position,
            config,
            extra_debits=((date(2025, 5, 10), Decimal("4000")),),
        )
        self.assertGreater(ledger.horizon_end, HORIZON_END)
        self.assertFalse(ledger.holds_floor)


class UnresolvedAmountTest(unittest.TestCase):
    def test_an_unresolved_future_outflow_inside_the_window_fails_loudly(self):
        events = (
            make_event(
                event_id="blank_out",
                amount=None,
                status="pending",
                direction="debit",
                settlement_date="2025-02-20",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="5000", minimum="2000")
        )
        with self.assertRaises(UnresolvedAmountError) as caught:
            amount_safe_to_pay(position, config, Decimal("1000"))
        self.assertIn("blank_out", str(caught.exception))

    def test_an_unresolved_outflow_after_the_window_does_not_block(self):
        events = (
            make_event(
                event_id="blank_far_out",
                amount=None,
                status="pending",
                direction="debit",
                settlement_date="2025-12-20",
            ),
        )
        position, _, _, config = build(
            events, profile=make_profile(balance="5000", minimum="2000")
        )
        self.assertEqual(
            amount_safe_to_pay(position, config, Decimal("1000")), Decimal("1000")
        )


class SafeAmountInjectionPropertyTest(unittest.TestCase):
    """The core invariant: the amount the simulator calls safe is actually safe.

    Regression for the Decimal-context boundary bug found in review. `amount_safe_to_pay`
    is derived from one ledger; re-injecting it as a payment builds a second ledger whose
    cumulative sums can round differently. The fix removes non-terminating modelled
    amounts, so the invariant holds *exactly* - never via an epsilon.

    request_47 is the named counterexample: before the fix, injecting its safe amount
    left the minimum a hair (3e-17) below the floor.
    """

    @staticmethod
    def _position_for(dataset, request, config):
        """The same merge the pipeline performs: explicit rows plus inferred streams."""
        profile = dataset.profiles[request.user_id]
        events = dataset.events_by_user.get(request.user_id, ())
        position = cash_position(request, profile, events, dataset.rates)
        return with_projections(
            position, events, request, profile, config, dataset.rates
        )

    def test_request_47_boundary_reinjection_holds_the_floor(self):
        dataset = shared_dataset()
        config = Config()
        request = next(r for r in dataset.requests if r.request_id == "request_47")
        position = self._position_for(dataset, request, config)
        safe = amount_safe_to_pay(position, config, request.requested_amount)
        ledger = simulate(
            position, config, extra_debits=((request.request_date, safe),)
        )
        self.assertTrue(
            ledger.holds_floor,
            f"injecting safe={safe} left {ledger.minimum_projected_balance} "
            f"below floor {position.minimum_balance}",
        )

    def test_injecting_the_safe_amount_never_breaches_the_floor_for_every_request(self):
        dataset = shared_dataset()
        config = Config()
        checked = 0
        for request in dataset.requests:
            position = self._position_for(dataset, request, config)
            try:
                safe = amount_safe_to_pay(position, config, request.requested_amount)
            except UnresolvedAmountError:
                continue
            if safe <= Decimal("0"):
                continue
            ledger = simulate(
                position, config, extra_debits=((request.request_date, safe),)
            )
            self.assertTrue(
                ledger.holds_floor,
                f"{request.request_id}: injecting safe={safe} left "
                f"{ledger.minimum_projected_balance} below floor "
                f"{position.minimum_balance}",
            )
            checked += 1
        # ~194 of 250 are checkable: the rest have safe == 0 or an unresolved outflow.
        self.assertGreater(checked, 150, "expected a substantial share to be checkable")


if __name__ == "__main__":
    unittest.main()

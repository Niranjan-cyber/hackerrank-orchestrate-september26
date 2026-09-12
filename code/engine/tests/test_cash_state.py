"""Cash-state classification: what each event row does to available cash, and when.

The governing discovery, verified against all 58 linked rows in the dataset: the
`linked_event_id` link never decides cash treatment. `status` and `direction` decide
it, alone. The seven lifecycle patterns are therefore *consequences* of the rule, not
cases in it - so they are asserted in `test_lifecycle_real_data.py` as regression
evidence rather than branched on in `cash.py`. See CONTEXT.md section 5.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.cash import (
    EXCLUDED,
    EXPECTED_CREDIT,
    IN_OPENING_BALANCE,
    RESERVED_DEBIT,
    UNKNOWN_AMOUNT,
    UnresolvedAmountError,
    cash_position,
    classify_event,
)

from .support import make_event, make_profile, make_request

REQUEST_DATE = date(2025, 2, 1)


class ClassifyMixin:
    def effect_of(self, event, *, request_date=REQUEST_DATE, home="INR", rates=None):
        return classify_event(
            event, request_date=request_date, home_currency=home, rates=rates or {}
        )

    def state_of(self, event, **kwargs):
        return self.effect_of(event, **kwargs).state


class OpeningBalanceTest(ClassifyMixin, unittest.TestCase):
    """Settled history is already inside `current_available_balance`."""

    def test_settled_row_before_request_date_is_already_in_the_balance(self):
        event = make_event(settlement_date="2025-01-31", status="settled")
        self.assertEqual(self.state_of(event), IN_OPENING_BALANCE)

    def test_a_row_settling_exactly_on_request_date_is_in_the_balance(self):
        # The boundary is inclusive: current_available_balance is the balance *as of*
        # request_date, so same-day settled cash is already inside it. Reserving it
        # too would double-count the outflow.
        event = make_event(settlement_date="2025-02-01", status="settled")
        self.assertEqual(self.state_of(event), IN_OPENING_BALANCE)

    def test_settled_rows_carry_no_forecast_amount_even_when_the_amount_is_blank(self):
        # 12 of the 16 blank-amount rows are settled history. Their value is already
        # in the balance, so a blank there is harmless and must not raise.
        event = make_event(amount=None, status="settled", settlement_date="2025-01-05")
        effect = self.effect_of(event)
        self.assertEqual(effect.state, IN_OPENING_BALANCE)
        self.assertIsNone(effect.amount_home)


class PendingTest(ClassifyMixin, unittest.TestCase):
    def test_pending_debit_is_reserved(self):
        event = make_event(
            status="pending", direction="debit", settlement_date="2025-02-20"
        )
        effect = self.effect_of(event)
        self.assertEqual(effect.state, RESERVED_DEBIT)
        self.assertEqual(effect.amount_home, Decimal("100"))
        self.assertEqual(effect.cash_date, date(2025, 2, 20))

    def test_pending_credit_is_never_counted(self):
        event = make_event(
            status="pending",
            direction="credit",
            event_type="refund",
            settlement_date="2025-02-20",
        )
        self.assertEqual(self.state_of(event), EXCLUDED)

    def test_a_pending_debit_marked_a_possible_duplicate_is_still_reserved(self):
        # The trap. Every one of the six such rows carries a bank message stating the
        # dispute is open and NO reversal has been posted - so the cash is genuinely
        # out of the account. Excluding it would overstate headroom. (D9.)
        event = make_event(
            status="pending",
            direction="debit",
            description="Possible duplicate card charge",
            settlement_date="2025-02-20",
            linked_event_id="event_parent",
        )
        self.assertEqual(self.state_of(event), RESERVED_DEBIT)


class ScheduledTest(ClassifyMixin, unittest.TestCase):
    def test_scheduled_debit_is_reserved(self):
        event = make_event(
            status="scheduled", direction="debit", settlement_date="2025-03-01"
        )
        self.assertEqual(self.state_of(event), RESERVED_DEBIT)

    def test_scheduled_credit_counts_as_expected_income(self):
        event = make_event(
            status="scheduled",
            direction="credit",
            event_type="income",
            description="Next confirmed salary",
            settlement_date="2025-02-15",
        )
        effect = self.effect_of(event)
        self.assertEqual(effect.state, EXPECTED_CREDIT)
        self.assertEqual(effect.amount_home, Decimal("100"))

    def test_a_scheduled_non_income_credit_is_not_counted_until_it_settles(self):
        # problem_statement.md:178 - refunds, bonuses, commissions and investment
        # gains are not counted until they settle. Only a confirmed salary is counted
        # while merely scheduled. (No such row exists in the supplied dataset, so this
        # pins the contract rather than a current data shape.)
        for event_type in ("refund", "investment_sale"):
            with self.subTest(event_type=event_type):
                event = make_event(
                    status="scheduled",
                    direction="credit",
                    event_type=event_type,
                    settlement_date="2025-02-20",
                )
                self.assertEqual(self.state_of(event), EXCLUDED)

    def test_a_settled_non_income_credit_counts(self):
        event = make_event(
            status="settled",
            direction="credit",
            event_type="investment_sale",
            settlement_date="2025-02-20",
        )
        self.assertEqual(self.state_of(event), EXPECTED_CREDIT)


class NeverMovesCashTest(ClassifyMixin, unittest.TestCase):
    def test_cancelled_row_never_affects_cash(self):
        self.assertEqual(self.state_of(make_event(status="cancelled")), EXCLUDED)

    def test_failed_row_never_affects_cash(self):
        event = make_event(status="failed", event_type="debt_payment")
        self.assertEqual(self.state_of(event), EXCLUDED)

    def test_unrealized_non_cash_valuation_is_never_available_cash(self):
        event = make_event(
            status="unrealized",
            direction="non_cash",
            event_type="investment_valuation",
            amount="500000",
        )
        self.assertEqual(self.state_of(event), EXCLUDED)

    def test_a_settled_investment_sale_counts_but_a_valuation_does_not(self):
        sale = make_event(
            event_id="event_sale",
            status="settled",
            direction="credit",
            event_type="investment_sale",
            settlement_date="2025-01-20",
        )
        valuation = make_event(
            event_id="event_val",
            status="unrealized",
            direction="non_cash",
            event_type="investment_valuation",
        )
        self.assertEqual(self.state_of(sale), IN_OPENING_BALANCE)
        self.assertEqual(self.state_of(valuation), EXCLUDED)


class BlankAmountTest(ClassifyMixin, unittest.TestCase):
    """A blank amount must never become zero - resolve it, or fail loudly."""

    def test_a_future_debit_with_a_blank_amount_is_flagged_not_silently_zero(self):
        event = make_event(
            amount=None,
            status="pending",
            direction="debit",
            settlement_date="2025-02-20",
        )
        effect = self.effect_of(event)
        self.assertEqual(effect.state, UNKNOWN_AMOUNT)
        self.assertIsNone(effect.amount_home)

    def test_asking_an_unresolved_effect_for_a_number_raises(self):
        # There is no honest value: contributing zero would report a balance too high
        # by exactly the unresolved charge, which is the direction that wrongly
        # certifies a payment as safe.
        event = make_event(
            amount=None,
            status="pending",
            direction="debit",
            settlement_date="2025-02-20",
        )
        effect = self.effect_of(event)
        with self.assertRaises(UnresolvedAmountError) as caught:
            effect.signed_amount
        self.assertIn(event.event_id, str(caught.exception))

    def test_a_supplied_amount_override_resolves_a_blank_future_debit(self):
        # The seam evidence extraction fills: cash.py never parses a message or an
        # image, it just accepts already-resolved amounts keyed by event_id.
        event = make_event(
            event_id="event_1786",
            amount=None,
            status="pending",
            direction="debit",
            settlement_date="2025-02-20",
        )
        effect = classify_event(
            event,
            request_date=REQUEST_DATE,
            home_currency="INR",
            rates={},
            amount_overrides={"event_1786": Decimal("4500")},
        )
        self.assertEqual(effect.state, RESERVED_DEBIT)
        self.assertEqual(effect.amount_home, Decimal("4500"))


class ForeignCurrencyTest(ClassifyMixin, unittest.TestCase):
    def test_a_foreign_future_credit_is_converted_into_home_currency(self):
        event = make_event(
            status="scheduled",
            direction="credit",
            event_type="income",
            amount="1000",
            currency="USD",
            settlement_date="2025-02-15",
        )
        effect = self.effect_of(
            event, rates={("2025-02-15", "USD", "INR"): Decimal("84.50")}
        )
        self.assertEqual(effect.amount_home, Decimal("84500.00"))


class StaleOpenRowTest(ClassifyMixin, unittest.TestCase):
    """Rows read in the safer direction when their date has already passed.

    The dataset never exercises these branches - see
    `test_closed_statuses_are_past_and_open_statuses_are_future` - but an unhandled
    stale row would be a silent mis-forecast, so the behaviour is pinned.
    """

    def test_a_pending_debit_whose_date_has_passed_is_reserved_immediately(self):
        event = make_event(
            status="pending", direction="debit", settlement_date="2025-01-15"
        )
        effect = self.effect_of(event)
        self.assertEqual(effect.state, RESERVED_DEBIT)
        self.assertEqual(
            effect.cash_date, REQUEST_DATE, "reserve now, not on a date already gone"
        )

    def test_a_scheduled_credit_whose_date_has_passed_is_not_counted(self):
        # It was due and has not settled. Counting it would assume income that
        # visibly failed to arrive.
        event = make_event(
            status="scheduled",
            direction="credit",
            event_type="income",
            settlement_date="2025-01-15",
        )
        self.assertEqual(self.state_of(event), EXCLUDED)


class CashPositionTest(unittest.TestCase):
    def test_position_separates_reserves_from_expected_credits(self):
        events = (
            make_event(
                event_id="event_a", status="settled", settlement_date="2025-01-05"
            ),
            make_event(
                event_id="event_b",
                status="pending",
                direction="debit",
                amount="300",
                settlement_date="2025-02-10",
            ),
            make_event(
                event_id="event_c",
                status="scheduled",
                direction="credit",
                event_type="income",
                amount="7000",
                settlement_date="2025-02-15",
            ),
            make_event(event_id="event_d", status="cancelled", amount="999"),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"),
            make_profile(balance="10000", minimum="2000"),
            events,
            {},
        )

        self.assertEqual(position.opening_balance, Decimal("10000"))
        self.assertEqual(position.minimum_balance, Decimal("2000"))
        self.assertEqual([e.event_id for e in position.reserved_debits], ["event_b"])
        self.assertEqual([e.event_id for e in position.expected_credits], ["event_c"])
        self.assertEqual(position.unknown_amounts, ())

    def test_every_event_is_classified_exactly_once(self):
        events = tuple(
            make_event(event_id=f"event_{i}", status=status, direction=direction)
            for i, (status, direction) in enumerate(
                [
                    ("settled", "debit"),
                    ("pending", "debit"),
                    ("pending", "credit"),
                    ("scheduled", "credit"),
                    ("cancelled", "debit"),
                    ("failed", "debit"),
                ]
            )
        )
        position = cash_position(make_request(), make_profile(), events, {})
        self.assertEqual(len(position.effects), len(events))
        self.assertEqual(
            {e.event_id for e in position.effects}, {e.event_id for e in events}
        )

    def test_a_future_debit_and_credit_of_equal_size_net_to_zero(self):
        """The netting property, pinned on *future* legs so it is not vacuous.

        Both legs must actually reach the forecast for this to mean anything: a pair
        of settled pre-request legs nets to zero whatever their amounts, because
        neither contributes at all. So this uses a scheduled debit and a scheduled
        credit, and deliberately asserts the balance is unmoved rather than merely
        that the sum is zero.
        """
        events = (
            make_event(
                event_id="event_out",
                status="scheduled",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-10",
            ),
            make_event(
                event_id="event_in",
                status="scheduled",
                direction="credit",
                event_type="income",
                amount="1500",
                settlement_date="2025-02-12",
            ),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"),
            make_profile(balance="10000"),
            events,
            {},
        )
        self.assertEqual(position.net_forecast_change, Decimal("0"))
        self.assertEqual(position.balance_on(date(2025, 3, 1)), Decimal("10000"))
        # ...and the legs are genuinely in the forecast, not excluded.
        self.assertEqual(len(position.reserved_debits), 1)
        self.assertEqual(len(position.expected_credits), 1)

    def test_unequal_future_legs_do_not_net_to_zero(self):
        """Guards the test above against passing for the wrong reason."""
        events = (
            make_event(
                event_id="event_out",
                status="scheduled",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-10",
            ),
            make_event(
                event_id="event_in",
                status="scheduled",
                direction="credit",
                event_type="income",
                amount="999",
                settlement_date="2025-02-12",
            ),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"), make_profile(), events, {}
        )
        self.assertEqual(position.net_forecast_change, Decimal("-501"))

    def test_a_settled_pair_is_inside_the_opening_balance_and_moves_nothing(self):
        # The real-data shape: all 14 settled expense/refund pairs have equal legs and
        # both land on or before request_date, so the pair nets to zero with no code.
        events = (
            make_event(
                event_id="event_out",
                direction="debit",
                amount="1500",
                settlement_date="2025-01-10",
            ),
            make_event(
                event_id="event_in",
                direction="credit",
                amount="1500",
                event_type="refund",
                settlement_date="2025-01-12",
                linked_event_id="event_out",
            ),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"), make_profile(), events, {}
        )
        self.assertEqual(position.net_forecast_change, Decimal("0"))
        self.assertTrue(all(e.state == IN_OPENING_BALANCE for e in position.effects))

    def test_a_total_refuses_to_compute_while_an_outflow_is_unpriced(self):
        events = (
            make_event(
                event_id="event_blank",
                amount=None,
                status="pending",
                direction="debit",
                settlement_date="2025-02-20",
            ),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"), make_profile(), events, {}
        )
        self.assertEqual(len(position.unknown_amounts), 1)
        with self.assertRaises(UnresolvedAmountError):
            position.balance_on(date(2025, 3, 1))
        with self.assertRaises(UnresolvedAmountError):
            position.net_forecast_change

    def test_an_unpriced_outflow_after_the_cutoff_does_not_block_an_earlier_total(self):
        # It cannot affect a balance dated before it, so refusing there would be
        # over-strict and would block ticket 06 needlessly.
        events = (
            make_event(
                event_id="event_blank",
                amount=None,
                status="pending",
                direction="debit",
                settlement_date="2025-03-20",
            ),
        )
        position = cash_position(
            make_request(request_date="2025-02-01"),
            make_profile(balance="10000"),
            events,
            {},
        )
        self.assertEqual(position.balance_on(date(2025, 2, 28)), Decimal("10000"))


if __name__ == "__main__":
    unittest.main()

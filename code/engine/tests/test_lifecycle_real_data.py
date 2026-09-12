"""The seven lifecycle patterns and the structural invariants, on real data.

`test_cash_state.py` pins the *rule* on hand-built rows. This module pins the rule's
behaviour on the actual 25,342 event rows, so a wrong reading of the dataset fails
here even if the unit tests still pass.

Every count below was measured from `dataset/financial_events.csv`, not assumed.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from functools import lru_cache

from engine.cash import (
    CLOSED_STATUSES,
    EXCLUDED,
    EXPECTED_CREDIT,
    IN_OPENING_BALANCE,
    OPEN_STATUSES,
    RESERVED_DEBIT,
    UNKNOWN_AMOUNT,
    classify_event,
)

from .support import all_events, events_by_id, request_date_by_user, shared_dataset


@lru_cache(maxsize=1)
def effects_by_event_id():
    """event_id -> CashEffect for every row in the dataset."""
    dataset = shared_dataset()
    dates = request_date_by_user()
    return {
        event.event_id: classify_event(
            event,
            request_date=dates[event.user_id],
            home_currency=dataset.profiles[event.user_id].home_currency,
            rates=dataset.rates,
        )
        for event in all_events()
    }


def linked_pairs():
    by_id = events_by_id()
    for child in all_events():
        if child.linked_event_id:
            parent = by_id.get(child.linked_event_id)
            if parent is not None:
                yield parent, child


def shape(event):
    return f"{event.status}/{event.direction}/{event.event_type}"


class WholeDatasetTest(unittest.TestCase):
    def test_every_row_in_the_dataset_classifies_without_error(self):
        self.assertEqual(len(all_events()), 25342)
        self.assertEqual(len(effects_by_event_id()), 25342)

    def test_closed_statuses_are_past_and_open_statuses_are_future(self):
        """Closed rows all land on or before request_date; open rows strictly after.

        This is why classifying by status alone is *sufficient* rather than merely
        convenient: the status split and the date split coincide exactly. If a future
        dataset breaks this, the stale-row branches in `cash.py` start firing and this
        test is the warning.
        """
        dates = request_date_by_user()
        for event in all_events():
            request_date = dates[event.user_id]
            if event.status in CLOSED_STATUSES:
                self.assertLessEqual(event.cash_date, request_date, event.event_id)
            else:
                self.assertIn(event.status, OPEN_STATUSES, event.event_id)
                self.assertGreater(event.cash_date, request_date, event.event_id)

    def test_the_expected_open_row_counts_are_present(self):
        counts = {}
        for event in all_events():
            if event.status in OPEN_STATUSES:
                key = (event.status, event.direction)
                counts[key] = counts.get(key, 0) + 1
        self.assertEqual(
            counts,
            {
                ("pending", "credit"): 8,
                ("pending", "debit"): 63,
                ("scheduled", "credit"): 47,
                ("scheduled", "debit"): 23,
            },
        )


# (parent shape, child shape) -> (count, parent state, child state)
EXPECTED_PATTERNS = {
    ("settled/debit/expense", "settled/credit/refund"): (
        14,
        IN_OPENING_BALANCE,
        IN_OPENING_BALANCE,
    ),
    ("settled/debit/investment_purchase", "unrealized/non_cash/investment_valuation"): (
        10,
        IN_OPENING_BALANCE,
        EXCLUDED,
    ),
    ("cancelled/debit/expense", "settled/debit/expense"): (
        8,
        EXCLUDED,
        IN_OPENING_BALANCE,
    ),
    ("settled/debit/expense", "pending/credit/refund"): (8, IN_OPENING_BALANCE, EXCLUDED),
    ("failed/debit/debt_payment", "scheduled/debit/debt_payment"): (
        7,
        EXCLUDED,
        RESERVED_DEBIT,
    ),
    ("settled/debit/expense", "pending/debit/expense"): (
        6,
        IN_OPENING_BALANCE,
        RESERVED_DEBIT,
    ),
    ("settled/debit/investment_purchase", "settled/credit/investment_sale"): (
        5,
        IN_OPENING_BALANCE,
        IN_OPENING_BALANCE,
    ),
}


class LifecyclePatternTest(unittest.TestCase):
    def test_all_fifty_eight_links_resolve_to_a_parent_in_the_same_user(self):
        pairs = list(linked_pairs())
        self.assertEqual(len(pairs), 58)
        for parent, child in pairs:
            self.assertEqual(parent.user_id, child.user_id, child.event_id)

    def test_each_pattern_gets_its_documented_cash_treatment(self):
        effects = effects_by_event_id()
        seen = {}
        for parent, child in linked_pairs():
            key = (shape(parent), shape(child))
            self.assertIn(key, EXPECTED_PATTERNS, f"undocumented pattern {key}")
            _, parent_state, child_state = EXPECTED_PATTERNS[key]
            self.assertEqual(effects[parent.event_id].state, parent_state, parent.event_id)
            self.assertEqual(effects[child.event_id].state, child_state, child.event_id)
            seen[key] = seen.get(key, 0) + 1

        self.assertEqual(seen, {key: value[0] for key, value in EXPECTED_PATTERNS.items()})

    def test_a_failed_retry_counts_the_scheduled_child_not_the_failed_parent(self):
        effects = effects_by_event_id()
        retries = [
            (parent, child)
            for parent, child in linked_pairs()
            if parent.status == "failed" and child.status == "scheduled"
        ]
        self.assertEqual(len(retries), 7)
        for parent, child in retries:
            self.assertEqual(effects[parent.event_id].state, EXCLUDED)
            self.assertEqual(effects[child.event_id].state, RESERVED_DEBIT)
            self.assertGreater(effects[child.event_id].amount_home, 0)

    def test_a_cancelled_authorization_counts_only_its_settled_child(self):
        effects = effects_by_event_id()
        authorizations = [
            (parent, child)
            for parent, child in linked_pairs()
            if parent.status == "cancelled" and child.status == "settled"
        ]
        self.assertEqual(len(authorizations), 8)
        for parent, child in authorizations:
            self.assertEqual(effects[parent.event_id].state, EXCLUDED)
            self.assertEqual(effects[child.event_id].state, IN_OPENING_BALANCE)

    def test_an_investment_valuation_never_becomes_available_cash(self):
        effects = effects_by_event_id()
        valuations = [e for e in all_events() if e.event_type == "investment_valuation"]
        self.assertEqual(len(valuations), 10)
        for event in valuations:
            self.assertEqual(effects[event.event_id].state, EXCLUDED)

    def test_a_settled_investment_sale_is_real_realized_cash(self):
        effects = effects_by_event_id()
        sales = [e for e in all_events() if e.event_type == "investment_sale"]
        self.assertEqual(len(sales), 5)
        for event in sales:
            self.assertEqual(effects[event.event_id].state, IN_OPENING_BALANCE)


class DuplicateChargeTest(unittest.TestCase):
    """The trap: these look ignorable and must be reserved. See D9."""

    def setUp(self):
        self.duplicates = [
            e for e in all_events() if "duplicate" in e.description.lower()
        ]

    def test_all_six_possible_duplicate_pending_debits_are_reserved(self):
        """The dispute is open and no reversal has been posted on any of the six.

        Each carries a bank message confirming it: "The dispute is open and no
        reversal has been posted yet." The cash has left the account, so excluding
        these rows would overstate headroom by the full charge.
        """
        effects = effects_by_event_id()
        self.assertEqual(len(self.duplicates), 6)
        self.assertEqual(
            {e.event_id for e in self.duplicates},
            {
                "event_12709",
                "event_14399",
                "event_18269",
                "event_19334",
                "event_21582",
                "event_23203",
            },
        )
        for event in self.duplicates:
            self.assertEqual(event.status, "pending")
            self.assertEqual(event.direction, "debit")
            effect = effects[event.event_id]
            self.assertEqual(effect.state, RESERVED_DEBIT, event.event_id)
            self.assertGreater(effect.amount_home, 0)

    def test_the_dispute_is_recorded_as_its_own_reason_code(self):
        # Treatment is the same as any pending debit, but the explanation should be
        # able to say *why* a disputed charge is still being held.
        effects = effects_by_event_id()
        for event in self.duplicates:
            self.assertEqual(
                effects[event.event_id].reason_code, "DISPUTED_DUPLICATE_RESERVED"
            )


class NettingTest(unittest.TestCase):
    def test_no_internal_transfer_pair_exists_as_event_rows(self):
        """The six `internal_transfer` bank messages have no event rows behind them.

        Those messages say "the matching debit and credit came from a transfer between
        your two accounts", and all six carry a blank `related_event_id` - which the
        dataset contract defines as "no one-to-one event row exists". Searching the
        full 25,342 rows confirms it: five of the six users have no equal-magnitude
        debit/credit pair at all (any currency, any date, any status), and the sixth
        (`user_261`) has only the ordinary settled card-reversal pair.

        So the "legs net to zero" requirement is satisfied with no code: there is
        nothing to net, and fabricating the missing legs would be inventing financial
        facts. This test exists so tickets 05 and 10 do not hunt for a stream to
        suppress. The netting *property* is pinned on synthetic future legs in
        `test_cash_state.CashPositionTest`.
        """
        dataset = shared_dataset()
        for user_id in ("user_18", "user_33", "user_57", "user_171", "user_273"):
            events = dataset.events_by_user[user_id]
            credit_amounts = {
                e.amount for e in events if e.direction == "credit" and e.amount is not None
            }
            offsetting = [
                e
                for e in events
                if e.direction == "debit"
                and e.amount is not None
                and e.amount in credit_amounts
            ]
            self.assertEqual(offsetting, [], user_id)

        # There is no transfer event type in the schema at all.
        credit_types = {e.event_type for e in all_events() if e.direction == "credit"}
        self.assertEqual(credit_types, {"income", "refund", "investment_sale"})

    def test_settled_refund_pairs_have_equal_legs_and_net_to_zero(self):
        effects = effects_by_event_id()
        pairs = [
            (parent, child)
            for parent, child in linked_pairs()
            if child.event_type == "refund" and child.status == "settled"
        ]
        self.assertEqual(len(pairs), 14)
        for parent, child in pairs:
            self.assertEqual(parent.amount, child.amount, child.event_id)
            # Both legs are already inside the opening balance, so neither contributes
            # to the forecast: the pair nets to zero without any special-casing.
            self.assertEqual(effects[parent.event_id].state, IN_OPENING_BALANCE)
            self.assertEqual(effects[child.event_id].state, IN_OPENING_BALANCE)


class BlankAmountRealDataTest(unittest.TestCase):
    def test_only_the_four_future_blank_amount_rows_need_image_evidence(self):
        """16 rows have a blank amount, but 12 are settled history already inside the
        balance. Only 4 are future cash events whose value the forecast needs.
        """
        effects = effects_by_event_id()
        blanks = [e for e in all_events() if e.amount is None]
        self.assertEqual(len(blanks), 16)

        unknown = [e for e in blanks if effects[e.event_id].state == UNKNOWN_AMOUNT]
        self.assertEqual(
            {e.event_id for e in unknown},
            {
                "event_1442",  # user_16  scheduled rent      (sample cohort)
                "event_1786",  # user_20  pending telecom     (sample cohort)
                "event_6033",  # user_64  pending groceries   (evaluation cohort)
                "event_6859",  # user_73  scheduled hospital  (evaluation cohort)
            },
        )
        for event in unknown:
            self.assertIsNone(effects[event.event_id].amount_home)

        settled_blanks = [e for e in blanks if e.status == "settled"]
        self.assertEqual(len(settled_blanks), 12)
        for event in settled_blanks:
            self.assertEqual(effects[event.event_id].state, IN_OPENING_BALANCE)


class ForeignOpenRowTest(unittest.TestCase):
    def test_the_eight_foreign_open_rows_are_converted_to_home_currency(self):
        dataset = shared_dataset()
        effects = effects_by_event_id()
        foreign_open = [
            e
            for e in all_events()
            if e.status in OPEN_STATUSES
            and e.currency != dataset.profiles[e.user_id].home_currency
        ]
        self.assertEqual(len(foreign_open), 8)
        for event in foreign_open:
            effect = effects[event.event_id]
            self.assertEqual(effect.state, EXPECTED_CREDIT)
            self.assertIsInstance(effect.amount_home, Decimal)
            self.assertNotEqual(
                effect.amount_home, event.amount, "a converted amount should differ"
            )


if __name__ == "__main__":
    unittest.main()

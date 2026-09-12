"""The six-level lexicographic ranking (Ticket 07).

`rank_key` is the highest-risk primitive in the engine: it is the one place the
challenge's stated priority order is expressed, and a weighted score - or a level in
the wrong position - silently trades a missed deadline for a cost saving. Every level
is therefore pinned by a test that varies *only* that level, so a reordering cannot
pass by accident.

Priority order (problem_statement.md:191, CONTEXT.md section 9):

  1. completes the full request by desired_completion_date
  2. requires no spending changes
  3. lowest total amount paid, fee-inclusive
  4. starts earlier
  5. fewer payments
  6. lowest payment_option_id
  7. request_id - not in the spec; appended so the order is total
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.plans import Plan, best_plan, option_sort_key, rank_key


def make_plan(
    *,
    request_id: str = "request_test",
    method: str = "wait",
    status: str = "affordable_later",
    payments: tuple[tuple[str, str], ...] = (("2025-02-01", "1000"),),
    total_paid: str = "1000",
    completes_by_deadline: bool = True,
    needs_spending_changes: bool = False,
    payment_option_id: str | None = None,
) -> Plan:
    return Plan(
        request_id=request_id,
        method=method,
        status=status,
        payments=tuple(
            (date.fromisoformat(day), Decimal(amount)) for day, amount in payments
        ),
        total_paid=Decimal(total_paid),
        completes_by_deadline=completes_by_deadline,
        needs_spending_changes=needs_spending_changes,
        payment_option_id=payment_option_id,
    )


def winner(*plans: Plan) -> Plan:
    """The ranking's pick, asserted to be independent of input order."""
    forward = best_plan(plans)
    backward = best_plan(tuple(reversed(plans)))
    assert forward == backward, "ranking depends on input order"
    return forward


class LevelOrderTest(unittest.TestCase):
    """Each test varies exactly one level and holds every other level equal."""

    def test_level_1_completing_by_the_deadline_beats_a_cheaper_late_plan(self):
        on_time = make_plan(completes_by_deadline=True, total_paid="5000")
        late = make_plan(completes_by_deadline=False, total_paid="1000")
        self.assertIs(winner(on_time, late), on_time)

    def test_level_2_no_spending_changes_beats_a_cheaper_plan_needing_changes(self):
        clean = make_plan(needs_spending_changes=False, total_paid="5000")
        changed = make_plan(needs_spending_changes=True, total_paid="1000")
        self.assertIs(winner(clean, changed), clean)

    def test_level_2_loses_to_level_1(self):
        """A change-requiring plan that completes beats a clean plan that does not.

        This is the samples 06/11/21 shape: `earliest` falls after the deadline, so the
        only no-change plan is a late `wait`, and the spending-change variant wins at
        level 1 before level 2 is ever consulted.
        """
        clean_but_late = make_plan(
            completes_by_deadline=False, needs_spending_changes=False
        )
        changed_on_time = make_plan(
            completes_by_deadline=True, needs_spending_changes=True
        )
        self.assertIs(winner(clean_but_late, changed_on_time), changed_on_time)

    def test_level_3_lower_fee_inclusive_total_wins(self):
        cheap = make_plan(total_paid="39660")
        dear = make_plan(total_paid="41246.40")
        self.assertIs(winner(cheap, dear), cheap)

    def test_level_3_beats_an_earlier_start(self):
        cheap_later = make_plan(total_paid="1000", payments=(("2025-03-01", "1000"),))
        dear_earlier = make_plan(total_paid="1500", payments=(("2025-02-01", "1500"),))
        self.assertIs(winner(cheap_later, dear_earlier), cheap_later)

    def test_level_4_earlier_start_wins_when_totals_tie(self):
        early = make_plan(payments=(("2025-02-01", "1000"),))
        late = make_plan(payments=(("2025-02-15", "1000"),))
        self.assertIs(winner(early, late), early)

    def test_level_5_fewer_payments_wins_when_start_and_total_tie(self):
        one = make_plan(payments=(("2025-02-01", "1000"),), total_paid="1000")
        two = make_plan(
            payments=(("2025-02-01", "500"), ("2025-03-01", "500")),
            total_paid="1000",
        )
        self.assertIs(winner(one, two), one)

    def test_level_6_lowest_payment_option_id_wins(self):
        low = make_plan(payment_option_id="payment_option_33")
        high = make_plan(payment_option_id="payment_option_54")
        self.assertIs(winner(low, high), low)

    def test_level_6_orders_option_ids_numerically_not_lexically(self):
        """`payment_option_100` is *higher* than `payment_option_33`.

        The supplied ids are zero-padded to two digits but run to 790, so a plain
        string sort puts `payment_option_100` before `payment_option_33`.
        """
        low = make_plan(payment_option_id="payment_option_33")
        high = make_plan(payment_option_id="payment_option_100")
        self.assertIs(winner(low, high), low)
        self.assertLess(
            option_sort_key("payment_option_33"), option_sort_key("payment_option_100")
        )

    def test_level_7_request_id_gives_a_total_order(self):
        first = make_plan(request_id="request_01")
        second = make_plan(request_id="request_02")
        self.assertIs(winner(first, second), first)

    def test_two_plans_alike_in_every_level_have_equal_keys(self):
        self.assertEqual(rank_key(make_plan()), rank_key(make_plan()))


class EmergentPreferencesTest(unittest.TestCase):
    """The behaviours the ticket demands be emergent, never hard-coded."""

    def test_wait_beats_installments_purely_because_installments_carry_a_fee(self):
        wait = make_plan(
            method="wait",
            payments=(("2025-03-15", "39660"),),
            total_paid="39660",
        )
        installments = make_plan(
            method="installments",
            payments=(("2025-02-01", "20623.20"), ("2025-03-01", "20623.20")),
            total_paid="41246.40",
            payment_option_id="payment_option_53",
        )
        self.assertIs(winner(wait, installments), wait)

    def test_a_zero_fee_installment_option_would_beat_wait_on_start_date(self):
        """The converse: nothing in the ranking prefers `wait` as a method.

        No supplied option is fee-free - all 515 carry a financing fee - so this case
        does not arise in the dataset. It exists to prove the preference is a
        consequence of the fee, not a rule about the method name.
        """
        wait = make_plan(
            method="wait", payments=(("2025-03-15", "1000"),), total_paid="1000"
        )
        free_installments = make_plan(
            method="installments",
            payments=(("2025-02-01", "500"), ("2025-03-01", "500")),
            total_paid="1000",
            payment_option_id="payment_option_53",
        )
        self.assertIs(winner(wait, free_installments), free_installments)


class BestPlanTest(unittest.TestCase):
    def test_no_candidates_returns_none(self):
        self.assertIsNone(best_plan(()))

    def test_single_candidate_is_the_winner(self):
        only = make_plan()
        self.assertIs(best_plan((only,)), only)


if __name__ == "__main__":
    unittest.main()

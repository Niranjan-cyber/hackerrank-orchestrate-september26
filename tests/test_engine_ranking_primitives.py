"""Focused primitives: the six-level lexicographic ranking (Ticket 13).

The six spec levels, plus the `request_id` tie-break that makes the order total.
Lexicographic order (never a weighted score), fee-inclusive totals, and pre-rank
pruning - the last proved at the candidate-generation seam, not only at `screen_options`,
so an ineligible option really is absent from the ranking input.

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

from engine.plans import (  # noqa: E402
    Plan,
    best_plan,
    candidate_plans,
    option_sort_key,
    rank_key,
    screen_options,
)
from engine.simulate import (  # noqa: E402
    amount_safe_to_pay,
    earliest_date_for_full_payment,
)
from engine.types import Config  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_position,
    make_payment_option,
    make_profile,
    make_request,
)


def make_plan(
    *,
    request_id: str = "request_test",
    method: str = "wait",
    payments: tuple[tuple[str, str], ...] = (("2025-02-01", "1000"),),
    total_paid: str = "1000",
    completes_by_deadline: bool = True,
    needs_spending_changes: bool = False,
    payment_option_id: str | None = None,
) -> Plan:
    return Plan(
        request_id=request_id,
        method=method,
        status="affordable_later",
        payments=tuple(
            (date.fromisoformat(day), Decimal(amount)) for day, amount in payments
        ),
        total_paid=Decimal(total_paid),
        completes_by_deadline=completes_by_deadline,
        needs_spending_changes=needs_spending_changes,
        payment_option_id=payment_option_id,
    )


def winner(*plans):
    forward = best_plan(plans)
    backward = best_plan(tuple(reversed(plans)))
    assert forward == backward, "ranking depends on input order"
    return forward


def candidates_for(request, profile, options=()):
    """Generate candidates from a flat position, exactly as the shell does."""
    config = Config()
    position = build_position((), request=request, profile=profile)
    safe = amount_safe_to_pay(position, config, request.requested_amount)
    earliest = earliest_date_for_full_payment(
        position, config, request.requested_amount
    )
    return candidate_plans(
        request, profile, options, position, config, safe=safe, earliest=earliest
    )


class LexicographicOrderTest(unittest.TestCase):
    def test_level_1_a_completing_plan_beats_a_cheaper_late_one(self):
        on_time = make_plan(completes_by_deadline=True, total_paid="5000")
        late = make_plan(completes_by_deadline=False, total_paid="1")
        self.assertIs(winner(on_time, late), on_time)

    def test_level_2_no_spending_change_beats_a_cheaper_change_plan(self):
        clean = make_plan(needs_spending_changes=False, total_paid="5000")
        changed = make_plan(needs_spending_changes=True, total_paid="1")
        self.assertIs(winner(clean, changed), clean)

    def test_level_3_lower_fee_inclusive_total_wins(self):
        cheap = make_plan(total_paid="39660")
        dear = make_plan(total_paid="41246.40")
        self.assertIs(winner(cheap, dear), cheap)

    def test_level_3_beats_an_earlier_start(self):
        cheap_later = make_plan(total_paid="1000", payments=(("2025-03-01", "1000"),))
        dear_earlier = make_plan(total_paid="1500", payments=(("2025-02-01", "1500"),))
        self.assertIs(winner(cheap_later, dear_earlier), cheap_later)

    def test_level_4_earlier_start_breaks_a_total_tie(self):
        early = make_plan(payments=(("2025-02-01", "1000"),))
        late = make_plan(payments=(("2025-02-15", "1000"),))
        self.assertIs(winner(early, late), early)

    def test_level_5_fewer_payments_breaks_a_tie(self):
        one = make_plan(payments=(("2025-02-01", "1000"),))
        two = make_plan(
            payments=(("2025-02-01", "500"), ("2025-03-01", "500")),
        )
        self.assertIs(winner(one, two), one)

    def test_level_6_lowest_payment_option_id_breaks_a_tie(self):
        low = make_plan(payment_option_id="payment_option_33")
        high = make_plan(payment_option_id="payment_option_54")
        self.assertIs(winner(low, high), low)

    def test_level_7_request_id_makes_the_order_total(self):
        first = make_plan(request_id="request_01")
        second = make_plan(request_id="request_02")
        self.assertIs(winner(first, second), first)
        self.assertEqual(rank_key(first), rank_key(first))


class FeeInclusiveTotalTest(unittest.TestCase):
    def test_the_option_total_payable_not_the_principal_decides_level_three(self):
        """Sample 19: partial (39,660) beat an installment option (41,246.40)."""
        partial = make_plan(method="partial_payment", total_paid="39660")
        installments = make_plan(
            method="installments",
            total_paid="41246.40",
            payment_option_id="payment_option_53",
        )
        self.assertIs(winner(partial, installments), partial)

    def test_a_generated_plan_carries_the_fees_total_payable_amount(self):
        """The plan total is the option's `total_payable_amount`, not principal x n."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-04-15",
        )
        profile = make_profile(
            balance="100000",
            minimum="2000",
            payment_methods_considered=("installments",),
            max_installment_months=6,
        )
        option = make_payment_option(
            payment_option_id="payment_option_53",
            payment_amount="1000",
            number_of_payments=3,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
            financing_fee="246.40",
            total_payable_amount="3246.40",
        )

        result = candidates_for(request, profile, (option,))
        plan = next(p for p in result.plans if p.method == "installments")

        self.assertEqual(plan.total_paid, Decimal("3246.40"))


class PreRankPruningTest(unittest.TestCase):
    def _request(self):
        return make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-04-15",
        )

    def test_an_option_over_the_users_maximum_is_pruned_before_ranking(self):
        profile = make_profile(
            payment_methods_considered=("installments",), max_installment_months=3
        )
        option = make_payment_option(
            payment_amount="1000",
            number_of_payments=4,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        screened, reasons = screen_options(
            self._request(), profile, (option,), Config()
        )

        self.assertEqual(screened, ())
        self.assertIn("OPTION_EXCEEDS_MAX_INSTALLMENTS", {r.code for r in reasons})

    def test_an_option_ending_after_the_deadline_is_pruned(self):
        profile = make_profile(
            payment_methods_considered=("installments",), max_installment_months=6
        )
        option = make_payment_option(
            payment_amount="1000",
            number_of_payments=3,
            first_payment_date="2025-04-01",
            payment_frequency_days=30,
        )
        request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-04-15",
        )

        screened, reasons = screen_options(request, profile, (option,), Config())

        self.assertEqual(screened, ())
        self.assertIn("OPTION_COMPLETES_AFTER_DEADLINE", {r.code for r in reasons})

    def test_installments_are_pruned_when_the_user_will_not_consider_them(self):
        profile = make_profile(
            payment_methods_considered=("full_payment",), max_installment_months=None
        )
        option = make_payment_option(number_of_payments=2)

        screened, reasons = screen_options(
            self._request(), profile, (option,), Config()
        )

        self.assertEqual(screened, ())
        self.assertIn("INSTALLMENTS_NOT_AN_ACCEPTED_METHOD", {r.code for r in reasons})

    def test_an_eligible_option_survives_the_screen(self):
        profile = make_profile(
            payment_methods_considered=("installments",), max_installment_months=6
        )
        option = make_payment_option(
            payment_option_id="payment_option_07",
            payment_amount="1000",
            number_of_payments=3,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        screened, _ = screen_options(self._request(), profile, (option,), Config())

        self.assertEqual(
            [entry.option.payment_option_id for entry in screened],
            ["payment_option_07"],
        )

    def test_a_pruned_option_is_absent_from_the_ranking_input(self):
        profile = make_profile(
            balance="100000",
            minimum="2000",
            payment_methods_considered=("installments",),
            max_installment_months=3,
        )
        option = make_payment_option(
            payment_amount="1000",
            number_of_payments=4,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        result = candidates_for(self._request(), profile, (option,))

        self.assertEqual([p.method for p in result.plans], [])
        self.assertIn(
            "OPTION_EXCEEDS_MAX_INSTALLMENTS", {r.code for r in result.reasons}
        )
        option = make_payment_option(
            payment_option_id="payment_option_07",
            payment_amount="1000",
            number_of_payments=3,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        screened, _ = screen_options(self._request(), profile, (option,), Config())

        self.assertEqual(
            [entry.option.payment_option_id for entry in screened],
            ["payment_option_07"],
        )


class TotalOrderTieBreakTest(unittest.TestCase):
    def test_option_ids_order_numerically_not_lexically(self):
        self.assertLess(
            option_sort_key("payment_option_33"),
            option_sort_key("payment_option_100"),
        )

    def test_plans_with_no_option_sort_ahead_of_every_option(self):
        self.assertLess(option_sort_key(None), option_sort_key("payment_option_01"))


if __name__ == "__main__":
    unittest.main()

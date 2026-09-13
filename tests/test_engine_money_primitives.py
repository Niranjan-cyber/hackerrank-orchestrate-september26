"""Focused primitives: Decimal arithmetic, rounding and the number formats.

Exact comparison, output-only quantisation, and the two graded CSV formats
(`payment_plan` / `reduce_to` and `amount_safe_to_pay`). The comma-grouped
`decision_explanation` prose format is pinned too, only to prove it stays distinct from
the two CSV columns a refactor could otherwise merge.

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

from engine.cash import convert  # noqa: E402
from engine.money import (  # noqa: E402
    format_explanation_amount,
    format_plan_amount,
    format_safe_amount,
    money_scale,
    quantize,
)
from engine.simulate import amount_safe_to_pay  # noqa: E402
from engine.types import Config  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_position,
    make_event,
    make_profile,
)


class ExactArithmeticTest(unittest.TestCase):
    def test_currency_conversion_is_exact_and_never_rounds_early(self):
        rates = {("2025-01-10", "USD", "INR"): Decimal("83.333")}
        self.assertEqual(
            convert(Decimal("3"), "USD", "INR", date(2025, 1, 10), rates),
            Decimal("249.999"),
        )

    def test_the_safe_amount_keeps_sub_cent_precision(self):
        """Intermediate rounding would report 8000.00; exact comparison reports 7999.999."""
        events = (
            make_event(
                event_id="tiny",
                status="scheduled",
                direction="debit",
                amount="0.001",
                settlement_date="2025-02-10",
            ),
        )
        position = build_position(
            events, profile=make_profile(balance="10000", minimum="2000")
        )

        safe = amount_safe_to_pay(position, Config(), Decimal("9000"))

        self.assertEqual(safe, Decimal("7999.999"))

    def test_quantisation_happens_only_at_the_output_boundary(self):
        safe = Decimal("7999.999")
        self.assertEqual(format_safe_amount(safe), "8000")
        # ...and the unrounded value is still the graded number.
        self.assertNotEqual(safe, quantize(safe))


class RoundingTest(unittest.TestCase):
    def test_quantize_rounds_half_up_to_two_places(self):
        self.assertEqual(quantize(Decimal("2.005")), Decimal("2.01"))
        self.assertEqual(quantize(Decimal("2.004")), Decimal("2.00"))
        self.assertEqual(quantize(Decimal("23.505")), Decimal("23.51"))

    def test_money_scale_normalises_a_non_terminating_modelled_amount(self):
        # A mean-of-three is non-terminating; the ledger must not carry it.
        self.assertEqual(money_scale(Decimal("10") / Decimal("3")), Decimal("3.33"))


class NumberFormatTest(unittest.TestCase):
    def test_plan_amount_is_two_dp_when_fractional_else_a_bare_integer(self):
        self.assertEqual(format_plan_amount(Decimal("620.40")), "620.40")
        self.assertEqual(format_plan_amount(Decimal("23.50")), "23.50")
        self.assertEqual(format_plan_amount(Decimal("15952906.67")), "15952906.67")
        self.assertEqual(format_plan_amount(Decimal("25256")), "25256")

    def test_safe_amount_is_the_shortest_natural_form(self):
        self.assertEqual(format_safe_amount(Decimal("603.3")), "603.3")
        self.assertEqual(format_safe_amount(Decimal("17229139.2")), "17229139.2")
        self.assertEqual(format_safe_amount(Decimal("243849.58")), "243849.58")
        self.assertEqual(format_safe_amount(Decimal("25256")), "25256")

    def test_the_two_graded_formats_disagree_on_trailing_zeros(self):
        value = Decimal("603.30")
        self.assertEqual(format_plan_amount(value), "603.30")
        self.assertEqual(format_safe_amount(value), "603.3")

    def test_explanation_amount_groups_thousands_but_the_csv_columns_never_do(self):
        self.assertEqual(format_explanation_amount(Decimal("25256")), "25,256")
        self.assertEqual(
            format_explanation_amount(Decimal("15952906.67")), "15,952,906.67"
        )
        self.assertEqual(format_plan_amount(Decimal("25256")), "25256")


if __name__ == "__main__":
    unittest.main()

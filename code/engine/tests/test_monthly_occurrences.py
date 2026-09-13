"""The single rule for where a monthly occurrence lands (Ticket 10 seam).

Ticket 05 projects detected streams; ticket 10 creates a stream from a confirmed
salary fact. Both must place occurrences by the same rule, so the rule is one public
function rather than two agreeing implementations.
"""

from __future__ import annotations

import unittest
from datetime import date

from engine.recurrence import monthly_occurrences


class MonthlyOccurrencesTest(unittest.TestCase):
    def test_lands_on_the_same_day_each_month_within_the_range(self):
        self.assertEqual(
            monthly_occurrences(
                day_of_month=10,
                start=date(2025, 2, 1),
                end=date(2025, 4, 30),
                direction="debit",
            ),
            (date(2025, 2, 10), date(2025, 3, 10), date(2025, 4, 10)),
        )

    def test_range_is_inclusive_at_both_ends(self):
        self.assertEqual(
            monthly_occurrences(
                day_of_month=10,
                start=date(2025, 2, 10),
                end=date(2025, 3, 10),
                direction="debit",
            ),
            (date(2025, 2, 10), date(2025, 3, 10)),
        )

    def test_clamps_a_day_the_month_does_not_have(self):
        # The 31st of February is the 28th, not an error and not March 3rd.
        self.assertEqual(
            monthly_occurrences(
                day_of_month=31,
                start=date(2025, 2, 1),
                end=date(2025, 2, 28),
                direction="debit",
            ),
            (date(2025, 2, 28),),
        )

    def test_a_debit_rolls_forward_off_a_weekend(self):
        # 2025-03-15 is a Saturday. A debit is later, which is the safer direction.
        self.assertEqual(
            monthly_occurrences(
                day_of_month=15,
                start=date(2025, 3, 1),
                end=date(2025, 3, 31),
                direction="debit",
            ),
            (date(2025, 3, 17),),
        )

    def test_a_credit_rolls_back_off_a_weekend(self):
        # The same Saturday: income arrives earlier, never later than stated.
        self.assertEqual(
            monthly_occurrences(
                day_of_month=15,
                start=date(2025, 3, 1),
                end=date(2025, 3, 31),
                direction="credit",
            ),
            (date(2025, 3, 14),),
        )

    def test_a_rolled_date_from_the_previous_month_still_lands_in_range(self):
        # 2025-02-28 is a Friday, so take the 29th: in 2025 February has 28 days, the
        # 29th clamps to the 28th. Use a debit on the 30th of November instead:
        # 2025-11-30 is a Sunday, so the debit rolls into December.
        self.assertEqual(
            monthly_occurrences(
                day_of_month=30,
                start=date(2025, 12, 1),
                end=date(2025, 12, 5),
                direction="debit",
            ),
            (date(2025, 12, 1),),
        )

    def test_an_empty_range_yields_nothing(self):
        self.assertEqual(
            monthly_occurrences(
                day_of_month=10,
                start=date(2025, 4, 1),
                end=date(2025, 3, 1),
                direction="debit",
            ),
            (),
        )

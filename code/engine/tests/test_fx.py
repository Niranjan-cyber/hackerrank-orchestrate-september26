"""Currency conversion: the dated-rate rule and its failure mode.

The spec is narrow: for a foreign-currency cash event, use the rate row for its
*settlement date* and the stated `from_currency` -> `to_currency` direction
(AGENTS.md 6.1). No inverse rates, no nearest-date fallback, no default.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.cash import MissingRateError, convert

from .support import shared_dataset

RATES = {
    ("2025-01-10", "USD", "INR"): Decimal("83.10"),
    ("2025-02-15", "USD", "INR"): Decimal("84.50"),
    ("2025-02-15", "EUR", "ZAR"): Decimal("19.80"),
}


class ConvertTest(unittest.TestCase):
    def test_same_currency_is_identity_and_needs_no_rate_row(self):
        self.assertEqual(
            convert(Decimal("250.75"), "INR", "INR", date(2030, 1, 1), {}),
            Decimal("250.75"),
        )

    def test_converts_at_the_rate_for_the_given_date(self):
        self.assertEqual(
            convert(Decimal("100"), "USD", "INR", date(2025, 1, 10), RATES),
            Decimal("8310.00"),
        )

    def test_picks_the_row_for_the_settlement_date_not_a_neighbouring_date(self):
        # Same pair, two dates, different rates. Choosing the wrong row is a silent
        # 1.7% error, so this is pinned rather than assumed.
        self.assertEqual(
            convert(Decimal("100"), "USD", "INR", date(2025, 2, 15), RATES),
            Decimal("8450.00"),
        )

    def test_respects_the_directed_pair_and_never_inverts_a_rate(self):
        # ZAR -> EUR is absent from the table even though EUR -> ZAR is present.
        # Inverting would look helpful and be wrong; the dataset needs no inversion.
        with self.assertRaises(MissingRateError):
            convert(Decimal("100"), "ZAR", "EUR", date(2025, 2, 15), RATES)

    def test_missing_rate_fails_loudly_rather_than_defaulting(self):
        with self.assertRaises(MissingRateError) as caught:
            convert(Decimal("100"), "USD", "INR", date(2025, 3, 1), RATES)
        message = str(caught.exception)
        self.assertIn("USD", message)
        self.assertIn("INR", message)
        self.assertIn("2025-03-01", message)

    def test_conversion_is_exact_and_never_rounds_early(self):
        # Rounding is a presentation step that happens once, at output (D5). An exact
        # product must survive unrounded, or floor comparisons drift.
        rates = {("2025-01-10", "USD", "INR"): Decimal("83.333")}
        self.assertEqual(
            convert(Decimal("3"), "USD", "INR", date(2025, 1, 10), rates),
            Decimal("249.999"),
        )


class RealDatasetCoverageTest(unittest.TestCase):
    """FX coverage is complete: no fallback path is needed, and none exists."""

    def setUp(self):
        self.dataset = shared_dataset()
        self.foreign = [
            event
            for user_id, events in self.dataset.events_by_user.items()
            for event in events
            if event.currency != self.dataset.profiles[user_id].home_currency
        ]

    def test_the_dataset_holds_one_hundred_and_forty_foreign_rows(self):
        self.assertEqual(len(self.foreign), 140)

    def test_every_priced_foreign_event_has_an_exact_rate_row(self):
        priced = [event for event in self.foreign if event.amount is not None]
        self.assertEqual(len(priced), 139)
        for event in priced:
            home = self.dataset.profiles[event.user_id].home_currency
            self.assertGreater(
                convert(
                    event.amount,
                    event.currency,
                    home,
                    event.cash_date,
                    self.dataset.rates,
                ),
                0,
                event.event_id,
            )

    def test_the_one_unpriced_foreign_row_is_settled_history(self):
        # `event_7307`, a settled USD taxi fare for an INR user. It has a blank amount,
        # but it is already inside the opening balance, so the forecast never needs to
        # price it - which is why 139, not 140, is the convertible count.
        (unpriced,) = [event for event in self.foreign if event.amount is None]
        self.assertEqual(unpriced.event_id, "event_7307")
        self.assertEqual(unpriced.status, "settled")

    def test_every_foreign_row_has_an_explicit_settlement_date(self):
        """So conversion never depends on `cash_date`'s event_date fallback.

        The criterion names `settlement_date`. `classify_event` converts at
        `Event.cash_date`, which falls back to `event_date` when settlement is blank -
        a fallback that must never fire for a foreign row, or the rule would quietly
        become "settlement date, except sometimes the event date".
        """
        for event in self.foreign:
            self.assertIsNotNone(event.settlement_date, event.event_id)
            self.assertEqual(event.cash_date, event.settlement_date, event.event_id)


if __name__ == "__main__":
    unittest.main()

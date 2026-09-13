"""The ticket 14 freeze, expressed as a test.

The calibration block was timeboxed and ended on 2026-09-13 with these seven values
settled against the 25 solved samples. "Frozen" has to mean something a later change
runs into, not a sentence in a document, so each value is pinned here: moving one turns
an invisible drift into a failing test and a deliberate edit to this file.

The reasoning behind each value, the measurements that chose it and the alternatives
that lost are in `docs/investigation/calibration.md`. Do not re-tune from here - the
block is closed.
"""

from __future__ import annotations

import unittest

from engine.types import Config

FROZEN_ON = "2026-09-13"

# The seven axes ticket 14 was allowed to sweep, and the value each settled at.
FROZEN = {
    "lookback_days": 90,
    "min_occurrences": 2,
    "variable_spend_estimator": "max_median3_mean6",
    "project_income_beyond_confirmed": True,
    "variable_spend_shape": "individual_events",
    "variable_spend_placement": "earliest",
    "same_day_ordering": "debits_credits_payment",
}


class FrozenCalibrationTest(unittest.TestCase):
    def test_every_swept_parameter_still_holds_its_frozen_value(self):
        config = Config()
        for name, value in FROZEN.items():
            with self.subTest(parameter=name):
                self.assertEqual(getattr(config, name), value)

    def test_the_freeze_covers_exactly_the_seven_axes_the_ticket_named(self):
        """A parameter added to the block later would be an unrecorded eighth axis."""
        self.assertEqual(len(FROZEN), 7)
        for name in FROZEN:
            self.assertTrue(
                hasattr(Config(), name), f"{name} is no longer a Config field"
            )


if __name__ == "__main__":
    unittest.main()

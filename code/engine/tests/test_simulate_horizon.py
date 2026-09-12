"""Simulation beyond the base 90-day window (review finding I2).

`simulate` extends its ledger to cover injected plan payments, but Ticket 05's
inferred recurring projections used to stop at day 90. A plan extending past day 90
could therefore be re-checked with its explicit payments but *without* the recurring
expenses that fall in the extended window - a silent under-check.

The fix makes the projection horizon explicit. A position built by
`recurrence.with_projections(..., horizon_end=...)` records how far its inferred
streams reach; `simulate` refuses to extend past that record rather than certify a
window it cannot see. Callers that evaluate long plans project far enough first.

Not reachable from the supplied dataset (max desired-completion distance is 86 days),
which is why it is pinned on a hand-built position instead.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from engine.cash import PROJECTED_DEBIT, cash_position
from engine.recurrence import with_projections
from engine.simulate import ProjectionHorizonError, simulate
from engine.types import Config

from .support import make_event, make_profile, make_request

REQUEST_DATE = date(2025, 2, 1)
BASE_HORIZON_END = date(2025, 5, 2)  # REQUEST_DATE + 90
EXTENDED_HORIZON_END = date(2025, 7, 1)


def monthly_rents():
    """Four settled rents on the 3rd -> Ticket 05 projects Mar/Apr (and beyond)."""
    return tuple(
        make_event(
            event_id=f"rent_{month}",
            category="rent",
            description="Monthly rent",
            amount="45000",
            settlement_date=f"2024-{month:02d}-03",
            status="settled",
        )
        for month in (9, 10, 11, 12)
    )


def projected_dates(position):
    return {
        effect.cash_date
        for effect in position.effects
        if effect.state == PROJECTED_DEBIT
    }


class ExtendedProjectionTest(unittest.TestCase):
    def build(self, *, horizon_end=None, balance="130000", minimum="10000"):
        request = make_request(
            request_date=REQUEST_DATE.isoformat(), requested_amount="1000"
        )
        profile = make_profile(balance=balance, minimum=minimum)
        config = Config()
        events = monthly_rents()
        position = cash_position(request, profile, events, {})
        position = with_projections(
            position,
            events,
            request,
            profile,
            config,
            {},
            horizon_end=horizon_end,
        )
        return position, request, config

    def test_base_projection_stops_at_day_90(self):
        position, _, _ = self.build()
        dates = projected_dates(position)
        self.assertIn(date(2025, 4, 3), dates)
        self.assertNotIn(
            date(2025, 6, 3), dates, "recurrence must not invent past the base horizon"
        )

    def test_extended_horizon_projects_the_recurring_expense_after_day_90(self):
        position, _, _ = self.build(horizon_end=EXTENDED_HORIZON_END)
        dates = projected_dates(position)
        self.assertIn(date(2025, 6, 3), dates)
        self.assertTrue(
            any(when > BASE_HORIZON_END for when in dates),
            f"expected a projected expense past day 90 in {sorted(dates)}",
        )

    def test_extended_recheck_includes_a_recurring_expense_after_day_90(self):
        position, _, config = self.build(horizon_end=EXTENDED_HORIZON_END)
        ledger = simulate(
            position,
            config,
            extra_debits=((date(2025, 6, 15), Decimal("1000")),),
        )
        # The June 3 rent is inside the extended window and must be applied.
        applied = {(step.when, step.event_id) for step in ledger.steps}
        self.assertTrue(
            any(
                when == date(2025, 6, 3) and eid.startswith("projected:")
                for when, eid in applied
            ),
            f"June 3 projected rent missing from {sorted(applied)}",
        )
        self.assertFalse(ledger.holds_floor)

    def test_extending_past_the_projected_horizon_fails_loudly(self):
        # A payment past day 90 on a position whose streams stop at day 90 cannot be
        # certified: silently ignoring the missing recurring spend is the unsafe read.
        position, _, config = self.build()
        self.assertEqual(position.projected_until, BASE_HORIZON_END)
        with self.assertRaises(ProjectionHorizonError):
            simulate(
                position,
                config,
                extra_debits=((date(2025, 6, 15), Decimal("1000")),),
            )

    def test_base_window_simulation_still_works_without_an_extended_horizon(self):
        position, _, config = self.build()
        # No raise: the window is exactly the projected horizon.
        simulate(position, config)


if __name__ == "__main__":
    unittest.main()

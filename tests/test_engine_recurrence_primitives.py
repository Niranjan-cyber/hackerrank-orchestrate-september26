"""Focused primitives: recurrence detection (Ticket 13).

The four shapes that are hard to diagnose from a wrong CSV cell are pinned directly:
a monthly stream, a semi-monthly pattern, a genuinely irregular one, and the
two-occurrence rule. Every assertion is on a detected `Stream` or a projected
`CashEffect` - the observable recurrence output - never on a call being made.

Run: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE.parent / "code", _HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from engine.cash import PROJECTED_CREDIT, PROJECTED_DEBIT  # noqa: E402
from engine.recurrence import detect_streams, projected_effects  # noqa: E402
from engine.types import Config  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_position,
    make_event,
    make_profile,
    make_request,
)

REQUEST_DATE = "2025-02-01"


def history(*rows):
    return tuple(make_event(**row) for row in rows)


def streams_for(events, profile, request_date=REQUEST_DATE, config=None):
    request = make_request(request_date=request_date)
    position = build_position(events, request=request, profile=profile)
    return (
        request,
        position,
        detect_streams(position, events, request, profile, config or Config()),
    )


def monthly_rents(months, day="03", amount="1000"):
    return history(
        *(
            dict(
                event_id=f"rent_{index}",
                category="rent",
                description="Monthly rent",
                amount=amount,
                event_date=f"{month}-{day}",
                settlement_date=f"{month}-{day}",
            )
            for index, month in enumerate(months)
        )
    )


class RecurrencePrimitivesTest(unittest.TestCase):
    def test_three_occurrences_form_a_monthly_stream_that_projects_forward(self):
        events = monthly_rents(("2024-10", "2024-11", "2024-12"))
        profile = make_profile()
        request, position, streams = streams_for(events, profile)

        self.assertEqual([(s.category, s.day_of_month) for s in streams], [("rent", 3)])
        projected = projected_effects(position, events, request, profile, Config())
        self.assertEqual(
            {e.cash_date for e in projected if e.state == PROJECTED_DEBIT},
            {date(2025, 2, 3), date(2025, 3, 3), date(2025, 4, 3)},
        )

    def test_semi_monthly_is_two_monthly_streams_not_one_fortnightly(self):
        """A day-1 + day-15 salary must not be read as a 15-day stream."""
        rows = []
        for month in ("2024-10", "2024-11", "2024-12", "2025-01"):
            for day in ("01", "15"):
                rows.append(
                    dict(
                        event_id=f"salary_{month}_{day}",
                        event_type="income",
                        direction="credit",
                        category="salary",
                        description="Monthly salary",
                        amount="5000",
                        event_date=f"{month}-{day}",
                        settlement_date=f"{month}-{day}",
                    )
                )
        events = history(*rows)
        profile = make_profile()
        request, position, streams = streams_for(events, profile)

        incomes = [s for s in streams if s.direction == "credit"]
        self.assertEqual(sorted(s.day_of_month for s in incomes), [1, 15])
        projected = projected_effects(position, events, request, profile, Config())
        projected_credits = [e for e in projected if e.state == PROJECTED_CREDIT]
        self.assertTrue(projected_credits)
        # Both halves of the month are represented in the window.
        self.assertTrue(any(e.cash_date.day <= 5 for e in projected_credits))
        self.assertTrue(any(12 <= e.cash_date.day <= 18 for e in projected_credits))

    def test_wobble_within_the_date_tolerance_stays_one_stream(self):
        events = history(
            dict(
                event_id="rent_a",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-09-03",
                settlement_date="2024-09-03",
            ),
            dict(
                event_id="rent_b",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-10-04",
                settlement_date="2024-10-04",
            ),
            dict(
                event_id="rent_c",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-11-06",
                settlement_date="2024-11-06",
            ),
            dict(
                event_id="rent_d",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-12-05",
                settlement_date="2024-12-05",
            ),
        )
        _, _, streams = streams_for(events, make_profile())

        self.assertEqual([s.category for s in streams], ["rent"])

    def test_irregular_spacing_is_not_mistaken_for_a_monthly_stream(self):
        events = history(
            dict(
                event_id="odd_a",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-09-02",
                settlement_date="2024-09-02",
            ),
            dict(
                event_id="odd_b",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-10-17",
                settlement_date="2024-10-17",
            ),
            dict(
                event_id="odd_c",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-11-25",
                settlement_date="2024-11-25",
            ),
            dict(
                event_id="odd_d",
                category="rent",
                description="Monthly rent",
                amount="1000",
                event_date="2024-12-09",
                settlement_date="2024-12-09",
            ),
        )
        _, _, streams = streams_for(events, make_profile())

        self.assertEqual(streams, ())

    def test_two_occurrences_project_only_for_a_protected_category(self):
        """Pinned at `min_occurrences=3`: the carve-out is only observable above the
        threshold. Ticket 14 froze the shipped threshold at 2, at which two
        occurrences are a stream in every category, protected or not."""
        above_threshold = Config(min_occurrences=3)
        two_rents = monthly_rents(("2024-12", "2025-01"))
        two_streams = monthly_rents(("2024-12", "2025-01"))
        two_streams = tuple(
            make_event(
                event_id=event.event_id,
                category="streaming",
                description="Streaming plan",
                amount="1000",
                event_date=event.event_date.isoformat(),
                settlement_date=event.settlement_date.isoformat(),
            )
            for event in two_streams
        )

        _, _, protected = streams_for(
            two_rents,
            make_profile(protected_categories=("rent",)),
            config=above_threshold,
        )
        _, _, unprotected = streams_for(
            two_streams, make_profile(), config=above_threshold
        )

        self.assertEqual([s.category for s in protected], ["rent"])
        self.assertEqual(unprotected, ())

    def test_below_two_occurrences_never_projects_even_when_protected(self):
        one = monthly_rents(("2025-01",))
        _, _, streams = streams_for(one, make_profile(protected_categories=("rent",)))

        self.assertEqual(streams, ())


if __name__ == "__main__":
    unittest.main()

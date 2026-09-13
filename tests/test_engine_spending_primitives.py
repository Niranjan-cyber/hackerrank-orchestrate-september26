"""Focused primitives: spending-change pruning and selection (Ticket 13).

The biconditional (expand change variants iff no safe no-change plan completes by the
deadline), the three published samples that force the converse, and the
reduce-beats-stop selection rule. All assertions are on candidate plans, on the
rendered change strings, and on the published sample rows.

Run: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import csv
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE.parent / "code", _HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from engine.cash import cash_position  # noqa: E402
from engine.loaders import load_dataset  # noqa: E402
from engine.plans import best_plan, candidate_plans  # noqa: E402
from engine.recurrence import detect_streams, with_projections  # noqa: E402
from engine.simulate import (  # noqa: E402
    amount_safe_to_pay,
    earliest_date_for_full_payment,
)
from engine.spending import (  # noqa: E402
    SpendingChange,
    eligible_changes,
    render_changes,
    sufficient_change_sets,
)
from engine.types import Config  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    make_event,
    make_profile,
    make_request,
)

REPO_ROOT = _HERE.parent


def squeeze_events():
    """2,500/month streaming on the 10th, plus 10,000 of salary on the 25th.

    Only 2,500 of a 5,000 request is safe today, and the full amount is not safe
    until the salary lands - so a late `wait` plan exists and the full request does
    not complete by an early deadline. Stopping the subscription lifts today's
    headroom by exactly the 2,500 shortfall.
    """
    return tuple(
        make_event(
            event_id=f"event_stream_{index}",
            event_type="subscription",
            description="Family streaming plan",
            category="streaming",
            flexibility="stoppable",
            amount="2500",
            event_date=f"{month}-10",
            settlement_date=f"{month}-10",
        )
        for index, month in enumerate(("2024-11", "2024-12", "2025-01"))
    ) + (
        make_event(
            event_id="event_salary",
            event_type="income",
            status="scheduled",
            direction="credit",
            amount="10000",
            event_date="2025-02-25",
            settlement_date="2025-02-25",
        ),
    )


def squeeze_profile():
    return make_profile(
        balance="7000", minimum="2000", stoppable_categories=("streaming",)
    )


def candidates_for(request, profile, events):
    config = Config()
    events = tuple(events)
    position = cash_position(request, profile, events, {})
    streams = detect_streams(position, events, request, profile, config, {})
    position = with_projections(
        position, events, request, profile, config, {}, streams=streams
    )
    safe = amount_safe_to_pay(position, config, request.requested_amount)
    earliest = earliest_date_for_full_payment(
        position, config, request.requested_amount
    )
    changes = eligible_changes(
        streams, {event.event_id: event for event in events}, profile, {}
    )
    return candidate_plans(
        request,
        profile,
        (),
        position,
        config,
        safe=safe,
        earliest=earliest,
        changes=changes,
    )


class SpendingChangeBiconditionalTest(unittest.TestCase):
    def setUp(self):
        self.profile = squeeze_profile()
        self.events = squeeze_events()

    def test_the_fixture_really_is_short_and_really_is_late(self):
        """Guard: without this the two tests below could pass vacuously."""
        request = make_request(request_date="2025-02-01", requested_amount="5000")
        config = Config()
        position = cash_position(request, self.profile, tuple(self.events), {})
        position = with_projections(
            position,
            tuple(self.events),
            request,
            self.profile,
            config,
            {},
        )

        self.assertEqual(
            amount_safe_to_pay(position, config, request.requested_amount),
            Decimal("2500"),
        )
        self.assertEqual(
            earliest_date_for_full_payment(position, config, request.requested_amount),
            date(2025, 2, 25),
        )

    def test_changes_are_offered_when_no_no_change_plan_completes(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-02-10",
        )

        result = candidates_for(request, self.profile, self.events)
        winner = best_plan(result.plans)

        self.assertEqual(winner.method, "full_payment")
        self.assertEqual(winner.status, "affordable_with_plan")
        self.assertTrue(winner.needs_spending_changes)
        self.assertEqual(winner.spending_changes, ("stop:event_stream_2",))
        self.assertTrue(winner.completes_by_deadline)

    def test_changes_are_never_offered_when_a_no_change_plan_completes(self):
        # Same user, same shortfall - only the deadline moves past `earliest`.
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-03-01",
        )

        result = candidates_for(request, self.profile, self.events)

        self.assertEqual(best_plan(result.plans).method, "wait")
        self.assertFalse(any(plan.needs_spending_changes for plan in result.plans))

    def test_an_insufficient_permitted_saving_is_never_offered(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="6500",
            desired_completion_date="2025-02-10",
        )

        result = candidates_for(request, self.profile, self.events)

        self.assertFalse(any(plan.needs_spending_changes for plan in result.plans))
        self.assertIn(
            "NO_SUFFICIENT_SPENDING_CHANGE_SET", {r.code for r in result.reasons}
        )

    def test_a_sufficient_saving_that_lands_after_the_breach_is_refused(self):
        """A sufficient saving is necessary, never sufficient: the ledger still rules."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="4000",
            desired_completion_date="2025-02-06",
        )
        events = self.events[:-1] + (
            make_event(
                event_id="event_early",
                status="scheduled",
                direction="debit",
                amount="2000",
                event_date="2025-02-05",
                settlement_date="2025-02-05",
            ),
            make_event(
                event_id="event_early_salary",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="10000",
                event_date="2025-02-08",
                settlement_date="2025-02-08",
            ),
        )

        result = candidates_for(request, self.profile, events)

        self.assertFalse(any(plan.needs_spending_changes for plan in result.plans))
        codes = {r.code for r in result.reasons}
        self.assertIn("PLAN_BREACHES_MINIMUM_BALANCE", codes)
        self.assertIn("NO_CERTIFIED_SPENDING_CHANGE_SET", codes)


def change(event_id, action, saving, new_amount=None):
    return SpendingChange(
        action=action,
        event_id=event_id,
        category="streaming",
        description="a commitment",
        saving=Decimal(saving),
        new_amount=None if new_amount is None else Decimal(new_amount),
    )


class ReduceBeatsStopSelectionTest(unittest.TestCase):
    def test_the_smallest_sufficient_total_beats_a_single_larger_stop(self):
        """Sample 21: two small changes (34.50) beat one sufficient stop (47)."""
        candidates = (
            change("event_01", "stop", "11"),
            change("event_02", "reduce_to", "23.50", "23.50"),
            change("event_02", "stop", "47"),
        )

        chosen = next(iter(sufficient_change_sets(candidates, Decimal("31.05"), 3)))

        self.assertEqual(
            [c.render() for c in chosen],
            ["stop:event_01", "reduce_to:event_02:23.50"],
        )

    def test_stop_entries_precede_reduce_entries_in_the_rendered_output(self):
        chosen = (
            change("event_09", "reduce_to", "5", "5"),
            change("event_01", "stop", "11"),
        )

        self.assertEqual(
            render_changes(chosen), ("stop:event_01", "reduce_to:event_09:5")
        )


class SamplesNeedingExpansionTest(unittest.TestCase):
    """Samples 06, 11 and 21 are the published answers that force the converse.

    Each is a change-funded full payment whose published `earliest` full-payment date
    falls *after* its deadline. Because `full_payment` is an accepted method for each
    user, that means a late `wait` plan is generated - so a pruning rule that expands
    variants only when *no* plan exists at all would lose all three and produce nothing
    to beat the late wait. The engine-level biconditional is pinned on the hand-built
    fixture above; this class pins the published shape that motivates it. (Sample 11 is
    reproduced in shape but not end to end in our build - the divergence is projection
    placement, CONTEXT.md section 10, asserted in the engine's `Sample11Test`.)
    """

    REQUEST_IDS = ("request_06", "request_11", "request_21")

    @classmethod
    def setUpClass(cls):
        with (REPO_ROOT / "dataset" / "sample_requests.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            rows = {row["request_id"]: row for row in csv.DictReader(handle)}
        cls.rows = rows
        dataset = load_dataset(REPO_ROOT / "dataset")
        cls.dataset = dataset

    def test_all_three_published_answers_need_a_spending_change(self):
        for request_id in self.REQUEST_IDS:
            with self.subTest(request=request_id):
                row = self.rows[request_id]
                self.assertNotIn(
                    row["spending_changes_needed"], ("", "none"), request_id
                )

    def test_all_three_published_earliest_dates_fall_after_the_deadline(self):
        for request_id in self.REQUEST_IDS:
            with self.subTest(request=request_id):
                row = self.rows[request_id]
                self.assertGreater(
                    row["earliest_date_for_full_payment"],
                    row["desired_completion_date"],
                    request_id,
                )

    def test_a_late_wait_plan_would_be_generated_for_each(self):
        """The precondition that makes the converse load-bearing, not a shortcut."""
        for request_id in self.REQUEST_IDS:
            with self.subTest(request=request_id):
                row = self.rows[request_id]
                profile = self.dataset.profiles[row["user_id"]]
                self.assertIn("full_payment", profile.payment_methods_considered)


if __name__ == "__main__":
    unittest.main()

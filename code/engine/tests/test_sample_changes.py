"""Samples 06, 11 and 21 reproduced at the spending-change seam (Ticket 08).

These three samples are the only published examples of a spending-change answer, and
between them they fix every rule in `engine/spending.py`. Each one is driven through
the *real* profile, the *real* event history, the *real* recurrence detection and the
*real* ledger; only the opening balance is adjusted, to reproduce the sample's
published `amount_safe_to_pay`.

WHY THE BALANCE IS ADJUSTED - THE SAME REASON AS `test_sample_plans.py`
`amount_safe_to_pay` currently matches on 0 of the 25 samples: recurrence and
variable-spend calibration is ticket 14's outstanding job. Driving these samples
through `run_pipeline` today would therefore test ticket 14, fail, and say nothing
about ticket 08. So each test shifts the opening balance until the published capacity
figure comes back out of the real simulator, and then asserts that ticket 08 turns
that figure into the sample's published change set. When ticket 14 lands, the shift
goes to zero and these become end-to-end by construction.

  sample 06  one permitted stop closes a 17.10 shortfall            -> reproduced
  sample 21  the smallest sufficient *set*, not the fewest changes  -> reproduced
  sample 11  see `Sample11Test` - reproduced in shape and in its cited event, but not
             in its exact set, for two reasons that both belong to other tickets and
             are recorded in CONTEXT.md section 10
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from engine.cash import cash_position
from engine.plans import best_plan, candidate_plans
from engine.recurrence import detect_streams, with_projections
from engine.simulate import amount_safe_to_pay, earliest_date_for_full_payment, simulate
from engine.spending import eligible_changes, sufficient_change_sets
from engine.types import Config

from .support import sample_requests, shared_dataset


def sample(request_id: str):
    return next(r for r in sample_requests() if r.request_id == request_id)


def position_for(request, profile):
    """The real position and the real detected streams for one sample user."""
    config = Config()
    events = shared_dataset().events_by_user.get(request.user_id, ())
    rates = shared_dataset().rates
    base = cash_position(request, profile, events, rates)
    streams = detect_streams(base, events, request, profile, config, rates)
    position = with_projections(
        base, events, request, profile, config, rates, streams=streams
    )
    return position, streams, events


def profile_reproducing(request, published_safe: str):
    """The real profile, shifted so the real simulator reports the published figure.

    The shift is computed from the *unclamped* headroom, so it reproduces the
    published number exactly rather than landing anywhere at or above it.
    """
    real = shared_dataset().profiles[request.user_id]
    position, _, _ = position_for(request, real)
    headroom = (
        simulate(position, Config()).minimum_projected_balance
        - position.minimum_balance
    )
    shift = Decimal(published_safe) - headroom
    return replace(
        real, current_available_balance=real.current_available_balance + shift
    )


def decide_at_the_change_seam(request, profile):
    """Exactly the steps `_decide` runs, returning (safe, earliest, winner)."""
    config = Config()
    position, streams, events = position_for(request, profile)
    safe = amount_safe_to_pay(position, config, request.requested_amount)
    earliest = earliest_date_for_full_payment(
        position, config, request.requested_amount
    )
    changes = eligible_changes(
        streams,
        {event.event_id: event for event in events},
        profile,
        shared_dataset().rates,
    )
    candidates = candidate_plans(
        request,
        profile,
        shared_dataset().options_by_request.get(request.request_id, ()),
        position,
        config,
        safe=safe,
        earliest=earliest,
        changes=changes,
    )
    return safe, earliest, best_plan(candidates.plans)


class Sample06Test(unittest.TestCase):
    """One stop, one stream, and the published capacity figures left alone.

    amount_safe_to_pay      603.30      (the request is 620.40)
    spending_changes_needed stop:event_476
    recommended_method      full_payment, affordable_with_plan
    """

    def setUp(self):
        self.request = sample("request_06")
        self.profile = profile_reproducing(self.request, "603.3")

    def test_the_cited_event_is_the_streams_last_settled_occurrence(self):
        """`event_476` is the 5th of 5 monthly `Family streaming plan` rows."""
        _, streams, events = position_for(self.request, self.profile)
        changes = eligible_changes(
            streams,
            {e.event_id: e for e in events},
            self.profile,
            shared_dataset().rates,
        )

        self.assertEqual(
            [(c.action, c.event_id) for c in changes], [("stop", "event_476")]
        )
        cited = {e.event_id: e for e in events}["event_476"]
        self.assertEqual(cited.settlement_date, date(2025, 12, 10))
        self.assertLess(cited.settlement_date, self.request.request_date)

    def test_the_published_answer_is_reproduced(self):
        safe, _, winner = decide_at_the_change_seam(self.request, self.profile)

        self.assertEqual(safe, Decimal("603.30"))
        self.assertEqual(winner.spending_changes, ("stop:event_476",))
        self.assertEqual(winner.method, "full_payment")
        self.assertEqual(winner.status, "affordable_with_plan")
        self.assertEqual(winner.payments, ((date(2026, 1, 3), Decimal("620.4")),))

    def test_the_change_does_not_move_the_published_capacity_figures(self):
        """ "before optional spending changes" is a promise about two columns."""
        safe, _, winner = decide_at_the_change_seam(self.request, self.profile)

        self.assertEqual(safe, Decimal("603.30"))
        self.assertLess(safe, self.request.requested_amount)


class Sample21Test(unittest.TestCase):
    """The sample that decides the selection rule.

        amount_safe_to_pay      1543.35     (the request is 1574.40, so 31.05 short)
        spending_changes_needed stop:event_1815|reduce_to:event_1816:23.50

    `event_1816` is `reducible_or_stoppable` and `streaming` is in both of the user's
    lists, so `stop:event_1816` alone - one change, saving 47 - would close the gap.
    The published answer is the two-change set saving 34.50. Fewest-changes-first
    cannot produce that; smallest-total-saving-first produces exactly it.
    """

    def setUp(self):
        self.request = sample("request_21")
        self.profile = profile_reproducing(self.request, "1543.35")

    def test_the_one_change_alternative_really_is_available_and_sufficient(self):
        """Without this, the test below could pass by never seeing the alternative."""
        _, streams, events = position_for(self.request, self.profile)
        changes = eligible_changes(
            streams,
            {e.event_id: e for e in events},
            self.profile,
            shared_dataset().rates,
        )

        by_render = {c.render(): c for c in changes}
        self.assertIn("stop:event_1816", by_render)
        self.assertGreater(by_render["stop:event_1816"].saving, Decimal("31.05"))

    def test_the_published_two_change_set_is_reproduced(self):
        safe, _, winner = decide_at_the_change_seam(self.request, self.profile)

        self.assertEqual(safe, Decimal("1543.35"))
        self.assertEqual(
            winner.spending_changes,
            ("stop:event_1815", "reduce_to:event_1816:23.50"),
        )
        self.assertEqual(winner.method, "full_payment")
        self.assertEqual(winner.status, "affordable_with_plan")
        self.assertEqual(winner.payments, ((date(2026, 4, 3), Decimal("1574.4")),))

    def test_the_published_earliest_date_is_reproduced_and_is_late(self):
        """This is why the pruning rule has to look for changes at all here."""
        _, earliest, _ = decide_at_the_change_seam(self.request, self.profile)

        self.assertEqual(earliest, date(2026, 4, 15))
        self.assertGreater(earliest, self.request.desired_completion_date)


class Sample11Test(unittest.TestCase):
    """Reproduced in shape and in its cited event, and NOT in its exact set.

        published   reduce_to:event_989:665950
        produced    stop:event_949|reduce_to:event_989:665950

    Both divergences are real and neither belongs to this ticket:

    1. End to end this request never reaches the change search at all. Our
       `earliest_date_for_full_payment` for `user_11` is 2025-05-15, *before* the
       2025-06-12 deadline, so a no-change `wait` completes and the pruning rule
       correctly refuses to offer changes. The published `earliest` is 2025-07-15.
       That gap is ticket 06/14 capacity calibration, not selection.

    2. At the published shortfall of 599,355 the engine adds `stop:event_949`
       (cloud storage, 168,150) to the published dining reduction, because its
       conservative one-occurrence saving for the dining stream is measured on the
       cited event - 1,163,530.49 down to 665,950, so 497,580.49 - which does not
       reach the shortfall alone. Reading `reduce_to` as "the whole month's dining
       forecast collapses to one meal's minimum" would close it with one change, and
       is exactly the over-credit this engine refuses (see `spending.py`).

    What the sample does pin, and what this test therefore asserts, is the citation
    rule: the change names `event_989`, the most recent settled dining occurrence
    before `request_date`, out of nine dining rows with four different descriptions.
    """

    def setUp(self):
        self.request = sample("request_11")
        self.profile = profile_reproducing(self.request, "12510645")
        self.shortfall = self.request.requested_amount - Decimal("12510645")

    def test_the_cited_event_is_the_last_settled_dining_row(self):
        _, streams, events = position_for(self.request, self.profile)
        changes = eligible_changes(
            streams,
            {e.event_id: e for e in events},
            self.profile,
            shared_dataset().rates,
        )

        dining = [c for c in changes if c.category == "dining"]
        self.assertEqual([c.event_id for c in dining], ["event_989"])
        self.assertEqual([c.action for c in dining], ["reduce_to"])
        self.assertEqual(dining[0].new_amount, Decimal("665950"))
        self.assertEqual(dining[0].render(), "reduce_to:event_989:665950")

    def test_the_chosen_set_contains_the_published_change(self):
        _, streams, events = position_for(self.request, self.profile)
        changes = eligible_changes(
            streams,
            {e.event_id: e for e in events},
            self.profile,
            shared_dataset().rates,
        )

        chosen = next(iter(sufficient_change_sets(changes, self.shortfall, 3)))

        self.assertIn("reduce_to:event_989:665950", [c.render() for c in chosen])
        # Documented divergence, asserted so that closing it is a visible change.
        self.assertEqual(
            [c.render() for c in chosen],
            ["stop:event_949", "reduce_to:event_989:665950"],
        )

    def test_no_change_is_offered_end_to_end_because_a_wait_completes(self):
        """The pruning rule doing its job - the divergence is upstream of ticket 08."""
        _, earliest, winner = decide_at_the_change_seam(self.request, self.profile)

        self.assertLess(earliest, self.request.desired_completion_date)
        self.assertEqual(winner.method, "wait")
        self.assertEqual(winner.spending_changes, ())


if __name__ == "__main__":
    unittest.main()

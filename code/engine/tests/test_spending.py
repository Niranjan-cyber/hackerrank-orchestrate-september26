"""Spending-change eligibility, selection and application (Ticket 08).

Three seams are tested here, each independently:

  eligibility   which (event, action) pairs the user has actually permitted
  selection     which *set* of permitted changes is chosen for a given shortfall
  application   what a chosen set does to the projected cash position

The sample reproductions live in `test_sample_changes.py`; this file pins the rules
those samples are instances of, with hand-built events so each rule can fail alone.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from engine.cash import cash_position
from engine.recurrence import detect_streams, with_projections
from engine.spending import (
    SpendingChange,
    apply_changes,
    eligible_changes,
    render_changes,
    sufficient_change_sets,
)
from engine.types import Config

from .support import make_event, make_profile, make_request


def monthly(
    prefix: str,
    *,
    category: str,
    flexibility: str,
    amount: str,
    minimum: str | None = None,
    months: tuple[str, ...] = ("2024-11", "2024-12", "2025-01"),
    day: str = "10",
    description: str | None = None,
):
    """A settled monthly debit history - the shape recurrence detection needs."""
    return tuple(
        make_event(
            event_id=f"event_{prefix}_{index}",
            category=category,
            flexibility=flexibility,
            amount=amount,
            minimum_allowed_amount=minimum,
            description=description or f"{category} commitment",
            event_date=f"{month}-{day}",
            settlement_date=f"{month}-{day}",
        )
        for index, month in enumerate(months)
    )


def changes_for(events, profile, request=None, config=None):
    """Run the real detection and eligibility path for a hand-built history."""
    request = request or make_request(request_date="2025-02-01")
    config = config or Config()
    position = cash_position(request, profile, events, {})
    streams = detect_streams(position, events, request, profile, config, {})
    return eligible_changes(
        streams, {event.event_id: event for event in events}, profile, {}
    )


class EligibilityTest(unittest.TestCase):
    """Only events the user has actually permitted may be changed."""

    def test_a_stoppable_event_in_the_stop_list_may_be_stopped(self):
        events = monthly(
            "s", category="streaming", flexibility="stoppable", amount="19"
        )
        profile = make_profile(stoppable_categories=("streaming",))

        changes = changes_for(events, profile)

        self.assertEqual([c.action for c in changes], ["stop"])
        self.assertEqual(changes[0].saving, Decimal("19"))

    def test_a_fixed_event_is_never_changeable(self):
        events = monthly("f", category="streaming", flexibility="fixed", amount="19")
        profile = make_profile(
            stoppable_categories=("streaming",), reducible_categories=("streaming",)
        )

        self.assertEqual(changes_for(events, profile), ())

    def test_stop_needs_the_category_in_the_stop_list_not_the_reduce_list(self):
        events = monthly(
            "s", category="streaming", flexibility="stoppable", amount="19"
        )
        profile = make_profile(reducible_categories=("streaming",))

        self.assertEqual(changes_for(events, profile), ())

    def test_reduce_needs_the_category_in_the_reduce_list(self):
        events = monthly(
            "r", category="dining", flexibility="reducible", amount="80", minimum="30"
        )
        permitted = make_profile(reducible_categories=("dining",))
        refused = make_profile(stoppable_categories=("dining",))

        self.assertEqual(
            [c.action for c in changes_for(events, permitted)], ["reduce_to"]
        )
        self.assertEqual(changes_for(events, refused), ())

    def test_a_protected_category_is_never_changeable_even_when_listed(self):
        events = monthly("p", category="rent", flexibility="stoppable", amount="500")
        profile = make_profile(
            stoppable_categories=("rent",), protected_categories=("rent",)
        )

        self.assertEqual(changes_for(events, profile), ())

    def test_a_reducible_or_stoppable_event_offers_both_actions(self):
        events = monthly(
            "b",
            category="streaming",
            flexibility="reducible_or_stoppable",
            amount="47",
            minimum="23.50",
        )
        profile = make_profile(
            stoppable_categories=("streaming",), reducible_categories=("streaming",)
        )

        changes = changes_for(events, profile)

        self.assertEqual(
            sorted((c.action, c.saving) for c in changes),
            [("reduce_to", Decimal("23.50")), ("stop", Decimal("47"))],
        )

    def test_reduce_needs_a_minimum_allowed_amount(self):
        """Without the column there is no target, and inventing one invents a fact."""
        events = monthly(
            "n", category="dining", flexibility="reducible", amount="80", minimum=None
        )
        profile = make_profile(reducible_categories=("dining",))

        self.assertEqual(changes_for(events, profile), ())

    def test_reduce_is_dropped_when_the_minimum_is_not_below_the_amount(self):
        """A `reduce_to` that saves nothing is not a change."""
        events = monthly(
            "z", category="dining", flexibility="reducible", amount="80", minimum="80"
        )
        profile = make_profile(reducible_categories=("dining",))

        self.assertEqual(changes_for(events, profile), ())

    def test_an_event_outside_a_detected_stream_is_not_changeable(self):
        """Two occurrences is below `min_occurrences`, so no stream, so no change."""
        events = monthly(
            "o",
            category="streaming",
            flexibility="stoppable",
            amount="19",
            months=("2024-12", "2025-01"),
        )
        profile = make_profile(stoppable_categories=("streaming",))

        self.assertEqual(changes_for(events, profile), ())

    def test_the_cited_event_is_the_most_recent_settled_occurrence(self):
        events = monthly(
            "c",
            category="streaming",
            flexibility="stoppable",
            amount="19",
            months=("2024-11", "2024-12", "2025-01"),
        )
        profile = make_profile(stoppable_categories=("streaming",))

        changes = changes_for(events, profile)

        self.assertEqual(changes[0].event_id, "event_c_2")  # the 2025-01 occurrence

    def test_reduce_to_targets_the_minimum_allowed_amount_exactly(self):
        events = monthly(
            "t", category="dining", flexibility="reducible", amount="80", minimum="30.5"
        )
        profile = make_profile(reducible_categories=("dining",))

        change = changes_for(events, profile)[0]

        self.assertEqual(change.new_amount, Decimal("30.5"))
        self.assertEqual(change.saving, Decimal("49.5"))
        self.assertEqual(change.render(), "reduce_to:event_t_2:30.50")

    def test_an_income_stream_is_never_a_spending_change(self):
        events = tuple(
            make_event(
                event_id=f"event_i_{index}",
                event_type="income",
                direction="credit",
                category="salary",
                flexibility="stoppable",
                amount="5000",
                event_date=f"{month}-10",
                settlement_date=f"{month}-10",
            )
            for index, month in enumerate(("2024-11", "2024-12", "2025-01"))
        )
        profile = make_profile(stoppable_categories=("salary",))

        self.assertEqual(changes_for(events, profile), ())


def change(event_id: str, action: str, saving: str, new_amount: str | None = None):
    return SpendingChange(
        action=action,
        event_id=event_id,
        category="streaming",
        description="a commitment",
        saving=Decimal(saving),
        new_amount=None if new_amount is None else Decimal(new_amount),
    )


class SelectionTest(unittest.TestCase):
    """The smallest sufficient set, in the order the samples establish."""

    def test_the_smallest_sufficient_total_saving_comes_first(self):
        """Sample 21's rule: two small changes beat one that over-saves.

        `stop:event_02` alone (47) would close the 31.05 gap with *one* change, but
        `stop:event_01` + `reduce_to:event_02` (34.50) asks the user to give up less,
        and that is what the sample chose.
        """
        candidates = (
            change("event_01", "stop", "11"),
            change("event_02", "reduce_to", "23.50", "23.50"),
            change("event_02", "stop", "47"),
        )

        sets = list(sufficient_change_sets(candidates, Decimal("31.05"), 3))

        self.assertEqual(
            [c.render() for c in sets[0]],
            ["stop:event_01", "reduce_to:event_02:23.50"],
        )

    def test_fewer_changes_breaks_a_tie_on_total_saving(self):
        candidates = (
            change("event_01", "stop", "40"),
            change("event_02", "stop", "20"),
            change("event_03", "stop", "20"),
        )

        sets = list(sufficient_change_sets(candidates, Decimal("40"), 3))

        self.assertEqual([c.event_id for c in sets[0]], ["event_01"])

    def test_the_lowest_event_ids_break_a_tie_on_saving_and_count(self):
        candidates = (
            change("event_03", "stop", "40"),
            change("event_01", "stop", "40"),
            change("event_02", "stop", "40"),
        )

        sets = list(sufficient_change_sets(candidates, Decimal("40"), 3))

        self.assertEqual([c.event_id for c in sets[0]], ["event_01"])

    def test_a_set_never_stops_and_reduces_the_same_event(self):
        candidates = (
            change("event_01", "stop", "47"),
            change("event_01", "reduce_to", "23.50", "23.50"),
        )

        for chosen in sufficient_change_sets(candidates, Decimal("1"), 3):
            self.assertEqual(len({c.event_id for c in chosen}), len(chosen))

    def test_no_set_exceeds_the_configured_maximum(self):
        candidates = tuple(
            change(f"event_{index:02d}", "stop", "10") for index in range(6)
        )

        sets = list(sufficient_change_sets(candidates, Decimal("5"), 3))

        self.assertTrue(sets)
        self.assertTrue(all(len(chosen) <= 3 for chosen in sets))

    def test_an_insufficient_total_is_never_offered(self):
        candidates = (
            change("event_01", "stop", "10"),
            change("event_02", "stop", "10"),
            change("event_03", "stop", "10"),
        )

        self.assertEqual(list(sufficient_change_sets(candidates, Decimal("31"), 3)), [])

    def test_stop_entries_precede_reduce_entries_in_the_output(self):
        chosen = (
            change("event_09", "reduce_to", "5", "5"),
            change("event_01", "stop", "11"),
        )

        self.assertEqual(
            render_changes(chosen), ("stop:event_01", "reduce_to:event_09:5")
        )


class ApplicationTest(unittest.TestCase):
    """A chosen set changes the forecast, and nothing else."""

    def setUp(self):
        self.request = make_request(request_date="2025-02-01")
        self.profile = make_profile(
            stoppable_categories=("streaming",), reducible_categories=("dining",)
        )
        self.events = monthly(
            "s", category="streaming", flexibility="stoppable", amount="19"
        ) + monthly(
            "d",
            category="dining",
            flexibility="reducible",
            amount="80",
            minimum="30",
            day="20",
        )
        self.config = Config()
        position = cash_position(self.request, self.profile, self.events, {})
        self.position = with_projections(
            position, self.events, self.request, self.profile, self.config, {}
        )

    def _projected(self, position, source_event_id: str):
        return tuple(
            effect
            for effect in position.projected_debits
            if effect.source_event_id == source_event_id
        )

    def test_stopping_removes_every_projected_occurrence_of_the_stream(self):
        stop = change("event_s_2", "stop", "19")

        changed = apply_changes(self.position, (stop,))

        self.assertTrue(self._projected(self.position, "event_s_2"))
        self.assertEqual(self._projected(changed, "event_s_2"), ())

    def test_reducing_lowers_every_projected_occurrence_by_the_saving(self):
        reduce = change("event_d_2", "reduce_to", "50", "30")

        changed = apply_changes(self.position, (reduce,))

        before = self._projected(self.position, "event_d_2")
        after = self._projected(changed, "event_d_2")
        self.assertEqual(len(before), len(after))
        self.assertTrue(before)
        for original, reduced in zip(before, after):
            self.assertEqual(reduced.amount_home, original.amount_home - Decimal("50"))

    def test_other_streams_are_untouched(self):
        stop = change("event_s_2", "stop", "19")

        changed = apply_changes(self.position, (stop,))

        self.assertEqual(
            self._projected(changed, "event_d_2"),
            self._projected(self.position, "event_d_2"),
        )

    def test_a_saving_never_turns_a_debit_into_a_credit(self):
        """An over-large saving zeroes the occurrence; it never pays the user."""
        reduce = change("event_d_2", "reduce_to", "1000", "0")

        changed = apply_changes(self.position, (reduce,))

        self.assertEqual(self._projected(changed, "event_d_2"), ())

    def test_the_opening_balance_and_floor_are_unchanged(self):
        changed = apply_changes(self.position, (change("event_s_2", "stop", "19"),))

        self.assertEqual(changed.opening_balance, self.position.opening_balance)
        self.assertEqual(changed.minimum_balance, self.position.minimum_balance)
        self.assertEqual(changed.projected_until, self.position.projected_until)


if __name__ == "__main__":
    unittest.main()

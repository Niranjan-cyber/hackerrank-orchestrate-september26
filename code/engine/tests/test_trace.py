"""Ticket 09: reason codes, the explanation renderer, and the trace ledger.

Four things are pinned here that no earlier ticket's tests cover:

  * `unrealized` gets its own exclusion reason code, distinct from a genuine non-cash
    row (`cash.py`).
  * `decision_explanation` groups thousands; the graded `payment_plan` column form
    does not (`money.py`, `test_pipeline_decide.py` pins the sentence shape itself).
  * `pipeline.trace_request` returns the exact ledger a plan was certified against,
    including the day a floor breach happens when nothing was safe.
  * the rendered table and the JSON payload never touch `output.csv` and never carry
    a `Fact` field.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal

from engine.cash import (
    NON_CASH_IGNORED,
    UNREALIZED_VALUATION_EXCLUDED,
    classify_event,
)
from engine.money import format_explanation_amount, format_plan_amount
from engine.pipeline import trace_request
from engine.trace import ledger_rows, render_ledger, trace_payload
from engine.types import Config, Dataset, Reason

from .support import make_event, make_profile, make_request


def _dataset(request, profile, events=()):
    return Dataset(
        requests=(request,),
        profiles={profile.user_id: profile},
        events_by_user={profile.user_id: tuple(events)},
        options_by_request={request.request_id: ()},
        rates={},
    )


class UnrealizedValuationReasonCodeTest(unittest.TestCase):
    def test_unrealized_status_gets_its_own_code(self):
        valuation = make_event(
            status="unrealized",
            direction="non_cash",
            event_type="investment_valuation",
            amount="500000",
        )
        effect = classify_event(
            valuation, request_date=date(2025, 1, 1), home_currency="INR", rates={}
        )
        self.assertEqual(effect.reason_code, UNREALIZED_VALUATION_EXCLUDED)

    def test_a_non_cash_row_that_is_not_unrealized_keeps_the_other_code(self):
        event = make_event(
            status="settled", direction="non_cash", event_type="investment_valuation"
        )
        effect = classify_event(
            event, request_date=date(2025, 1, 1), home_currency="INR", rates={}
        )
        self.assertEqual(effect.reason_code, NON_CASH_IGNORED)


class FormatExplanationAmountTest(unittest.TestCase):
    def test_groups_thousands(self):
        self.assertEqual(format_explanation_amount(Decimal("25256")), "25,256")

    def test_groups_millions_and_keeps_two_decimals(self):
        self.assertEqual(
            format_explanation_amount(Decimal("15952906.67")), "15,952,906.67"
        )

    def test_sub_thousand_amount_is_unaffected(self):
        self.assertEqual(format_explanation_amount(Decimal("620.40")), "620.40")

    def test_disagrees_with_the_graded_column_formatter_above_a_thousand(self):
        # payment_plan / reduce_to must never gain a separator - this pins that the
        # two formatters give different answers so a future refactor cannot quietly
        # merge them and break the graded column.
        self.assertEqual(format_plan_amount(Decimal("25256")), "25256")
        self.assertEqual(format_explanation_amount(Decimal("25256")), "25,256")


class TraceRequestTest(unittest.TestCase):
    def test_unknown_request_id_raises(self):
        request = make_request()
        profile = make_profile()
        dataset = _dataset(request, profile)
        with self.assertRaises(ValueError):
            trace_request("does_not_exist", dataset, (), Config())

    def test_full_payment_ledger_carries_the_plan_payment_itself(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="1000",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="5000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        dataset = _dataset(request, profile)

        trace = trace_request(request.request_id, dataset, (), Config())

        self.assertEqual(trace.row.recommended_payment_method, "full_payment")
        self.assertTrue(trace.ledger.holds_floor)
        self.assertTrue(
            any(
                step.event_id.startswith("plan:payment:")
                and step.amount == Decimal("-1000")
                for step in trace.ledger.steps
            )
        )

    def test_a_breach_is_marked_on_the_day_it_happens_and_nowhere_else(self):
        """Nothing is accepted, so no plan is built - the trace falls back to the
        base position, which is exactly where an unaffordable request's floor
        breach actually lives."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="100",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="1000", minimum="900", payment_methods_considered=()
        )
        debit = make_event(
            event_id="event_big_bill",
            status="scheduled",
            direction="debit",
            amount="500",
            settlement_date="2025-02-15",
        )
        dataset = _dataset(request, profile, events=(debit,))

        trace = trace_request(request.request_id, dataset, (), Config())

        self.assertEqual(trace.row.recommended_payment_method, "not_recommended")
        rows = ledger_rows(trace.ledger)
        breaches = [row for row in rows if row["breach"]]
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0]["date"], "2025-02-15")
        self.assertEqual(breaches[0]["event"], "event_big_bill")

    def test_an_unresolved_future_amount_degrades_instead_of_crashing(self):
        """No candidate certifies (the blank amount forces `safe=ZERO`), so the
        fallback ledger is the base position's - and that position still contains
        the very effect that made the row degrade in the first place. The trace is
        a diagnostic, not a safety certification, so it drops that one row rather
        than raising `UnresolvedAmountError` the way `simulate` correctly does for
        every safety-critical caller in `plans.py`/`simulate.py` itself."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="100",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="1000", minimum="900", payment_methods_considered=()
        )
        blank = make_event(
            event_id="event_blank",
            status="scheduled",
            direction="debit",
            amount=None,
            settlement_date="2025-02-15",
        )
        dataset = _dataset(request, profile, events=(blank,))

        trace = trace_request(request.request_id, dataset, (), Config())

        self.assertEqual(trace.row.amount_safe_to_pay, Decimal("0"))
        self.assertTrue(
            any(
                r.code == "FORECAST_INCOMPLETE_UNRESOLVED_AMOUNT"
                for r in trace.row.reasons
            )
        )
        self.assertFalse(
            any(step.event_id == "event_blank" for step in trace.ledger.steps)
        )


class RenderLedgerTest(unittest.TestCase):
    def test_table_has_the_required_columns_and_marks_the_breach_row(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="100",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="1000", minimum="900", payment_methods_considered=()
        )
        debit = make_event(
            event_id="event_big_bill",
            status="scheduled",
            direction="debit",
            amount="500",
            settlement_date="2025-02-15",
        )
        dataset = _dataset(request, profile, events=(debit,))
        trace = trace_request(request.request_id, dataset, (), Config())

        text = render_ledger(trace.ledger)

        for column in ("DATE", "EVENT", "AMOUNT", "BALANCE", "FLOOR", "BREACH"):
            self.assertIn(column, text)
        event_line = next(
            line for line in text.splitlines() if "event_big_bill" in line
        )
        self.assertIn("BREACH", event_line)


class TracePayloadTest(unittest.TestCase):
    def test_payload_is_json_safe_and_matches_the_ledger(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="1000",
            desired_completion_date="2025-02-10",
        )
        profile = make_profile(
            balance="5000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        dataset = _dataset(request, profile)
        trace = trace_request(request.request_id, dataset, (), Config())

        payload = trace_payload(request.request_id, trace.ledger, trace.row.reasons)
        json.dumps(payload)  # never raises: every value is a JSON primitive

        self.assertEqual(payload["request_id"], request.request_id)
        self.assertEqual(len(payload["ledger"]), len(trace.ledger.steps))
        self.assertEqual(payload["minimum_balance"], "2000")

    def test_a_reason_has_no_field_a_facts_verbatim_text_could_reach(self):
        """Pins the invariant at the type level: `Reason` carries no free-text field
        wide enough to smuggle a `Fact.verbatim_quote` into a trace or explanation."""
        self.assertNotIn("verbatim_quote", Reason.__dataclass_fields__)
        self.assertNotIn("verbatim_amount_string", Reason.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()

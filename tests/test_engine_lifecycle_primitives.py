"""Focused primitives: lifecycle resolution (Ticket 13).

All seven linked-event patterns, the disputed-duplicate reservation, and
internal-transfer netting. Everything here asserts a `CashEffect.state` / `reason_code`
or the set of effects a transfer fact nets out - observable cash treatment, never that a
function was called.

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

from engine.cash import (  # noqa: E402
    CANCELLED_IGNORED,
    DISPUTED_DUPLICATE_RESERVED,
    EXCLUDED,
    FAILED_IGNORED,
    IN_OPENING_BALANCE,
    PENDING_DEBIT_RESERVED,
    RESERVED_DEBIT,
    SCHEDULED_DEBIT_RESERVED,
    UNREALIZED_VALUATION_EXCLUDED,
    UNSETTLED_CREDIT_NOT_COUNTED,
    cash_position,
    classify_event,
)
from engine.evidence import (  # noqa: E402
    EVIDENCE_AUTHORITY_DOWNGRADE,
    EVIDENCE_INTERNAL_TRANSFER_NETTED,
    EVIDENCE_TARGET_UNRESOLVED,
    apply_evidence,
)
from engine.types import Config, Fact  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_position,
    make_event,
    make_profile,
    make_request,
)

REQUEST_DATE = date(2025, 2, 1)


def state_of(event, request_date=REQUEST_DATE):
    return classify_event(
        event, request_date=request_date, home_currency="INR", rates={}
    )


class LinkedEventLifecycleTest(unittest.TestCase):
    """One test per documented `linked_event_id` pattern, with concrete parent/child."""

    def test_settled_expense_to_settled_refund_counts_both(self):
        parent = make_event(
            event_id="parent", settlement_date="2025-01-10", linked_event_id="child"
        )
        child = make_event(
            event_id="child",
            direction="credit",
            event_type="refund",
            amount="100",
            settlement_date="2025-01-20",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, IN_OPENING_BALANCE)
        self.assertEqual(state_of(child).state, IN_OPENING_BALANCE)

    def test_settled_expense_to_pending_refund_counts_only_the_parent(self):
        parent = make_event(
            event_id="parent", settlement_date="2025-01-10", linked_event_id="child"
        )
        child = make_event(
            event_id="child",
            direction="credit",
            event_type="refund",
            status="pending",
            settlement_date="2025-02-10",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, IN_OPENING_BALANCE)
        child_effect = state_of(child)
        self.assertEqual(child_effect.state, EXCLUDED)
        self.assertEqual(child_effect.reason_code, UNSETTLED_CREDIT_NOT_COUNTED)

    def test_cancelled_authorization_counts_only_its_settled_child(self):
        parent = make_event(
            event_id="parent",
            status="cancelled",
            settlement_date="2025-01-05",
            linked_event_id="child",
        )
        child = make_event(
            event_id="child", settlement_date="2025-01-12", linked_event_id="parent"
        )
        self.assertEqual(state_of(parent).state, EXCLUDED)
        self.assertEqual(state_of(parent).reason_code, CANCELLED_IGNORED)
        self.assertEqual(state_of(child).state, IN_OPENING_BALANCE)

    def test_failed_payment_counts_only_the_scheduled_retry(self):
        parent = make_event(
            event_id="parent",
            event_type="debt_payment",
            status="failed",
            settlement_date="2025-01-20",
            linked_event_id="child",
        )
        child = make_event(
            event_id="child",
            event_type="debt_payment",
            status="scheduled",
            settlement_date="2025-02-20",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, EXCLUDED)
        self.assertEqual(state_of(parent).reason_code, FAILED_IGNORED)
        child_effect = state_of(child)
        self.assertEqual(child_effect.state, RESERVED_DEBIT)
        self.assertEqual(child_effect.reason_code, SCHEDULED_DEBIT_RESERVED)

    def test_investment_valuation_is_never_available_cash(self):
        parent = make_event(
            event_id="parent",
            event_type="investment_purchase",
            settlement_date="2025-01-05",
            linked_event_id="child",
        )
        child = make_event(
            event_id="child",
            event_type="investment_valuation",
            direction="non_cash",
            status="unrealized",
            settlement_date="2025-01-05",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, IN_OPENING_BALANCE)
        child_effect = state_of(child)
        self.assertEqual(child_effect.state, EXCLUDED)
        self.assertEqual(child_effect.reason_code, UNREALIZED_VALUATION_EXCLUDED)

    def test_settled_investment_sale_counts_as_realized_cash(self):
        parent = make_event(
            event_id="parent",
            event_type="investment_purchase",
            settlement_date="2025-01-05",
            linked_event_id="child",
        )
        child = make_event(
            event_id="child",
            event_type="investment_sale",
            direction="credit",
            settlement_date="2025-01-25",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, IN_OPENING_BALANCE)
        self.assertEqual(state_of(child).state, IN_OPENING_BALANCE)

    def test_disputed_duplicate_child_is_reserved(self):
        parent = make_event(
            event_id="parent", settlement_date="2025-01-05", linked_event_id="child"
        )
        child = make_event(
            event_id="child",
            status="pending",
            description="Possible duplicate card charge",
            amount="150",
            settlement_date="2025-02-10",
            linked_event_id="parent",
        )
        self.assertEqual(state_of(parent).state, IN_OPENING_BALANCE)
        child_effect = state_of(child)
        self.assertEqual(child_effect.state, RESERVED_DEBIT)
        self.assertEqual(child_effect.reason_code, DISPUTED_DUPLICATE_RESERVED)

    def test_the_link_alone_never_changes_the_cash_treatment(self):
        """`status` and `direction` decide alone; the link is provenance, not a rule."""
        linked = make_event(
            event_id="child",
            status="pending",
            amount="150",
            settlement_date="2025-02-10",
            linked_event_id="parent",
        )
        unlinked = make_event(
            event_id="child",
            status="pending",
            amount="150",
            settlement_date="2025-02-10",
        )
        self.assertEqual(state_of(linked), state_of(unlinked))


class DisputedDuplicateTest(unittest.TestCase):
    def test_a_possible_duplicate_pending_debit_is_reserved_not_ignored(self):
        event = make_event(
            event_id="dup",
            status="pending",
            description="Possible duplicate card charge",
            amount="150",
            settlement_date="2025-02-10",
        )
        position = cash_position(
            make_request(request_date="2025-02-01"),
            make_profile(balance="1000", minimum="100"),
            (event,),
            {},
        )

        self.assertEqual([e.event_id for e in position.reserved_debits], ["dup"])
        self.assertEqual(position.balance_on(date(2025, 3, 1)), Decimal("850"))

    def test_an_ordinary_pending_debit_uses_the_plain_reason(self):
        event = make_event(
            event_id="normal",
            status="pending",
            description="Card purchase",
            amount="150",
            settlement_date="2025-02-10",
        )
        self.assertEqual(state_of(event).reason_code, PENDING_DEBIT_RESERVED)


class InternalTransferNettingTest(unittest.TestCase):
    """The matching debit/credit pair is the true de-duplication case (CONTEXT D9)."""

    def _position(self, credit_date="2025-02-12"):
        events = (
            make_event(
                event_id="event_out",
                status="scheduled",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-10",
                linked_event_id="event_in",
            ),
            make_event(
                event_id="event_in",
                event_type="income",
                status="scheduled",
                direction="credit",
                amount="1500",
                settlement_date=credit_date,
                linked_event_id="event_out",
            ),
        )
        return events, build_position(events)

    def _transfer_fact(self):
        return Fact(
            fact_type="internal_transfer",
            subject="message_transfer",
            user_id="user_test",
            related_event_id="event_out",
            verbatim_quote="a transfer between your two accounts",
            source_type="bank",
        )

    def test_future_legs_of_a_transfer_net_to_zero_but_stay_in_the_forecast(self):
        _, position = self._position()

        self.assertEqual(position.net_forecast_change, Decimal("0"))
        self.assertEqual(len(position.reserved_debits), 1)
        self.assertEqual(len(position.expected_credits), 1)

    def test_same_date_matched_legs_are_netted_out_by_a_transfer_fact(self):
        events, position = self._position(credit_date="2025-02-10")
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000", minimum="2000")

        applied = apply_evidence(
            position, (self._transfer_fact(),), request, profile, Config(), events, {}
        )

        self.assertEqual(applied.position.effects, ())
        self.assertIn(
            EVIDENCE_INTERNAL_TRANSFER_NETTED, {r.code for r in applied.reasons}
        )

    def test_netting_is_refused_when_it_would_free_cash_before_the_credit_lands(self):
        """D31: removing the early debit leg alone would lift the window minimum.

        The transfer fact is therefore downgraded and both legs stay in the forecast -
        the conservative reading, not a silently optimistic net.
        """
        events, position = self._position(credit_date="2025-02-12")
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000", minimum="2000")

        applied = apply_evidence(
            position, (self._transfer_fact(),), request, profile, Config(), events, {}
        )

        self.assertEqual(
            {e.event_id for e in applied.position.effects}, {"event_out", "event_in"}
        )
        self.assertIn(EVIDENCE_AUTHORITY_DOWNGRADE, {r.code for r in applied.reasons})

    def test_a_same_direction_link_is_not_netted(self):
        """A lifecycle link (failed -> retry) is not a transfer pair; dropping the
        debit leg alone would hand back headroom the user does not have."""
        events = (
            make_event(
                event_id="event_out",
                status="scheduled",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-10",
                linked_event_id="event_other_debit",
            ),
            make_event(
                event_id="event_other_debit",
                status="scheduled",
                direction="debit",
                amount="1500",
                settlement_date="2025-02-12",
                linked_event_id="event_out",
            ),
        )
        position = build_position(events)
        request = make_request(request_date="2025-02-01")
        profile = make_profile(balance="10000", minimum="2000")

        applied = apply_evidence(
            position, (self._transfer_fact(),), request, profile, Config(), events, {}
        )

        self.assertEqual(
            {e.event_id for e in applied.position.effects},
            {effect.event_id for effect in position.effects},
        )
        self.assertIn(EVIDENCE_TARGET_UNRESOLVED, {r.code for r in applied.reasons})


if __name__ == "__main__":
    unittest.main()

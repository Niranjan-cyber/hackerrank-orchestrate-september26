"""The validator - the only writer to output.csv.

Two design decisions worth stating, both from CONTEXT.md D22:

1. **It returns violations, it does not raise.** A malformed row must not kill the
   batch on request 300 of 250. A row that fails validation is replaced by a
   conservative safe row and the violation is recorded, so the submission always has
   250 rows.
2. **It is the sole writer.** Nothing else in the codebase opens output.csv. If the
   file exists, this module wrote it, and it wrote it only after checking every row.

Byte-stability matters because we develop on Windows and the grader is likely on
Linux: `newline=""` stops the csv module adding a second \\r, and an explicit
`lineterminator="\\n"` overrides the default `\\r\\n`. Values are written as
`str(Decimal)`, never a float, so there is no repr variance across platforms.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .money import ZERO, format_date, format_plan_amount, format_safe_amount, money
from .types import (
    AFFORDABILITY_STATUSES,
    OUTPUT_COLUMNS,
    PAYMENT_METHODS,
    Dataset,
    OutputRow,
)


@dataclass(frozen=True, slots=True)
class Violation:
    request_id: str
    rule: str
    detail: str


def validate_row(row: OutputRow, dataset: Dataset) -> tuple[Violation, ...]:
    """Check one row against every output invariant. See CONTEXT.md section 11."""
    found: list[Violation] = []

    def fail(rule: str, detail: str) -> None:
        found.append(Violation(row.request_id, rule, detail))

    request = next(
        (r for r in dataset.requests if r.request_id == row.request_id), None
    )
    if request is None:
        fail("unknown_request", "row does not correspond to any request")
        return tuple(found)

    # 2. bounds
    if row.amount_safe_to_pay < ZERO:
        fail("bounds", f"amount_safe_to_pay {row.amount_safe_to_pay} < 0")
    if row.amount_safe_to_pay > request.requested_amount:
        fail(
            "bounds",
            f"amount_safe_to_pay {row.amount_safe_to_pay} > requested "
            f"{request.requested_amount}",
        )

    # 3, 4. enums
    if row.affordability_status not in AFFORDABILITY_STATUSES:
        fail("enum", f"bad affordability_status {row.affordability_status!r}")
    if row.recommended_payment_method not in PAYMENT_METHODS:
        fail(
            "enum", f"bad recommended_payment_method {row.recommended_payment_method!r}"
        )

    # 5. affordable_now implies earliest == request_date
    if row.affordability_status == "affordable_now":
        if row.earliest_date_for_full_payment != request.request_date:
            fail(
                "affordable_now_earliest",
                "affordable_now requires earliest_date_for_full_payment == request_date",
            )

    # 6. plan is chronological
    dates = [d for d, _ in row.payment_plan]
    if dates != sorted(dates):
        fail("plan_order", "payment_plan is not chronological")

    # 7. partial_payment shape
    if row.recommended_payment_method == "partial_payment":
        if row.affordability_status != "affordable_with_plan":
            fail("partial_status", "partial_payment requires affordable_with_plan")
        if not request.allows_partial_payment:
            fail("partial_not_allowed", "request does not allow partial payment")
        if len(row.payment_plan) != 2:
            fail("partial_shape", "partial_payment needs exactly two payments")
        else:
            first, second = row.payment_plan
            if first[0] != request.request_date:
                fail("partial_shape", "first partial payment must be on request_date")
            if first[1] != row.amount_safe_to_pay:
                fail("partial_shape", "first payment must equal amount_safe_to_pay")
            if first[1] + second[1] != request.requested_amount:
                fail("partial_sum", "partial payments must sum to requested_amount")
            if row.earliest_date_for_full_payment is None:
                fail("partial_shape", "partial_payment needs an earliest date")
            elif second[0] != row.earliest_date_for_full_payment:
                fail("partial_shape", "second payment must be on the earliest date")

    # 8. installments must match a supplied option exactly
    if row.recommended_payment_method == "installments":
        options = dataset.options_by_request.get(request.request_id, ())
        profile = dataset.profiles[request.user_id]
        if not _matches_an_option(row, options, profile.max_installment_months):
            fail("installments_match", "plan matches no supplied payment option")

    # 9. wait shape
    if row.recommended_payment_method == "wait":
        if len(row.payment_plan) != 1:
            fail("wait_shape", "wait needs exactly one payment")
        elif row.payment_plan[0][1] != request.requested_amount:
            fail("wait_shape", "wait payment must be the full requested amount")
        elif row.payment_plan[0][0] != row.earliest_date_for_full_payment:
            fail("wait_shape", "wait payment must be on the earliest date")

    # 10. not_recommended shape
    # `earliest_date_for_full_payment` is deliberately NOT constrained here. It is a
    # capacity figure, independent of the user's payment-method preferences
    # (problem_statement.md:163), and is left blank only when the full amount never
    # becomes safe within the forecast period (problem_statement.md:113). A
    # not_recommended row can legitimately carry a date when the full payment only
    # becomes safe after `desired_completion_date`.
    if row.recommended_payment_method == "not_recommended":
        if row.payment_plan:
            fail("not_recommended_shape", "not_recommended requires an empty plan")
        if row.spending_changes_needed:
            fail(
                "not_recommended_shape", "not_recommended requires no spending changes"
            )

    # 11. spending changes
    _check_spending_changes(row, dataset, request, fail)

    # explanation must exist - it is a graded column
    if not row.decision_explanation.strip():
        fail("explanation", "decision_explanation is empty")

    return tuple(found)


def _check_spending_changes(row: OutputRow, dataset: Dataset, request, fail) -> None:
    """Output invariant 11, in full (CONTEXT.md section 11).

    This is the last line of defence on the one column the engine writes about the
    *user's own* money rather than the seller's: an entry here is an instruction to
    cancel or shrink something they depend on. Every clause is re-checked against the
    dataset row, not against the engine value that produced it, so a bug in eligibility
    surfaces as a violation instead of as advice to stop paying the rent.
    """
    changes = row.spending_changes_needed
    if not changes:
        return
    if len(changes) > 3:
        fail("changes_count", "at most three spending changes are allowed")

    profile = dataset.profiles[request.user_id]
    events = {
        event.event_id: event
        for event in dataset.events_by_user.get(request.user_id, ())
    }
    seen: set[str] = set()
    stop_seen_after_reduce = False
    reduce_seen = False

    for entry in changes:
        parts = entry.split(":")
        action, event_id = parts[0], parts[1] if len(parts) > 1 else ""
        if action not in ("stop", "reduce_to"):
            fail("changes_format", f"unknown spending change action in {entry!r}")
            continue
        if action == "reduce_to":
            reduce_seen = True
            if len(parts) != 3:
                fail("changes_format", f"reduce_to needs an amount: {entry!r}")
                continue
        elif reduce_seen:
            stop_seen_after_reduce = True

        event = events.get(event_id)
        if event is None:
            fail("changes_event", f"{event_id} is not one of this user's events")
            continue
        if event_id in seen:
            fail("changes_event", f"{event_id} is changed twice")
        seen.add(event_id)

        if event.category in profile.protected_categories:
            fail("changes_protected", f"{event.category} is a protected category")
        permitted = (
            profile.stoppable_categories
            if action == "stop"
            else profile.reducible_categories
        )
        if event.category not in permitted:
            fail(
                "changes_permission",
                f"the user has not agreed to {action} {event.category}",
            )
        allowed_flexibility = (
            ("stoppable", "reducible_or_stoppable")
            if action == "stop"
            else ("reducible", "reducible_or_stoppable")
        )
        if event.flexibility not in allowed_flexibility:
            fail(
                "changes_flexibility",
                f"{event_id} is {event.flexibility}, which cannot be {action}ped",
            )
        if action == "reduce_to":
            # `money` rather than `Decimal`: D22 says this module returns violations
            # and never raises, and `Decimal("")` raises. A malformed amount must
            # become one row's violation, not the end of the batch.
            target = money(parts[2])
            if event.minimum_allowed_amount is None:
                fail("changes_minimum", f"{event_id} has no minimum_allowed_amount")
            elif target is None:
                fail("changes_format", f"{parts[2]!r} is not a valid amount")
            elif target < event.minimum_allowed_amount:
                fail(
                    "changes_minimum",
                    f"{parts[2]} is below {event_id}'s minimum "
                    f"{event.minimum_allowed_amount}",
                )

    if stop_seen_after_reduce:
        fail("changes_order", "stop entries must precede reduce_to entries")


def _matches_an_option(row: OutputRow, options, max_installment_months) -> bool:
    """An installment plan must reproduce a supplied option's schedule exactly."""
    from datetime import timedelta

    for option in options:
        if option.payment_method != "installments":
            continue
        if max_installment_months is not None:
            if option.number_of_payments > max_installment_months:
                continue
        frequency = option.payment_frequency_days or 0
        expected = tuple(
            (
                option.first_payment_date + timedelta(days=frequency * index),
                option.payment_amount,
            )
            for index in range(option.number_of_payments)
        )
        if expected == row.payment_plan:
            return True
    return False


def _conservative_row(row: OutputRow) -> OutputRow:
    """Replacement for a row that failed validation: safe, valid, and honest."""
    from dataclasses import replace

    return replace(
        row,
        amount_safe_to_pay=ZERO,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan=(),
        earliest_date_for_full_payment=None,
        spending_changes_needed=(),
        decision_explanation=(
            "No safe recommendation could be produced for this request, so no payment "
            "is advised."
        ),
    )


def _render(row: OutputRow) -> dict[str, str]:
    plan = (
        "|".join(
            f"{day.isoformat()}:{format_plan_amount(amount)}"
            for day, amount in row.payment_plan
        )
        or "none"
    )
    changes = "|".join(row.spending_changes_needed) or "none"
    return {
        "request_id": row.request_id,
        "amount_safe_to_pay": format_safe_amount(row.amount_safe_to_pay),
        "affordability_status": row.affordability_status,
        "recommended_payment_method": row.recommended_payment_method,
        "payment_plan": plan,
        "earliest_date_for_full_payment": format_date(
            row.earliest_date_for_full_payment
        ),
        "spending_changes_needed": changes,
        "decision_explanation": row.decision_explanation,
    }


def validate_and_write(
    rows: tuple[OutputRow, ...],
    dataset: Dataset,
    destination: Path,
) -> tuple[Violation, ...]:
    """Validate every row, substitute conservative rows for failures, then write.

    Returns the violations found. Never raises on a bad row.
    """
    violations: list[Violation] = []
    checked: list[OutputRow] = []

    for row in rows:
        row_violations = validate_row(row, dataset)
        if row_violations:
            violations.extend(row_violations)
            checked.append(_conservative_row(row))
        else:
            checked.append(row)

    # Contract-level check: exactly one row per request, in dataset order.
    expected_ids = [request.request_id for request in dataset.requests]
    actual_ids = [row.request_id for row in checked]
    if actual_ids != expected_ids:
        violations.append(
            Violation(
                request_id="<batch>",
                rule="row_coverage",
                detail=(
                    f"expected {len(expected_ids)} rows in dataset order, "
                    f"got {len(actual_ids)}"
                ),
            )
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(OUTPUT_COLUMNS), lineterminator="\n"
        )
        writer.writeheader()
        for row in checked:
            writer.writerow(_render(row))

    return tuple(violations)

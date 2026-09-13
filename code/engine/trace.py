"""Day-by-day ledger rendering and per-request trace payloads (Ticket 09).

Pure by construction: takes an already-simulated `Ledger` (Ticket 06) and the reasons
collected for one request (`pipeline.trace_request`), and renders them into a
human-readable table or a JSON-serialisable payload. No filesystem access lives here -
`code/main.py` is the only place a trace is written to disk, the same imperative-shell
boundary `pipeline.py` and `validate.py` already keep.

OFF THE output.csv PATH ON PURPOSE
`code/engine/validate.py` is the sole writer of `output.csv`, and it renders only
`types.OUTPUT_COLUMNS` off an `OutputRow`. Nothing here is imported by that module or
reachable from it, so a trace can be written, changed, or skipped entirely without
touching a single graded value.
"""

from __future__ import annotations

from typing import Sequence

from .money import format_explanation_amount
from .simulate import Ledger
from .types import Reason


def ledger_rows(ledger: Ledger) -> tuple[dict[str, str], ...]:
    """One row per applied movement, in ledger order.

    The single source both `render_ledger` and `trace_payload` format from, so the
    printed table and the written trace file can never disagree with each other.
    """
    return tuple(
        {
            "date": step.when.isoformat(),
            "event": step.event_id,
            "reason_code": step.reason_code,
            "amount": format_explanation_amount(step.amount),
            "balance": format_explanation_amount(step.balance),
            "floor": format_explanation_amount(ledger.minimum_balance),
            "breach": "BREACH" if step.balance < ledger.minimum_balance else "",
        }
        for step in ledger.steps
    )


_COLUMNS = ("date", "event", "reason_code", "amount", "balance", "floor", "breach")


def render_ledger(ledger: Ledger) -> str:
    """A human-readable day-by-day ledger: date, event, amount, balance, floor, breach.

    Column widths are computed from the actual rows so the table stays readable for
    both a three-movement request and one with a hundred, and `assertIn` in a test can
    match a whole row without pinning exact whitespace to a magic number.
    """
    rows = ledger_rows(ledger)
    header = {name: name.upper() for name in _COLUMNS}
    widths = {
        name: max(len(header[name]), *(len(row[name]) for row in rows))
        if rows
        else len(header[name])
        for name in _COLUMNS
    }

    def line(row: dict[str, str]) -> str:
        return "  ".join(row[name].ljust(widths[name]) for name in _COLUMNS)

    lines = [
        f"opening balance {format_explanation_amount(ledger.opening_balance)}  "
        f"floor {format_explanation_amount(ledger.minimum_balance)}  "
        f"window {ledger.request_date.isoformat()} -> {ledger.horizon_end.isoformat()}",
        line(header),
    ]
    lines.extend(line(row) for row in rows)
    if ledger.opening_breached:
        lines.append("NOTE  opening balance is already below the floor")
    elif not ledger.holds_floor:
        breach = ledger.first_breach
        when = breach.when.isoformat() if breach else "?"
        lines.append(f"NOTE  floor breached on {when}")
    return "\n".join(lines)


def trace_payload(request_id: str, ledger: Ledger, reasons: Sequence[Reason]) -> dict:
    """A JSON-serialisable trace for one request.

    Amounts are kept as exact decimal strings (never `float`), consistent with every
    other value this engine writes. `verbatim_quote` and every other `Fact` field are
    absent by construction - a `Reason` never carries one (`types.Reason`), so there is
    nothing extracted-text-shaped for this payload to leak.
    """
    return {
        "request_id": request_id,
        "window": {
            "start": ledger.request_date.isoformat(),
            "end": ledger.horizon_end.isoformat(),
        },
        "opening_balance": str(ledger.opening_balance),
        "minimum_balance": str(ledger.minimum_balance),
        "holds_floor": ledger.holds_floor,
        "minimum_projected_balance": str(ledger.minimum_projected_balance),
        "ledger": [
            {
                "date": step.when.isoformat(),
                "event_id": step.event_id,
                "kind": step.kind,
                "reason_code": step.reason_code,
                "amount": str(step.amount),
                "balance": str(step.balance),
                "breach": step.balance < ledger.minimum_balance,
            }
            for step in ledger.steps
        ],
        "reasons": [
            {
                "code": reason.code,
                "event_id": reason.event_id,
                "amount": None if reason.amount is None else str(reason.amount),
                "currency": reason.currency,
                "detail": reason.detail,
            }
            for reason in reasons
        ],
    }

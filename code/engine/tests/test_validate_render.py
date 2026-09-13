"""`rendered_rows` is the seam the calibration sweep scores against.

The sweep (ticket 14) must score exactly what the submission would contain, so the
rendering it reads has to be the same code path `validate_and_write` uses - including
the conservative substitution for a row that fails validation. These tests pin that
agreement; if the two ever diverge the sweep would be tuning against a fiction.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from engine.types import Dataset, OutputRow
from engine.validate import rendered_rows, validate_and_write

from .support import make_request


def _dataset(request) -> Dataset:
    return Dataset(
        requests=(request,),
        profiles={},
        events_by_user={},
        options_by_request={},
        rates={},
    )


def _row(**overrides) -> OutputRow:
    base = dict(
        request_id="request_test",
        amount_safe_to_pay=Decimal("100"),
        affordability_status="affordable_now",
        recommended_payment_method="full_payment",
        payment_plan=((date(2025, 2, 1), Decimal("100")),),
        earliest_date_for_full_payment=date(2025, 2, 1),
        spending_changes_needed=(),
        decision_explanation="ok",
    )
    base.update(overrides)
    return OutputRow(**base)


class RenderedRowsTest(unittest.TestCase):
    def test_rendered_rows_match_the_file_validate_and_write_produces(self):
        request = make_request(requested_amount="5000")
        dataset = _dataset(request)
        rows = (_row(),)

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "out.csv"
            validate_and_write(rows, dataset, destination)
            with destination.open(encoding="utf-8", newline="") as handle:
                from_file = list(csv.DictReader(handle))

        self.assertEqual(list(rendered_rows(rows, dataset)), from_file)

    def test_a_row_that_fails_validation_renders_conservatively_in_both(self):
        # amount_safe_to_pay above requested_amount breaks output invariant 2, so the
        # writer substitutes a conservative row. The sweep must see that same row.
        request = make_request(requested_amount="50")
        dataset = _dataset(request)
        rows = (_row(amount_safe_to_pay=Decimal("100")),)

        rendered = list(rendered_rows(rows, dataset))
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "out.csv"
            violations = validate_and_write(rows, dataset, destination)
            with destination.open(encoding="utf-8", newline="") as handle:
                from_file = list(csv.DictReader(handle))

        self.assertTrue(violations)
        self.assertEqual(rendered, from_file)
        self.assertEqual(rendered[0]["affordability_status"], "not_affordable")


if __name__ == "__main__":
    unittest.main()

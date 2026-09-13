"""Tests for the fixture document formats the adapter must read.

A recorded message fixture may carry several facts (contract section 5: a message can
state a regular salary figure *and* a one-time arrears adjustment). The legacy
single-fact shape from ticket 03 must still load.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.loaders import load_dataset  # noqa: E402
from extraction.fixture_adapter import FixtureExtractor  # noqa: E402


def _message_row(message_id: str) -> dict[str, str]:
    with (REPO_ROOT / "dataset" / "messages.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = {row["message_id"]: row for row in csv.DictReader(handle)}
    return rows[message_id]


def _fact(message_id: str, fact_type: str, **overrides) -> dict:
    row = _message_row(message_id)
    base = {
        "fact_type": fact_type,
        "subject": message_id,
        "user_id": row["user_id"],
        "request_id": None,
        "related_event_id": None,
        "amount": None,
        "currency": None,
        "percent_change": None,
        "effective_date": None,
        "applies_to_cycles": None,
        "verbatim_quote": "Rincian penggajian Anda di Cobalt Systems telah berubah.",
        "verbatim_amount_string": None,
        "source_type": row["source_type"],
        "source_language": "id",
        "confidence": "high",
        "extractor": {
            "provider": "groq",
            "model": "qwen/qwen3.8-27b",
            "fixture_key": "k",
        },
    }
    base.update(overrides)
    return base


class TestFixtureDocumentFormats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixtures = Path(self._tmp.name) / "fixtures"
        (self.fixtures / "message").mkdir(parents=True)
        self.dataset_dir = REPO_ROOT / "dataset"
        self.dataset = load_dataset(self.dataset_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, document) -> None:
        (self.fixtures / "message" / f"{name}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    def _extractor(self) -> FixtureExtractor:
        return FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
        )

    def test_facts_array_document_loads_every_fact(self):
        fact_a = _fact(
            "message_01",
            "salary_increase",
            amount="42750000",
            currency="IDR",
            effective_date="2025-08-15",
            verbatim_quote="Gaji bulanan Anda naik menjadi IDR 42750000.",
        )
        fact_b = _fact("message_01", "salary_confirmed_unchanged")
        self._write("recorded", {"facts": [fact_a, fact_b]})

        facts = self._extractor().facts_for_user("user_02")
        types = {f.fact_type for f in facts}
        self.assertEqual(types, {"salary_increase", "salary_confirmed_unchanged"})

    def test_legacy_single_fact_document_still_loads(self):
        self._write("legacy", _fact("message_01", "salary_confirmed_unchanged"))
        facts = self._extractor().facts_for_user("user_02")
        self.assertEqual([f.fact_type for f in facts], ["salary_confirmed_unchanged"])

    def test_malformed_fact_in_array_is_dropped_not_fatal(self):
        good = _fact("message_01", "salary_confirmed_unchanged")
        bad = {"fact_type": "salary_increase", "subject": "message_01"}  # missing user
        self._write("mixed", {"facts": [good, bad]})

        extractor = self._extractor()
        facts = extractor.facts_for_user("user_02")
        self.assertEqual([f.fact_type for f in facts], ["salary_confirmed_unchanged"])
        self.assertTrue(extractor.violations)


if __name__ == "__main__":
    unittest.main()

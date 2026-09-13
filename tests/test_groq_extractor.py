"""Tests for the recording Groq extractor.

The extractor turns one message into one model call, writes the result as a
content-addressed fixture, and validates it. Nothing here touches the network: the
model client is a fake.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.loaders import load_dataset  # noqa: E402
from extraction.groq_client import Completion, GroqError  # noqa: E402
from extraction.groq_extractor import GroqExtractor, RecordSummary  # noqa: E402
from extraction.usage_log import UsageLogger  # noqa: E402

FIXED_CLOCK = lambda: "2026-09-13T00:00:00+00:00"  # noqa: E731


def _all_messages() -> dict[str, dict[str, str]]:
    with (REPO_ROOT / "dataset" / "messages.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        return {row["message_id"]: row for row in csv.DictReader(handle)}


def _completion(facts: list[dict], *, input_tokens=100, output_tokens=50) -> Completion:
    return Completion(
        payload={"facts": facts},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        retries=0,
    )


def _valid_salary_fact() -> dict:
    return {
        "fact_type": "salary_increase",
        "amount": "42750000",
        "currency": "IDR",
        "percent_change": None,
        "effective_date": "2025-08-15",
        "applies_to_cycles": None,
        "verbatim_quote": "Gaji bulanan Anda naik menjadi IDR 42750000.",
        "source_language": "id",
        "confidence": "high",
    }


class FakeClient:
    def __init__(self, by_subject=None, default=None):
        self.by_subject = by_subject or {}
        self.default = default
        self.calls: list[str] = []

    def complete(self, messages, schema):
        record = messages[1]["content"].split("RECORD:\n", 1)[1]
        payload = json.loads(record)
        subject = payload["message_id"]
        self.calls.append(subject)
        result = self.by_subject.get(subject, self.default)
        if result is None:
            raise AssertionError(f"unexpected model call for {subject}")
        if isinstance(result, Exception):
            raise result
        return result


class ExtractorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixtures = Path(self._tmp.name) / "fixtures"
        self.log_path = Path(self._tmp.name) / "evaluation" / "usage_raw.jsonl"
        self.dataset_dir = REPO_ROOT / "dataset"
        self.dataset = load_dataset(self.dataset_dir)
        self.messages = _all_messages()

    def tearDown(self):
        self._tmp.cleanup()

    def _extractor(self, client: FakeClient, logger=None) -> GroqExtractor:
        return GroqExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
            client=client,
            logger=logger or UsageLogger(self.log_path, now=FIXED_CLOCK),
            now=FIXED_CLOCK,
        )

    def _log_records(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]


class TestRecordMessage(ExtractorCase):
    def test_records_fixture_and_returns_validated_fact(self):
        client = FakeClient({"message_01": _completion([_valid_salary_fact()])})
        extractor = self._extractor(client)

        self.assertTrue(extractor.record_message(self.messages["message_01"]))

        files = list((self.fixtures / "message").glob("*.json"))
        self.assertEqual(len(files), 1)
        document = json.loads(files[0].read_text(encoding="utf-8"))
        written = document["facts"][0]
        self.assertEqual(written["subject"], "message_01")
        self.assertEqual(written["user_id"], "user_02")
        self.assertEqual(written["source_type"], "employer")
        self.assertEqual(written["extractor"]["provider"], "groq")
        self.assertEqual(written["extractor"]["model"], "qwen/qwen3.8-27b")
        self.assertEqual(written["extractor"]["fixture_key"], files[0].stem)

        facts = extractor.facts_for_user("user_02")
        self.assertEqual([f.fact_type for f in facts], ["salary_increase"])
        self.assertEqual(facts[0].amount, Decimal("42750000"))

    def test_logs_one_call_with_tokens_and_cost(self):
        client = FakeClient({"message_01": _completion([_valid_salary_fact()])})
        self._extractor(client).record_message(self.messages["message_01"])

        calls = [r for r in self._log_records() if r["purpose"] == "message_fact"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["subject_id"], "message_01")
        self.assertEqual(calls[0]["input_tokens"], 100)
        self.assertEqual(calls[0]["output_tokens"], 50)
        self.assertIsNotNone(calls[0]["estimated_cost_usd"])
        self.assertIsNone(calls[0]["error"])

    def test_invalid_fact_is_dropped_logged_and_never_repaired(self):
        bad = dict(_valid_salary_fact(), amount="9999")  # digits absent from quote
        client = FakeClient({"message_01": _completion([bad])})
        extractor = self._extractor(client)

        extractor.record_message(self.messages["message_01"])

        self.assertEqual(extractor.facts_for_user("user_02"), ())
        rules = [
            r["error"]
            for r in self._log_records()
            if r["purpose"] == "message_validation"
        ]
        self.assertIn("V5", rules)
        # The raw model output is preserved in the fixture, not repaired.
        document = json.loads(
            next((self.fixtures / "message").glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(document["facts"][0]["amount"], "9999")

    def test_empty_facts_are_recorded_as_an_empty_fixture(self):
        client = FakeClient({"message_01": _completion([])})
        extractor = self._extractor(client)
        extractor.record_message(self.messages["message_01"])
        self.assertEqual(extractor.facts_for_user("user_02"), ())
        document = json.loads(
            next((self.fixtures / "message").glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(document["facts"], [])


class TestResume(ExtractorCase):
    def test_second_run_reuses_fixture_without_calling(self):
        row = self.messages["message_01"]
        self._extractor(
            FakeClient({"message_01": _completion([_valid_salary_fact()])})
        ).record_message(row)

        second_client = FakeClient({})  # any call raises AssertionError
        second = self._extractor(second_client)

        self.assertFalse(second.record_message(row))
        self.assertEqual(second_client.calls, [])
        self.assertEqual(
            [f.fact_type for f in second.facts_for_user("user_02")], ["salary_increase"]
        )

    def test_record_all_makes_exactly_one_call_per_message(self):
        client = FakeClient(default=_completion([]))
        extractor = self._extractor(client)

        summary = extractor.record_all()

        self.assertEqual(sorted(client.calls), sorted(self.messages))
        self.assertEqual(len(client.calls), len(set(client.calls)))
        self.assertEqual(summary.recorded, len(self.messages))
        self.assertEqual(summary.cached, 0)

    def test_cache_detection_spans_known_models(self):
        """Switching model must not re-record a fixture already written by another."""
        row = self.messages["message_01"]
        self._extractor(
            FakeClient({"message_01": _completion([_valid_salary_fact()])})
        ).record_message(row)

        other_client = FakeClient({})
        other = GroqExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
            client=other_client,
            model="openai/gpt-oss-20b",
            known_specs=[("groq", "qwen/qwen3.8-27b"), ("groq", "openai/gpt-oss-20b")],
            logger=UsageLogger(self.log_path, now=FIXED_CLOCK),
            now=FIXED_CLOCK,
        )
        self.assertFalse(other.record_message(row))
        self.assertEqual(other_client.calls, [])

    def test_record_all_counts_cache_hits_on_second_run(self):
        client = FakeClient(default=_completion([]))
        self._extractor(client).record_all()

        second_client = FakeClient({})
        summary = self._extractor(second_client).record_all()

        self.assertEqual(second_client.calls, [])
        self.assertEqual(summary.recorded, 0)
        self.assertEqual(summary.cached, len(self.messages))


class TestFailures(ExtractorCase):
    def test_api_error_is_logged_and_run_continues(self):
        error = GroqError("rate_limit_exceeded", status=429, retries=5)
        client = FakeClient(
            {"message_01": error},
            default=_completion([]),
        )
        extractor = self._extractor(client)

        summary = extractor.record_all()

        self.assertEqual(summary.failed, 1)
        errors = [r for r in self._log_records() if r["error"] == "rate_limit_exceeded"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["subject_id"], "message_01")
        # No fixture was written for the failed message, so a later run retries it.
        second_client = FakeClient({"message_01": _completion([_valid_salary_fact()])})
        second = self._extractor(second_client)
        self.assertTrue(second.record_message(self.messages["message_01"]))
        self.assertEqual(second_client.calls, ["message_01"])


if __name__ == "__main__":
    unittest.main()

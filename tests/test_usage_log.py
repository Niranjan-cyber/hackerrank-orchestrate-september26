"""Tests for the append-only extraction usage log.

The log is a required submission artifact (AGENTS.md 6.5). Its contract is narrow
and safety-critical: it must never fail a run, must append rather than rewrite, and
must never record prompt content, response content, or credentials.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from extraction.usage_log import UsageLogger  # noqa: E402

FIXED_CLOCK = lambda: "2026-09-13T00:00:00+00:00"  # noqa: E731


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestUsageLogger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "evaluation" / "usage_raw.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _logger(self) -> UsageLogger:
        return UsageLogger(self.path, now=FIXED_CLOCK)

    def test_writes_one_json_object_per_call(self):
        self._logger().record_call(
            provider="groq",
            model="qwen/qwen3.8-27b",
            purpose="message_fact",
            subject_id="message_01",
            input_tokens=141,
            output_tokens=132,
            total_tokens=273,
            estimated_cost_usd=Decimal("0.0001"),
            retries=0,
        )
        records = _read_lines(self.path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["provider"], "groq")
        self.assertEqual(record["model"], "qwen/qwen3.8-27b")
        self.assertEqual(record["purpose"], "message_fact")
        self.assertEqual(record["subject_id"], "message_01")
        self.assertEqual(record["input_tokens"], 141)
        self.assertEqual(record["output_tokens"], 132)
        self.assertEqual(record["total_tokens"], 273)
        self.assertEqual(record["retries"], 0)
        self.assertEqual(record["timestamp"], FIXED_CLOCK())

    def test_estimated_cost_is_a_string_not_a_float(self):
        self._logger().record_call(
            provider="groq",
            model="m",
            purpose="message_fact",
            subject_id="message_01",
            estimated_cost_usd=Decimal("0.0000123"),
        )
        record = _read_lines(self.path)[0]
        self.assertEqual(record["estimated_cost_usd"], "0.0000123")
        self.assertIsInstance(record["estimated_cost_usd"], str)

    def test_appends_rather_than_rewrites(self):
        logger = self._logger()
        logger.record_call(
            provider="groq", model="m", purpose="message_fact", subject_id="message_01"
        )
        logger.record_call(
            provider="groq", model="m", purpose="message_fact", subject_id="message_02"
        )
        records = _read_lines(self.path)
        self.assertEqual(
            [r["subject_id"] for r in records], ["message_01", "message_02"]
        )

    def test_records_errors_and_retries(self):
        self._logger().record_call(
            provider="groq",
            model="m",
            purpose="message_fact",
            subject_id="message_01",
            retries=3,
            error="rate_limit_exceeded",
        )
        record = _read_lines(self.path)[0]
        self.assertEqual(record["retries"], 3)
        self.assertEqual(record["error"], "rate_limit_exceeded")

    def test_records_violation_without_fact_content(self):
        self._logger().record_violation(
            subject_id="message_01", rule="V5", fact_type="salary_increase"
        )
        record = _read_lines(self.path)[0]
        self.assertEqual(record["purpose"], "message_validation")
        self.assertEqual(record["subject_id"], "message_01")
        self.assertEqual(record["error"], "V5")
        self.assertNotIn("detail", record)
        self.assertNotIn("quote", record)

    def test_never_raises_when_path_is_unwritable(self):
        """A logging failure must never fail a run (CONTEXT 16)."""
        directory_as_file = Path(self._tmp.name) / "a_directory"
        directory_as_file.mkdir()
        # Opening a directory for append raises; the logger must swallow it.
        UsageLogger(directory_as_file, now=FIXED_CLOCK).record_call(
            provider="groq", model="m", purpose="message_fact", subject_id="message_01"
        )

    def test_creates_parent_directories(self):
        self.assertFalse(self.path.parent.exists())
        self._logger().record_call(
            provider="groq", model="m", purpose="message_fact", subject_id="message_01"
        )
        self.assertTrue(self.path.exists())

    def test_record_has_exactly_the_contract_fields(self):
        """Only ids and token counts - never prompt content or credentials."""
        self._logger().record_call(
            provider="groq",
            model="m",
            purpose="message_fact",
            subject_id="message_01",
        )
        allowed = {
            "timestamp",
            "provider",
            "model",
            "purpose",
            "subject_id",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "estimated_cost_usd",
            "retries",
            "error",
        }
        self.assertEqual(set(_read_lines(self.path)[0]), allowed)

    def test_null_logger_is_a_no_op(self):
        from extraction.usage_log import NullUsageLogger

        NullUsageLogger().record_call(
            provider="groq", model="m", purpose="message_fact", subject_id="x"
        )
        NullUsageLogger().record_violation(subject_id="x", rule="V1", fact_type="t")


if __name__ == "__main__":
    unittest.main()

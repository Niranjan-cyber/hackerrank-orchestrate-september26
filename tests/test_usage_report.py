"""Tests for the ticket 15 usage-report generator.

``evaluation/usage_report.md`` is a required submission artifact (AGENTS.md 6.5) and
must be generated from ``evaluation/usage_raw.jsonl`` (CONTEXT.md section 16), never
hand-written, so the numbers are measured rather than reconstructed. The final run
(``code/main.py``) is entirely fixture-backed and makes zero live model calls, so the
report must say so explicitly, and must keep that fact separate from the historical
cost of recording the fixture cache those facts came from (CONTEXT D26/D27).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from evaluation.report import (  # noqa: E402
    load_records,
    render_report,
    summarize_message_extraction,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


SUCCESS_A = {
    "timestamp": "2026-09-13T00:00:00+00:00",
    "provider": "groq",
    "model": "qwen/qwen3.8-27b",
    "purpose": "message_fact",
    "subject_id": "message_01",
    "input_tokens": 1000,
    "output_tokens": 100,
    "total_tokens": 1100,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "estimated_cost_usd": "0.001",
    "retries": 0,
    "error": None,
}
SUCCESS_B = {
    **SUCCESS_A,
    "subject_id": "message_02",
    "model": "openai/gpt-oss-20b",
    "input_tokens": 500,
    "output_tokens": 50,
    "total_tokens": 550,
    "estimated_cost_usd": "0.0005",
}
FAILED = {
    "timestamp": "2026-09-13T00:00:01+00:00",
    "provider": "groq",
    "model": "qwen/qwen3.8-27b",
    "purpose": "message_fact",
    "subject_id": "message_03",
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "estimated_cost_usd": None,
    "retries": 6,
    "error": "rate_limit_exceeded",
}
CACHE_HIT = {
    "timestamp": "2026-09-13T00:00:02+00:00",
    "provider": "groq",
    "model": "qwen/qwen3.8-27b",
    "purpose": "message_cache_hit",
    "subject_id": "message_04",
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "estimated_cost_usd": None,
    "retries": 0,
    "error": None,
}
VIOLATION = {
    "timestamp": "2026-09-13T00:00:03+00:00",
    "provider": "",
    "model": "",
    "purpose": "message_validation",
    "subject_id": "message_04",
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "estimated_cost_usd": None,
    "retries": 0,
    "error": "V10",
}


class TestLoadRecords(unittest.TestCase):
    def test_reads_every_line_as_a_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage_raw.jsonl"
            _write_jsonl(path, [SUCCESS_A, FAILED])
            records = load_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["subject_id"], "message_01")

    def test_missing_file_yields_no_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.jsonl"
            self.assertEqual(load_records(path), [])

    def test_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage_raw.jsonl"
            path.write_text(json.dumps(SUCCESS_A) + "\n\n", encoding="utf-8")
            self.assertEqual(len(load_records(path)), 1)


class TestSummarizeMessageExtraction(unittest.TestCase):
    def test_separates_successes_failures_cache_hits_and_violations(self):
        summary = summarize_message_extraction(
            [SUCCESS_A, SUCCESS_B, FAILED, CACHE_HIT, VIOLATION]
        )
        self.assertEqual(summary.attempted, 3)  # SUCCESS_A, SUCCESS_B, FAILED
        self.assertEqual(summary.succeeded, 2)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.cache_hits, 1)
        self.assertEqual(summary.dropped_facts, 1)
        self.assertEqual(summary.error_counts, {"rate_limit_exceeded": 1})
        self.assertEqual(summary.violation_counts, {"V10": 1})

    def test_per_model_and_overall_token_and_cost_totals(self):
        summary = summarize_message_extraction([SUCCESS_A, SUCCESS_B, FAILED])
        by_model = {stats.model: stats for stats in summary.per_model}
        self.assertEqual(by_model["qwen/qwen3.8-27b"].calls, 1)
        self.assertEqual(by_model["qwen/qwen3.8-27b"].input_tokens, 1000)
        self.assertEqual(by_model["qwen/qwen3.8-27b"].output_tokens, 100)
        self.assertEqual(
            by_model["qwen/qwen3.8-27b"].cost_usd, Decimal("0.001")
        )
        self.assertEqual(by_model["openai/gpt-oss-20b"].calls, 1)
        self.assertEqual(summary.total.calls, 2)
        self.assertEqual(summary.total.input_tokens, 1500)
        self.assertEqual(summary.total.output_tokens, 150)
        self.assertEqual(summary.total.total_tokens, 1650)
        self.assertEqual(summary.total.cost_usd, Decimal("0.0015"))

    def test_empty_input_never_divides_by_zero(self):
        summary = summarize_message_extraction([])
        self.assertEqual(summary.attempted, 0)
        self.assertEqual(summary.succeeded, 0)
        self.assertEqual(summary.total.calls, 0)
        self.assertEqual(summary.total.cost_usd, Decimal("0"))
        self.assertEqual(summary.per_model, ())


class TestRenderReport(unittest.TestCase):
    def _summary(self):
        return summarize_message_extraction([SUCCESS_A, SUCCESS_B, FAILED, VIOLATION])

    def test_states_zero_live_calls_for_the_final_run(self):
        report = render_report(
            message_summary=self._summary(),
            fixture_counts={"message": 215, "image": 16},
            image_provenance={"provider": "opencode", "model": "deepseek-v4.1-flash"},
            request_count=250,
        )
        self.assertIn("0", report)
        self.assertIn("live model call", report.lower())
        self.assertIn("231", report)  # 215 + 16 fixture/cache hits

    def test_documents_cache_hits_separately_from_live_recording_cost(self):
        report = render_report(
            message_summary=self._summary(),
            fixture_counts={"message": 215, "image": 16},
            image_provenance={"provider": "opencode", "model": "deepseek-v4.1-flash"},
            request_count=250,
        )
        self.assertIn("cache", report.lower())
        self.assertIn("qwen/qwen3.8-27b", report)
        self.assertIn("openai/gpt-oss-20b", report)
        self.assertIn("groq", report.lower())
        self.assertIn("opencode", report.lower())
        self.assertIn("deepseek-v4.1-flash", report)

    def test_reports_totals_and_per_call_averages(self):
        report = render_report(
            message_summary=self._summary(),
            fixture_counts={"message": 215, "image": 16},
            image_provenance={"provider": "opencode", "model": "deepseek-v4.1-flash"},
            request_count=250,
        )
        self.assertIn("1650", report)  # total tokens across both models
        self.assertIn("825", report)  # average tokens per successful call (1650/2)

    def test_reports_an_average_normalized_over_evaluation_requests(self):
        """AGENTS.md 6.5 asks for "average tokens per request" / "per-request cost" -
        the request being the 250 evaluation rows, not the underlying model calls."""
        report = render_report(
            message_summary=self._summary(),
            fixture_counts={"message": 215, "image": 16},
            image_provenance={"provider": "opencode", "model": "deepseek-v4.1-flash"},
            request_count=10,
        )
        self.assertIn("165", report)  # 1650 total tokens / 10 requests

    def test_handles_no_image_provenance(self):
        report = render_report(
            message_summary=self._summary(),
            fixture_counts={"message": 215, "image": 0},
            image_provenance=None,
            request_count=250,
        )
        self.assertIsInstance(report, str)
        self.assertGreater(len(report), 0)


if __name__ == "__main__":
    unittest.main()

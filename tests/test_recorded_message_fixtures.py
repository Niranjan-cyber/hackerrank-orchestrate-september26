"""Integration test over the committed, recorded message fixtures.

This is the acceptance test for Ticket 11: every one of the 215 messages has a
committed fixture keyed by the current prompt and contract version, so a warm run is
offline and reproducible. It touches no network. A prompt edit bumps ``PROMPT_VERSION``
and turns every lookup into a miss, so this test fails until the corpus is re-recorded -
which is the intended trap.
"""

from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.loaders import load_dataset  # noqa: E402
from extraction.fixture_adapter import FixtureExtractor, fixture_key  # noqa: E402
from extraction.prompts import build_fixture_payload  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
CONTRACT_VERSION = "1.0.0"
PROVIDER = "groq"
# The primary model is qwen/qwen3.8-27b. A daily token cap forced the remainder of the
# corpus onto the gpt-oss fallbacks; every model is recorded per fact, so provenance
# stays truthful.
MODELS = ("qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b")


def _messages() -> dict[str, dict[str, str]]:
    with (REPO_ROOT / "dataset" / "messages.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        return {row["message_id"]: row for row in csv.DictReader(handle)}


def _fixture_paths(row: dict[str, str]) -> list[Path]:
    paths = []
    for model in MODELS:
        key = fixture_key(
            CONTRACT_VERSION,
            PROVIDER,
            model,
            "message",
            row["message_id"],
            build_fixture_payload(row),
        )
        paths.append(FIXTURES_DIR / "message" / f"{key}.json")
    return paths


def _has_fixture(row: dict[str, str]) -> bool:
    return any(path.exists() for path in _fixture_paths(row))


class TestRecordedFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = _messages()
        cls.dataset = load_dataset(REPO_ROOT / "dataset")
        cls.extractor = FixtureExtractor(
            dataset=cls.dataset,
            dataset_dir=REPO_ROOT / "dataset",
            fixtures_dir=FIXTURES_DIR,
            contract_version=CONTRACT_VERSION,
        )

    def test_every_message_has_a_committed_fixture(self):
        missing = [mid for mid, row in self.messages.items() if not _has_fixture(row)]
        self.assertEqual(missing, [], f"{len(missing)} messages lack a fixture")

    def test_fixture_count_matches_message_count(self):
        files = list((FIXTURES_DIR / "message").glob("*.json"))
        self.assertEqual(len(files), len(self.messages))

    def test_sample_user_messages_are_included(self):
        with (REPO_ROOT / "dataset" / "sample_requests.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            sample_users = {row["user_id"] for row in csv.DictReader(handle)}
        sample_messages = [
            row for row in self.messages.values() if row["user_id"] in sample_users
        ]
        self.assertTrue(sample_messages)
        for row in sample_messages:
            with self.subTest(message=row["message_id"]):
                self.assertTrue(_has_fixture(row))

    def test_every_fixture_records_groq_provenance(self):
        for path in (FIXTURES_DIR / "message").glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("facts", document)
            for fact in document["facts"]:
                extractor = fact["extractor"]
                self.assertEqual(extractor["provider"], PROVIDER)
                self.assertIn(extractor["model"], MODELS)
                self.assertEqual(extractor["contract_version"], CONTRACT_VERSION)
                self.assertEqual(extractor["fixture_key"], path.stem)

    def test_no_verbatim_quote_failures_across_the_corpus(self):
        v4 = [v for v in self.extractor.violations if v.rule == "V4"]
        self.assertEqual(v4, [])

    def test_the_scam_message_is_still_a_windfall_solicitation(self):
        facts = self.extractor.facts_for_user("user_88")
        self.assertIn("windfall_solicitation", {f.fact_type for f in facts})

    def test_message_01_is_a_salary_increase(self):
        facts = self.extractor.facts_for_user("user_02")
        self.assertIn("salary_increase", {f.fact_type for f in facts})


if __name__ == "__main__":
    unittest.main()

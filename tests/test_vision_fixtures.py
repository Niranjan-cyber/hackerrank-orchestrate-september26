"""Cross-checks for the 16 committed image fixtures (ticket 12).

These pin the shipped artifact, not the extractor: the fixtures in `fixtures/image/`
must all resolve through the same offline adapter a grader runs, must never be zero,
and - for the five sample users - must produce the amount the solved samples imply.
`request_16` is the one sample request whose blank rent event is inside the forecast
window, so it is a true end-to-end cross-check against the published output.
"""

from __future__ import annotations

import csv
import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.evidence import resolve_blank_amounts  # noqa: E402
from engine.loaders import load_dataset  # noqa: E402
from engine.pipeline import run_pipeline  # noqa: E402
from engine.types import Config  # noqa: E402
from extraction.fixture_adapter import FixtureExtractor  # noqa: E402

# event_id -> (amount, currency, image_id). The 5 sample-user images (01-05) are
# cross-checked against published outputs; the 11 evaluation ones against the image.
EXPECTED = {
    "event_253": ("4365000.00", "IDR", "image_01"),
    "event_1442": ("100000.00", "INR", "image_02"),
    "event_1545": ("41272.00", "INR", "image_03"),
    "event_1700": ("2854.00", "INR", "image_04"),
    "event_1786": ("704.05", "INR", "image_05"),
    "event_3051": ("1995.00", "INR", "image_06"),
    "event_3231": ("8528.10", "INR", "image_07"),
    "event_4535": ("15339.00", "INR", "image_08"),
    "event_5170": ("723.00", "INR", "image_09"),
    "event_6033": ("79679.26", "INR", "image_10"),
    "event_6859": ("3650.00", "INR", "image_11"),
    "event_7307": ("33.50", "USD", "image_12"),
    "event_7941": ("2298.00", "INR", "image_13"),
    "event_9421": ("4543.00", "INR", "image_14"),
    "event_9806": ("9968.00", "INR", "image_15"),
    "event_10521": ("393.22", "INR", "image_16"),
}

SAMPLE_IMAGE_EVENTS = (
    "event_253",
    "event_1442",
    "event_1545",
    "event_1700",
    "event_1786",
)


class TestCommittedImageFixtures(unittest.TestCase):
    def setUp(self):
        self.dataset_dir = REPO_ROOT / "dataset"
        self.dataset = load_dataset(self.dataset_dir)
        self.extractor = FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=REPO_ROOT / "fixtures",
        )

    def test_exactly_sixteen_blank_amount_events_have_a_fixture(self):
        resolved = [
            event_id
            for event_id in sorted(self.extractor.blank_amount_events)
            if self.extractor.image_amount(event_id) is not None
        ]
        self.assertEqual(len(resolved), 16)
        self.assertEqual(set(resolved), set(EXPECTED))

    def test_each_amount_and_currency_is_the_verified_reading(self):
        for event_id, (amount, currency, image_id) in EXPECTED.items():
            with self.subTest(event_id=event_id):
                fact = self.extractor.image_amount(event_id)
                self.assertIsNotNone(fact)
                self.assertEqual(fact.amount, Decimal(amount))
                self.assertEqual(fact.currency, currency)
                self.assertEqual(fact.subject, image_id)
                self.assertEqual(fact.related_event_id, event_id)

    def test_no_fixture_is_zero(self):
        for event_id in EXPECTED:
            with self.subTest(event_id=event_id):
                fact = self.extractor.image_amount(event_id)
                self.assertIsNotNone(fact.amount)
                self.assertGreater(fact.amount, Decimal("0"))

    def test_no_image_fixture_fails_validation(self):
        image_violations = [
            violation
            for violation in self.extractor.violations
            if violation.subject.startswith("image_")
        ]
        self.assertEqual(image_violations, [])

    def test_sample_user_images_carry_two_call_provenance(self):
        for event_id in SAMPLE_IMAGE_EVENTS:
            with self.subTest(event_id=event_id):
                fact = self.extractor.image_amount(event_id)
                self.assertIsNotNone(fact)
                self.assertEqual(
                    fact.extractor.get("self_consistency"), "two_call_agree"
                )


class TestSampleCrossCheck(unittest.TestCase):
    """Drive the solved samples and check the one image-resolved request end to end.

    Only `request_16` can be compared to a published output this way: the other four
    sample-user blank events are either settled history already inside
    `current_available_balance` (events 253/1545/1700, which the published output cannot
    discriminate) or belong to a request the uncalibrated engine already misses
    (`request_20`). They are still verified structurally and against imputation below.
    """

    def test_image_01_matches_the_engines_own_salary_imputation(self):
        """An independent deterministic route lands on the same 4,365,000 net pay."""
        dataset_dir = REPO_ROOT / "dataset"
        dataset = load_dataset(dataset_dir, dataset_dir / "sample_requests.csv")
        request = next(r for r in dataset.requests if r.user_id == "user_03")
        blanks = resolve_blank_amounts(
            dataset.events_by_user["user_03"],
            (),
            request,
            dataset.profiles["user_03"],
            Config(),
            dataset.rates,
        )
        self.assertEqual(blanks.overrides["event_253"], Decimal("4365000.00"))
        # The image fixture and the engine's own imputation agree on that salary.
        reader = FixtureExtractor(
            dataset=dataset,
            dataset_dir=dataset_dir,
            fixtures_dir=REPO_ROOT / "fixtures",
        )
        self.assertEqual(reader.image_amount("event_253").amount, Decimal("4365000.00"))

    def test_request_16_matches_the_published_sample(self):
        dataset_dir = REPO_ROOT / "dataset"
        sample_path = dataset_dir / "sample_requests.csv"
        dataset = load_dataset(dataset_dir, sample_path)
        extractor = FixtureExtractor(
            dataset=dataset,
            dataset_dir=dataset_dir,
            fixtures_dir=REPO_ROOT / "fixtures",
        )
        facts = tuple(
            fact
            for request in dataset.requests
            for fact in extractor.facts_for_user(request.user_id)
        )
        rows = {row.request_id: row for row in run_pipeline(dataset, facts, Config())}
        row = rows["request_16"]

        with sample_path.open(encoding="utf-8-sig", newline="") as handle:
            published = {item["request_id"]: item for item in csv.DictReader(handle)}[
                "request_16"
            ]

        self.assertEqual(
            row.amount_safe_to_pay, Decimal(published["amount_safe_to_pay"])
        )
        self.assertEqual(row.affordability_status, published["affordability_status"])
        self.assertEqual(
            row.recommended_payment_method, published["recommended_payment_method"]
        )
        self.assertEqual(
            row.earliest_date_for_full_payment.isoformat(),
            published["earliest_date_for_full_payment"],
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for the extraction port and fixture adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

# Make code/ importable from tests/
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.loaders import load_dataset
from engine.types import Fact
from extraction.fixture_adapter import FixtureExtractor, fixture_key


class TestFixtureKey(unittest.TestCase):
    """The key must change when contract version, provider, model or input changes."""

    def test_key_is_16_hex_chars(self):
        key = fixture_key(
            "1.0.0",
            "fixture",
            "hand_authored",
            "message",
            "message_01",
            {"message_id": "message_01", "message_text": "hello"},
        )
        self.assertEqual(len(key), 16)
        int(key, 16)  # valid hex

    def test_key_changes_with_contract_version(self):
        payload = {"message_id": "message_01", "message_text": "hello"}
        k1 = fixture_key(
            "1.0.0", "fixture", "hand_authored", "message", "message_01", payload
        )
        k2 = fixture_key(
            "1.0.1", "fixture", "hand_authored", "message", "message_01", payload
        )
        self.assertNotEqual(k1, k2)

    def test_key_changes_with_input(self):
        payload = {"message_id": "message_01", "message_text": "hello"}
        k1 = fixture_key(
            "1.0.0", "fixture", "hand_authored", "message", "message_01", payload
        )
        payload["message_text"] = "world"
        k2 = fixture_key(
            "1.0.0", "fixture", "hand_authored", "message", "message_01", payload
        )
        self.assertNotEqual(k1, k2)


class TestFixtureExtractor(unittest.TestCase):
    """End-to-end adapter behaviour against the real dataset and hand-authored fixtures."""

    def setUp(self):
        self.dataset_dir = REPO_ROOT / "dataset"
        self.fixtures_dir = REPO_ROOT / "fixtures"
        self.dataset = load_dataset(self.dataset_dir)
        self.extractor = FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures_dir,
        )

    def test_windfall_solicitation_fixture_returned(self):
        """message_67 is an advance-fee scam; the adapter returns it as a valid fact."""
        facts = self.extractor.facts_for_user("user_88")
        self.assertTrue(any(f.subject == "message_67" for f in facts))
        scam = next(f for f in facts if f.subject == "message_67")
        self.assertEqual(scam.fact_type, "windfall_solicitation")
        self.assertIsNone(scam.amount)
        self.assertEqual(scam.extractor["provider"], "fixture")
        self.assertIn("hand_authored", scam.extractor.get("model", ""))

    def test_bad_digit_fixture_dropped(self):
        """message_01 fixture has amount digits absent from its quote -> V5 drop."""
        facts = self.extractor.facts_for_user("user_02")
        self.assertFalse(any(f.subject == "message_01" for f in facts))
        v5 = [v for v in self.extractor.violations if v.rule == "V5"]
        self.assertTrue(v5)
        self.assertEqual(v5[0].subject, "message_01")

    def test_empty_tuple_for_user_with_no_evidence(self):
        facts = self.extractor.facts_for_user("user_999")
        self.assertEqual(facts, ())

    def test_deterministic_ordering(self):
        facts1 = self.extractor.facts_for_user("user_88")
        extractor2 = FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures_dir,
        )
        facts2 = extractor2.facts_for_user("user_88")
        self.assertEqual(facts1, facts2)
        # Ordering key is (fact_type, subject)
        self.assertEqual(
            list(facts1),
            sorted(facts1, key=lambda f: (f.fact_type, f.subject)),
        )

    def test_image_amount_none_when_no_image_linked(self):
        """A non-blank-amount event returns None."""
        # Pick any event known to have an amount.
        for user_events in self.dataset.events_by_user.values():
            for event in user_events:
                if event.amount is not None:
                    result = self.extractor.image_amount(event.event_id)
                    self.assertIsNone(result)
                    return
        self.fail("no event with an amount found")

    def test_image_amount_hard_fail_when_fixture_missing(self):
        """A blank-amount event with a linked image but no fixture raises loudly."""
        # Find a blank-amount event linked to an image (the 16 real cases).
        import csv

        with (self.dataset_dir / "images.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            images = {row["image_id"]: row for row in csv.DictReader(handle)}

        for event in self.extractor.blank_amount_events.values():
            image = next(
                (
                    img
                    for img in images.values()
                    if img.get("related_event_id") == event.event_id
                ),
                None,
            )
            if image is not None:
                with self.assertRaises(FileNotFoundError) as ctx:
                    self.extractor.image_amount(event.event_id)
                self.assertIn(image["image_id"], str(ctx.exception))
                return
        self.fail("no blank-amount event with linked image found")


class TestFixtureExtractorWithCustomFixtures(unittest.TestCase):
    """Isolated tests with a temporary fixture directory."""

    def test_custom_image_fixture_resolves(self):
        from engine.money import money

        # Use a real blank-amount event from the dataset.
        blank_event = next(
            iter(
                FixtureExtractor(
                    dataset=load_dataset(REPO_ROOT / "dataset"),
                    dataset_dir=REPO_ROOT / "dataset",
                    fixtures_dir=REPO_ROOT / "fixtures",
                ).blank_amount_events.values()
            )
        )

        import csv

        with (REPO_ROOT / "dataset" / "images.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            images = {row["image_id"]: row for row in csv.DictReader(handle)}
        image = next(
            img
            for img in images.values()
            if img.get("related_event_id") == blank_event.event_id
        )

        payload = {"event_id": blank_event.event_id, "image_id": image["image_id"]}
        key = fixture_key(
            "1.0.0", "fixture", "hand_authored", "image", image["image_id"], payload
        )

        fact_json = {
            "fact_type": "image_amount",
            "subject": image["image_id"],
            "user_id": blank_event.user_id,
            "related_event_id": blank_event.event_id,
            "amount": "12345.67",
            "currency": blank_event.currency,
            "verbatim_amount_string": "12,345.67",
            "confidence": "high",
            "extractor": {
                "provider": "fixture",
                "model": "hand_authored",
                "contract_version": "1.0.0",
                "extracted_at": "2026-09-12T21:40:00Z",
                "fixture_key": key,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = Path(tmp) / "fixtures"
            image_dir = fixtures / "image"
            image_dir.mkdir(parents=True)
            (image_dir / f"{key}.json").write_text(
                json.dumps(fact_json, sort_keys=True), encoding="utf-8"
            )

            extractor = FixtureExtractor(
                dataset=load_dataset(REPO_ROOT / "dataset"),
                dataset_dir=REPO_ROOT / "dataset",
                fixtures_dir=fixtures,
            )
            fact = extractor.image_amount(blank_event.event_id)
            self.assertIsNotNone(fact)
            self.assertEqual(fact.fact_type, "image_amount")
            self.assertEqual(fact.amount, money("12345.67"))


class TestValidateRegression(unittest.TestCase):
    """Focused regression tests for extraction validation bugs."""

    def _minimal_dataset(
        self,
        home_currency: str = "INR",
        rates: dict[tuple[str, str, str], Decimal] | None = None,
    ):
        from engine.money import money
        from engine.types import Dataset, Profile, Request

        request = Request(
            request_id="request_test",
            user_id="user_test",
            request_date=date(2025, 8, 1),
            request_type="purchase",
            requested_amount=money("1000"),
            desired_completion_date=date(2025, 10, 1),
            allows_partial_payment=False,
            request_text="test",
        )
        profile = Profile(
            user_id="user_test",
            home_currency=home_currency,
            current_available_balance=money("10000"),
            minimum_balance_to_keep=money("1000"),
            financial_priorities=(),
            protected_categories=(),
            reducible_categories=(),
            stoppable_categories=(),
            payment_methods_considered=("full_payment",),
            max_installment_months=None,
        )
        return Dataset(
            requests=(request,),
            profiles={"user_test": profile},
            events_by_user={},
            options_by_request={},
            rates=rates or {},
        )

    def test_v9_downgrade_does_not_crash_on_slotted_fact(self):
        """Authority downgrade used Fact(**fact.__dict__) on a slots dataclass."""
        from engine.money import money
        from engine.types import Fact
        from extraction.validate import validate_fact

        fact = Fact(
            fact_type="salary_first",
            subject="message_test",
            user_id="user_test",
            amount=money("5000"),
            currency="INR",
            effective_date=date(2025, 8, 15),
            verbatim_quote="Your salary is INR 5000.",
            source_type="financial_service",  # fails authority condition 2
            source_language="en",
            confidence="high",
        )
        dataset = self._minimal_dataset()
        messages = {
            "message_test": {
                "message_id": "message_test",
                "message_text": "Your salary is INR 5000.",
            }
        }
        requests_by_user = {r.user_id: r for r in dataset.requests}

        # Before the fix this raised AttributeError because Fact has slots and no
        # __dict__. After the fix it completes and records the V9 authority failure.
        _, violations = validate_fact(fact, dataset, messages, {}, {}, requests_by_user)

        v9_violations = [v for v in violations if v.rule == "V9"]
        self.assertEqual(len(v9_violations), 1)
        self.assertIn("authority downgrade", v9_violations[0].detail)

        self.assertTrue(any(v.rule == "V9" for v in violations))
        self.assertIsNotNone([v for v in violations if v.rule == "V9"][0].detail)

    def test_v7_requires_exact_rate_direction(self):
        """Validation must not accept the inverse rate that the engine cannot use."""
        from engine.money import money
        from engine.types import Fact
        from extraction.validate import validate_fact

        # Rate exists only USD -> INR; engine cannot convert INR -> USD.
        rates = {("2025-08-01", "USD", "INR"): Decimal("84.50")}
        requests_by_user = {
            r.user_id: r for r in self._minimal_dataset(home_currency="INR").requests
        }

        fact_usd = Fact(
            fact_type="salary_first",
            subject="message_usd",
            user_id="user_test",
            amount=money("100"),
            currency="USD",
            effective_date=date(2025, 8, 15),
            verbatim_quote="Salary USD 100.",
            source_type="employer",
            source_language="en",
            confidence="high",
        )
        dataset_usd = self._minimal_dataset(home_currency="INR", rates=rates)
        messages_usd = {
            "message_usd": {
                "message_id": "message_usd",
                "message_text": "Salary USD 100.",
            }
        }
        valid, violations = validate_fact(
            fact_usd, dataset_usd, messages_usd, {}, {}, requests_by_user
        )
        self.assertIsNotNone(valid)
        self.assertFalse(any(v.rule == "V7" for v in violations))

        fact_inr = Fact(
            fact_type="salary_first",
            subject="message_inr",
            user_id="user_test",
            amount=money("100"),
            currency="INR",
            effective_date=date(2025, 8, 15),
            verbatim_quote="Salary INR 100.",
            source_type="employer",
            source_language="en",
            confidence="high",
        )
        dataset_inr = self._minimal_dataset(home_currency="USD", rates=rates)
        messages_inr = {
            "message_inr": {
                "message_id": "message_inr",
                "message_text": "Salary INR 100.",
            }
        }
        valid, violations = validate_fact(
            fact_inr, dataset_inr, messages_inr, {}, {}, requests_by_user
        )
        self.assertIsNone(valid)
        self.assertTrue(any(v.rule == "V7" for v in violations))


if __name__ == "__main__":
    unittest.main()

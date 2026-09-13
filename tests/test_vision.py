"""Tests for vision extraction of the 16 blank amounts (ticket 12).

Pure pieces are tested directly; the extractor is driven end-to-end against the real
dataset with a scripted client, so the two-call consistency, the currency-aware parse,
the digit check, and the "no fixture on failure" rule are all exercised without a
network or a key.
"""

from __future__ import annotations

import csv
import json
import struct
import sys
import tempfile
import unittest
import zlib
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from engine.loaders import load_dataset  # noqa: E402
from extraction.amounts import AmountParseError  # noqa: E402
from extraction.fixture_adapter import (  # noqa: E402
    FIXTURE_MODEL,
    FIXTURE_PROVIDER,
    FixtureExtractor,
    fixture_key,
)
from extraction.prompts import (  # noqa: E402
    build_image_fixture_payload,
    build_image_schema,
)
from extraction.vision import (  # noqa: E402
    CACHED,
    DISAGREED,
    FAILED,
    RECORDED,
    RawReading,
    ScriptedVisionClient,
    VisionError,
    VisionExtractor,
    build_vision_messages,
    prepare_image,
    reading_to_fact,
    reconcile,
)


def make_png(width: int, height: int, color: tuple[int, int, int] = (180, 180, 180)):
    """A valid PNG with no external dependency (stdlib zlib + struct)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        signature
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


class TestPrepareImage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_in_cap_image_is_passed_through_untouched(self):
        data = make_png(100, 50)
        path = self._write("small.png", data)
        prepared = prepare_image(path, "image_x", tier_cap=1568)
        self.assertFalse(prepared.resized)
        self.assertEqual(prepared.data, data)
        self.assertEqual((prepared.width, prepared.height), (100, 50))
        self.assertEqual(prepared.mime_type, "image/png")

    @unittest.skipUnless(_has_pillow(), "Pillow not installed")
    def test_oversized_image_is_resized_and_never_jpeg(self):
        path = self._write("big.png", make_png(1700, 900))
        prepared = prepare_image(path, "image_x", tier_cap=1568)
        self.assertTrue(prepared.resized)
        self.assertEqual(max(prepared.width, prepared.height), 1568)
        self.assertEqual(prepared.mime_type, "image/png")
        self.assertTrue(prepared.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_non_png_is_rejected(self):
        path = self._write("not.png", b"definitely not a png")
        with self.assertRaises(VisionError):
            prepare_image(path, "image_x")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            prepare_image(self.dir / "absent.png", "image_x")


class TestBuildVisionMessages(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "img.png"
        path.write_bytes(make_png(20, 20))
        self.image = prepare_image(path, "image_01")

    def tearDown(self):
        self._tmp.cleanup()

    def test_image_content_precedes_text(self):
        messages = build_vision_messages(self.image)
        user_content = messages[1]["content"]
        self.assertEqual(user_content[0]["type"], "image_url")
        self.assertEqual(user_content[1]["type"], "text")
        self.assertTrue(
            user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_system_prompt_frames_the_image_as_untrusted(self):
        messages = build_vision_messages(self.image)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("untrusted", messages[0]["content"].lower())

    def test_no_output_column_is_named_in_the_prompt(self):
        def text_of(content):
            if isinstance(content, str):
                return content
            return " ".join(part.get("text", "") for part in content)

        messages = build_vision_messages(self.image)
        joined = " ".join(text_of(message["content"]) for message in messages).lower()
        for column in (
            "amount_safe_to_pay",
            "affordability_status",
            "recommended_payment_method",
            "spending_changes_needed",
            "decision_explanation",
        ):
            self.assertNotIn(column, joined)


class TestImageSchema(unittest.TestCase):
    def test_schema_is_strict_and_has_no_decision_slot(self):
        schema = build_image_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertNotIn("decision", " ".join(schema["properties"]).lower())


class TestReconcile(unittest.TestCase):
    def _r(self, verbatim, currency="INR", amount=None):
        return RawReading(
            verbatim_amount_string=verbatim, currency=currency, amount=amount
        )

    def test_agreeing_readings_return_the_first(self):
        first = self._r("4,365,000", "IDR", "4365000")
        second = self._r("4365000", "IDR", "4365000")
        reading, reason = reconcile(first, second)
        self.assertIs(reading, first)
        self.assertEqual(reason, "")

    def test_amount_disagreement_discards(self):
        reading, reason = reconcile(
            self._r("4,365,000", "IDR"), self._r("4,635,000", "IDR")
        )
        self.assertIsNone(reading)
        self.assertEqual(reason, "disagreement")

    def test_currency_disagreement_discards(self):
        reading, reason = reconcile(self._r("100", "INR"), self._r("100", "USD"))
        self.assertIsNone(reading)
        self.assertEqual(reason, "disagreement")

    def test_missing_amount_discards(self):
        reading, reason = reconcile(self._r(None, "INR"), self._r("100", "INR"))
        self.assertIsNone(reading)
        self.assertEqual(reason, "no_amount")


class TestReadingToFact(unittest.TestCase):
    def _event(self):
        from datetime import date

        from engine.types import Event

        return Event(
            event_id="event_253",
            user_id="user_03",
            event_type="income",
            description="net salary",
            category="salary",
            direction="credit",
            amount=None,
            currency="IDR",
            event_date=date(2019, 8, 31),
            settlement_date=None,
            status="settled",
            linked_event_id=None,
            flexibility="fixed",
            minimum_allowed_amount=None,
        )

    def test_parses_the_printed_string_and_builds_a_fact(self):
        fact = reading_to_fact(
            RawReading("IDR 4,365,000", "4365000", "IDR"),
            image_id="image_01",
            event=self._event(),
            request_id="request_03",
            currency="IDR",
            extractor={"provider": "opencode", "model": "m"},
        )
        self.assertEqual(fact.amount, Decimal("4365000.00"))
        self.assertEqual(fact.currency, "IDR")
        self.assertEqual(fact.verbatim_amount_string, "IDR 4,365,000")

    def test_model_amount_that_disagrees_with_the_print_is_rejected(self):
        with self.assertRaises(VisionError):
            reading_to_fact(
                RawReading("IDR 4,365,000", "4635000", "IDR"),
                image_id="image_01",
                event=self._event(),
                request_id="request_03",
                currency="IDR",
                extractor={},
            )

    def test_zero_is_rejected_and_never_becomes_a_fixture(self):
        with self.assertRaises(AmountParseError):
            reading_to_fact(
                RawReading("IDR 0", "0", "IDR"),
                image_id="image_01",
                event=self._event(),
                request_id="request_03",
                currency="IDR",
                extractor={},
            )


class TestVisionExtractor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixtures = Path(self._tmp.name) / "fixtures"
        self.dataset_dir = REPO_ROOT / "dataset"
        self.dataset = load_dataset(self.dataset_dir)
        with (self.dataset_dir / "images.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            self.images = {row["image_id"]: row for row in csv.DictReader(handle)}

    def tearDown(self):
        self._tmp.cleanup()

    def _extractor(self, readings):
        client = ScriptedVisionClient(readings, provider="opencode", model="test")
        return VisionExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
            client=client,
        )

    def _image_row(self, image_id):
        return self.images[image_id]

    def test_two_agreeing_readings_write_one_resolvable_fixture(self):
        readings = {
            "image_01": [
                RawReading("IDR 4,365,000", "4365000", "IDR"),
                RawReading("IDR 4,365,000", "4365000", "IDR"),
            ]
        }
        result = self._extractor(readings).record_image(self._image_row("image_01"))
        self.assertEqual(result.status, RECORDED)
        files = list((self.fixtures / "image").glob("*.json"))
        self.assertEqual(len(files), 1)

        reader = FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
        )
        fact = reader.image_amount("event_253")
        self.assertIsNotNone(fact)
        self.assertEqual(fact.amount, Decimal("4365000.00"))

    def test_disagreement_writes_no_fixture(self):
        readings = {
            "image_01": [
                RawReading("IDR 4,365,000", "4365000", "IDR"),
                RawReading("IDR 4,635,000", "4635000", "IDR"),
            ]
        }
        result = self._extractor(readings).record_image(self._image_row("image_01"))
        self.assertEqual(result.status, DISAGREED)
        self.assertFalse((self.fixtures / "image").exists())

    def test_zero_reading_writes_no_fixture(self):
        readings = {
            "image_01": [
                RawReading("IDR 0", "0", "IDR"),
                RawReading("IDR 0", "0", "IDR"),
            ]
        }
        result = self._extractor(readings).record_image(self._image_row("image_01"))
        self.assertEqual(result.status, FAILED)
        self.assertFalse((self.fixtures / "image").exists())

    def test_repeat_run_is_cached_and_calls_the_provider_once(self):
        readings = {
            "image_01": [
                RawReading("IDR 4,365,000", "4365000", "IDR"),
                RawReading("IDR 4,365,000", "4365000", "IDR"),
            ]
        }
        client = ScriptedVisionClient(readings, provider="opencode", model="test")
        extractor = VisionExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures,
            client=client,
        )
        first = extractor.record_image(self._image_row("image_01"))
        self.assertEqual(first.status, RECORDED)
        # The queue has no readings left; a cache hit must not call the client.
        second = extractor.record_image(self._image_row("image_01"))
        self.assertEqual(second.status, CACHED)

    def test_fixture_key_matches_the_adapters_expected_key(self):
        """The miss message's recomputed key must name the file the recorder wrote."""
        readings = {
            "image_01": [
                RawReading("IDR 4,365,000", "4365000", "IDR"),
                RawReading("IDR 4,365,000", "4365000", "IDR"),
            ]
        }
        self._extractor(readings).record_image(self._image_row("image_01"))
        written = next((self.fixtures / "image").glob("*.json")).stem

        prepared = prepare_image(
            self.dataset_dir / "media" / "images" / "image_01.png", "image_01"
        )
        payload = build_image_fixture_payload("image_01", "event_253", prepared.sha256)
        expected = fixture_key(
            "1.0.0",
            FIXTURE_PROVIDER,
            FIXTURE_MODEL,
            "image",
            "image_01",
            payload,
        )
        self.assertEqual(written, expected)

    def test_provenance_is_recorded(self):
        readings = {
            "image_01": [
                RawReading("IDR 4,365,000", "4365000", "IDR"),
                RawReading("IDR 4,365,000", "4365000", "IDR"),
            ]
        }
        self._extractor(readings).record_image(self._image_row("image_01"))
        fixture = json.loads(next((self.fixtures / "image").glob("*.json")).read_text())
        extractor_meta = fixture["facts"][0]["extractor"]
        self.assertEqual(extractor_meta["provider"], "opencode")
        self.assertEqual(extractor_meta["contract_version"], "1.0.0")
        self.assertEqual(extractor_meta["self_consistency"], "two_call_agree")
        self.assertEqual(len(extractor_meta["source_image_sha256"]), 64)


class TestImageFixturePayload(unittest.TestCase):
    def test_payload_carries_prompt_version_and_image_hash(self):
        payload = build_image_fixture_payload("image_01", "event_253", "abc123")
        self.assertEqual(payload["image_id"], "image_01")
        self.assertEqual(payload["event_id"], "event_253")
        self.assertEqual(payload["image_sha256"], "abc123")
        self.assertTrue(payload["prompt_version"])


if __name__ == "__main__":
    unittest.main()

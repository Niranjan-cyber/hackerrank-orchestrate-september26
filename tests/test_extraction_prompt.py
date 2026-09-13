"""Tests for the message-extraction prompt and strict JSON schema.

These pin the two structural guarantees the contract leans on (D19/D20):

* the schema is *strict* - every object forbids extra properties and requires every
  listed key - so the model cannot invent a field that reaches the engine;
* the untrusted message text is delivered JSON-encoded as data, never interpolated
  into an instruction.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from extraction.prompts import (  # noqa: E402
    PROMPT_VERSION,
    build_facts_schema,
    build_fixture_payload,
    build_messages,
)
from extraction.text import normalize_text  # noqa: E402
from extraction.validate import MESSAGE_FACT_TYPES  # noqa: E402

MESSAGE_ROW = {
    "message_id": "message_01",
    "user_id": "user_02",
    "request_id": "",
    "related_event_id": "",
    "sent_at": "2025-07-29T09:30:00Z",
    "source_type": "employer",
    "message_text": 'Salary rose to IDR 42750000 on 2025-08-15.\nRef "EMP-1".',
}


class TestFactsSchema(unittest.TestCase):
    def setUp(self):
        self.schema = build_facts_schema()

    def test_top_level_is_strict_object(self):
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), set(self.schema["properties"]))

    def test_fact_items_forbid_extra_properties_and_require_all(self):
        items = self.schema["properties"]["facts"]["items"]
        self.assertFalse(items["additionalProperties"])
        self.assertEqual(set(items["required"]), set(items["properties"]))

    def test_fact_type_enum_is_the_contract_25(self):
        enum = self.schema["properties"]["facts"]["items"]["properties"]["fact_type"][
            "enum"
        ]
        self.assertEqual(len(enum), 25)
        self.assertEqual(set(enum), set(MESSAGE_FACT_TYPES))
        self.assertNotIn("image_amount", enum)

    def test_currency_enum_matches_contract(self):
        currency = self.schema["properties"]["facts"]["items"]["properties"]["currency"]
        enum = {value for value in currency["enum"] if value is not None}
        self.assertEqual(enum, {"INR", "IDR", "EUR", "USD", "ZAR"})


class TestFixturePayload(unittest.TestCase):
    def test_payload_carries_prompt_version_and_message_fields(self):
        payload = build_fixture_payload(MESSAGE_ROW)
        self.assertEqual(payload["prompt_version"], PROMPT_VERSION)
        self.assertEqual(payload["message_id"], "message_01")
        self.assertEqual(payload["message_text"], MESSAGE_ROW["message_text"])
        self.assertEqual(payload["source_type"], "employer")

    def test_blank_request_and_event_ids_become_none(self):
        payload = build_fixture_payload(MESSAGE_ROW)
        self.assertIsNone(payload["request_id"])
        self.assertIsNone(payload["related_event_id"])

    def test_payload_is_json_serialisable_as_canonical_input(self):
        payload = build_fixture_payload(MESSAGE_ROW)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.assertIn("message_01", canonical)

    def test_payload_normalises_typographic_punctuation(self):
        row = dict(
            MESSAGE_ROW, message_text="You\u2019ve \u2014 \u201cread\u201d\u2026"
        )
        payload = build_fixture_payload(row)
        self.assertEqual(payload["message_text"], 'You\'ve - "read"...')


class TestNormalizeText(unittest.TestCase):
    def test_replaces_typographic_punctuation(self):
        self.assertEqual(
            normalize_text("You\u2019ve \u2014 \u201cread\u201d\u2026\u00a0x"),
            'You\'ve - "read"... x',
        )

    def test_leaves_numbers_letters_and_digits_untouched(self):
        text = "IDR 42.750.000 on 2025-08-15; pay 12% now"
        self.assertEqual(normalize_text(text), text)

    def test_is_idempotent(self):
        text = normalize_text("a\u2019b")
        self.assertEqual(normalize_text(text), text)


class TestBuildMessages(unittest.TestCase):
    def setUp(self):
        self.payload = build_fixture_payload(MESSAGE_ROW)
        self.messages = build_messages(self.payload)

    def test_two_roles_system_then_user(self):
        self.assertEqual([m["role"] for m in self.messages], ["system", "user"])

    def test_untrusted_text_is_json_encoded_not_interpolated(self):
        user_content = self.messages[1]["content"]
        encoded = json.dumps(MESSAGE_ROW["message_text"], ensure_ascii=False)
        self.assertIn(encoded, user_content)
        # The raw text contains a double quote and a real newline; its escaped form
        # must be present and the raw form must not (that would be interpolation).
        self.assertNotIn(MESSAGE_ROW["message_text"], user_content)

    def test_content_frames_the_record_as_untrusted_data(self):
        self.assertIn("untrusted", self.messages[1]["content"].lower())
        self.assertIn("data", self.messages[1]["content"].lower())

    def test_prompt_never_mentions_an_output_column(self):
        joined = " ".join(m["content"] for m in self.messages).lower()
        for column in (
            "amount_safe_to_pay",
            "affordability_status",
            "recommended_payment_method",
            "spending_changes_needed",
            "decision_explanation",
        ):
            self.assertNotIn(column, joined)


if __name__ == "__main__":
    unittest.main()

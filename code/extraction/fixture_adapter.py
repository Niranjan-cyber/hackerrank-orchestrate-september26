"""Offline fixture-backed extraction adapter.

The engine can run with no API key because every extraction result is stored as a
content-addressed JSON fixture. The key includes the contract version and prompt/
provider identity, so changing either invalidates every fixture by construction.

A live adapter (GroqExtractor, VisionExtractor) computes the same key and writes the
fixture as a side effect, making recording free. This module only reads fixtures.

See docs/contracts/extraction-fact-schema.md for the keying scheme.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from engine.money import money, parse_date
from engine.types import Dataset, Event, Fact, Request

from .validate import Violation, validate_facts

FIXTURE_PROVIDER = "fixture"
FIXTURE_MODEL = "hand_authored"


def _canonical_input(payload: dict) -> str:
    """Stable compact JSON for the variable part of the fixture key."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def fixture_key(
    contract_version: str,
    provider: str,
    model: str,
    kind: str,
    subject_id: str,
    payload: dict,
) -> str:
    """16-hex content-addressed fixture key."""
    canonical = _canonical_input(payload)
    raw = f"{contract_version}|{provider}|{model}|{kind}|{subject_id}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fact_from_json(data: dict) -> Fact:
    """Build a Fact from a fixture JSON object."""
    amount_raw = data.get("amount")
    amount = money(amount_raw) if amount_raw is not None else None
    percent_raw = data.get("percent_change")
    percent_change = money(percent_raw) if percent_raw is not None else None
    return Fact(
        fact_type=data["fact_type"],
        subject=data["subject"],
        user_id=data["user_id"],
        request_id=data.get("request_id") or None,
        related_event_id=data.get("related_event_id") or None,
        amount=amount,
        currency=data.get("currency") or None,
        percent_change=percent_change,
        effective_date=parse_date(data.get("effective_date")),
        applies_to_cycles=data.get("applies_to_cycles"),
        verbatim_quote=data.get("verbatim_quote", ""),
        verbatim_amount_string=data.get("verbatim_amount_string") or None,
        source_type=data.get("source_type", ""),
        source_language=data.get("source_language", "en"),
        confidence=data.get("confidence", "high"),
        extractor=data.get("extractor", {}),
    )


class FixtureExtractor:
    """Offline adapter: resolves facts from content-addressed fixtures.

    The adapter reads messages.csv and images.csv itself (the only code that does so
    besides live adapters), loads every fixture under ``fixtures_dir``, and returns
    validated facts per the ExtractionPort protocol.
    """

    def __init__(
        self,
        dataset: Dataset,
        dataset_dir: Path,
        fixtures_dir: Path | None = None,
        contract_version: str = "1.0.0",
    ):
        self.dataset = dataset
        self.dataset_dir = Path(dataset_dir)
        self.fixtures_dir = (
            Path(fixtures_dir) if fixtures_dir else self.dataset_dir.parent / "fixtures"
        )
        self.contract_version = contract_version

        self.messages_by_id = self._load_messages()
        self.images_by_id = self._load_images()
        self.events_by_id = self._index_events()
        self.requests_by_user = self._index_requests_by_user()
        self.blank_amount_events = self._index_blank_amount_events()

        # Load every fixture once and index by subject.
        self._fixtures: dict[str, list[Fact]] = {}
        self._violations: list[Violation] = []
        self._load_all_fixtures()

    def _load_messages(self) -> dict[str, dict[str, str]]:
        path = self.dataset_dir / "messages.csv"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {row["message_id"]: row for row in csv.DictReader(handle)}

    def _load_images(self) -> dict[str, dict[str, str]]:
        path = self.dataset_dir / "images.csv"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {row["image_id"]: row for row in csv.DictReader(handle)}

    def _index_events(self) -> dict[str, Event]:
        events: dict[str, Event] = {}
        for user_events in self.dataset.events_by_user.values():
            for event in user_events:
                events[event.event_id] = event
        return events

    def _index_requests_by_user(self) -> dict[str, Request]:
        return {request.user_id: request for request in self.dataset.requests}

    def _index_blank_amount_events(self) -> dict[str, Event]:
        """Map event_id -> Event for every event whose CSV amount is blank."""
        return {
            event.event_id: event
            for user_events in self.dataset.events_by_user.values()
            for event in user_events
            if event.amount is None
        }

    def _load_all_fixtures(self) -> None:
        """Scan ``fixtures/message`` and ``fixtures/image`` and validate every file."""
        raw_facts: list[Fact] = []
        for kind in ("message", "image"):
            kind_dir = self.fixtures_dir / kind
            if not kind_dir.exists():
                continue
            for path in kind_dir.glob("*.json"):
                try:
                    data = _load_json(path)
                except json.JSONDecodeError as exc:
                    self._violations.append(
                        Violation(
                            "PARSE",
                            path.stem,
                            "unknown",
                            f"invalid JSON in {path}: {exc}",
                        )
                    )
                    continue
                try:
                    fact = _fact_from_json(data)
                except (KeyError, ValueError, TypeError) as exc:
                    self._violations.append(
                        Violation(
                            "SHAPE",
                            path.stem,
                            data.get("fact_type", "unknown"),
                            f"cannot build Fact from {path}: {exc}",
                        )
                    )
                    continue
                raw_facts.append(fact)

        validated, violations = validate_facts(
            raw_facts,
            self.dataset,
            self.messages_by_id,
            self.images_by_id,
            self.events_by_id,
            self.requests_by_user,
        )
        self._violations.extend(violations)
        for fact in validated:
            self._fixtures.setdefault(fact.user_id, []).append(fact)
        for facts in self._fixtures.values():
            facts.sort(key=lambda f: (f.fact_type, f.subject))

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Validation problems encountered while loading fixtures."""
        return tuple(self._violations)

    def facts_for_user(self, user_id: str) -> tuple[Fact, ...]:
        """Return all validated facts for ``user_id`` in deterministic order."""
        return tuple(self._fixtures.get(user_id, ()))

    def image_amount(self, event_id: str) -> Fact | None:
        """Return the image_amount fact for a blank-amount event, or None.

        If the event has no linked image in images.csv, None is returned and the
        engine will impute. If an image is linked but no fixture exists, this is a
        miss and raises FileNotFoundError so the omission is noticed.
        """
        event = self.blank_amount_events.get(event_id)
        if event is None:
            return None

        # Find the image row that points at this event.
        image_row = next(
            (
                row
                for row in self.images_by_id.values()
                if row.get("related_event_id") == event_id
            ),
            None,
        )
        if image_row is None:
            return None

        image_id = image_row["image_id"]
        facts = self._fixtures.get(event.user_id, ())
        for fact in facts:
            if fact.fact_type == "image_amount" and fact.subject == image_id:
                return fact

        # Linked image exists but fixture is missing: hard fail with the expected key.
        payload = {"event_id": event_id, "image_id": image_id}
        key = fixture_key(
            self.contract_version,
            FIXTURE_PROVIDER,
            FIXTURE_MODEL,
            "image",
            image_id,
            payload,
        )
        raise FileNotFoundError(
            f"Fixture miss for image {image_id} / event {event_id}: "
            f"expected fixtures/image/{key}.json"
        )

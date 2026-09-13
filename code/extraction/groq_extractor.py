"""Recording extractor for the 215 message corpus.

One message, one model call, one committed fixture. The run is resumable because a
fixture that already exists is reused without calling the provider, and it is safe to
re-run: nothing is cached in-process, only on disk under a content-addressed key that
includes the prompt template and contract version.

Facts that fail contract validation are preserved in the fixture (never repaired) but
dropped from the validated stream and logged with their rule code. Nothing here decides
anything - it emits closed-enum facts and hands them to the deterministic engine.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from engine.types import Dataset, Fact

from .fixture_adapter import FixtureExtractor, fact_from_json, fixture_key
from .groq_client import DEFAULT_MODEL, GroqClient, GroqError, estimate_cost
from .prompts import build_facts_schema, build_fixture_payload, build_messages
from .usage_log import NullUsageLogger, UsageLogger
from .validate import validate_fact

RECORDED = "recorded"
CACHED = "cached"
FAILED = "failed"


@dataclass(frozen=True)
class RecordSummary:
    recorded: int = 0
    cached: int = 0
    failed: int = 0
    facts_assembled: int = 0


class GroqExtractor:
    """Records message fixtures through a live client, then reads them back offline.

    The fixture directory is the only state; deleting it forces a cold run.
    """

    def __init__(
        self,
        dataset: Dataset,
        dataset_dir: Path | str,
        fixtures_dir: Path | str,
        client: GroqClient,
        *,
        provider: str = "groq",
        model: str = DEFAULT_MODEL,
        contract_version: str = "1.0.0",
        logger: UsageLogger | NullUsageLogger | None = None,
        now=None,
        known_specs: list[tuple[str, str]] | None = None,
    ):
        self.dataset = dataset
        self.dataset_dir = Path(dataset_dir)
        self.fixtures_dir = Path(fixtures_dir)
        self.message_dir = self.fixtures_dir / "message"
        self.client = client
        self.provider = provider
        self.model = model
        self.contract_version = contract_version
        self.logger = logger or NullUsageLogger()
        self._now = now or (
            lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        # Cache detection spans every model the corpus may have been recorded with, so
        # switching model to escape a daily token cap does not re-record earlier work.
        self._known_specs = known_specs or [(provider, model)]

        # A read-only reader gives the validation context (real ids, events, requests).
        context = FixtureExtractor(
            dataset=dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures_dir,
            contract_version=contract_version,
        )
        self._messages = context.messages_by_id
        self._images = context.images_by_id
        self._events = context.events_by_id
        self._requests = context.requests_by_user

    # --- recording -------------------------------------------------------------

    def record_all(
        self, on_progress: Callable[[int, int, str], None] | None = None
    ) -> RecordSummary:
        """Record every message, tolerating per-message failures."""
        recorded = cached = failed = facts = 0
        rows = sorted(self._messages.values(), key=lambda r: r["message_id"])
        for index, row in enumerate(rows, start=1):
            status, assembled = self._record(row)
            if status == CACHED:
                cached += 1
            elif status == RECORDED:
                recorded += 1
                facts += len(assembled)
            else:
                failed += 1
            if on_progress is not None:
                on_progress(index, len(rows), status)
        return RecordSummary(
            recorded=recorded, cached=cached, failed=failed, facts_assembled=facts
        )

    def record_message(self, row: dict[str, str]) -> bool:
        """Record one message. Returns True only when a model call wrote a fixture."""
        status, _assembled = self._record(row)
        return status == RECORDED

    def _record(self, row: dict[str, str]) -> tuple[str, list[dict]]:
        message_id = row["message_id"]
        payload = build_fixture_payload(row)
        for known_provider, known_model in self._known_specs:
            known_key = fixture_key(
                self.contract_version,
                known_provider,
                known_model,
                "message",
                message_id,
                payload,
            )
            if (self.message_dir / f"{known_key}.json").exists():
                self.logger.record_call(
                    provider=known_provider,
                    model=known_model,
                    purpose="message_cache_hit",
                    subject_id=message_id,
                )
                return CACHED, []

        key = fixture_key(
            self.contract_version,
            self.provider,
            self.model,
            "message",
            message_id,
            payload,
        )
        path = self.message_dir / f"{key}.json"

        try:
            completion = self.client.complete(
                build_messages(payload), build_facts_schema()
            )
        except GroqError as exc:
            self.logger.record_call(
                provider=self.provider,
                model=self.model,
                purpose="message_fact",
                subject_id=message_id,
                retries=exc.retries,
                error=exc.error_class,
            )
            return FAILED, []

        assembled = [
            self._assemble(row, fact, key)
            for fact in completion.payload.get("facts", [])
            if isinstance(fact, dict)
        ]
        self._write_fixture(path, assembled)
        self.logger.record_call(
            provider=self.provider,
            model=self.model,
            purpose="message_fact",
            subject_id=message_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            total_tokens=completion.total_tokens,
            estimated_cost_usd=estimate_cost(
                self.model, completion.input_tokens, completion.output_tokens
            ),
            retries=completion.retries,
        )
        self._validate_and_log(assembled, message_id)
        return RECORDED, assembled

    def _assemble(self, row: dict[str, str], fact: dict, key: str) -> dict:
        return {
            "fact_type": fact.get("fact_type"),
            "subject": row["message_id"],
            "user_id": row["user_id"],
            "request_id": row.get("request_id") or None,
            "related_event_id": row.get("related_event_id") or None,
            "amount": fact.get("amount"),
            "currency": fact.get("currency"),
            "percent_change": fact.get("percent_change"),
            "effective_date": fact.get("effective_date"),
            "applies_to_cycles": fact.get("applies_to_cycles"),
            "verbatim_quote": fact.get("verbatim_quote", ""),
            "verbatim_amount_string": None,
            "source_type": row.get("source_type", ""),
            "source_language": fact.get("source_language", "en"),
            "confidence": fact.get("confidence", "high"),
            "extractor": {
                "provider": self.provider,
                "model": self.model,
                "contract_version": self.contract_version,
                "extracted_at": self._now(),
                "fixture_key": key,
            },
        }

    def _write_fixture(self, path: Path, assembled: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"facts": assembled}
        # Write then atomically replace, so a killed run never leaves a half-file that
        # would be treated as a cache hit on the next run.
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp, path)

    def _validate_and_log(self, assembled: list[dict], message_id: str) -> None:
        for raw in assembled:
            try:
                fact = fact_from_json(raw)
            except (KeyError, ValueError, TypeError, ArithmeticError):
                self.logger.record_violation(
                    subject_id=message_id,
                    rule="SHAPE",
                    fact_type=str(raw.get("fact_type", "unknown")),
                )
                continue
            _valid, violations = validate_fact(
                fact,
                self.dataset,
                self._messages,
                self._images,
                self._events,
                self._requests,
            )
            for violation in violations:
                self.logger.record_violation(
                    subject_id=message_id,
                    rule=violation.rule,
                    fact_type=violation.fact_type,
                )

    # --- ExtractionPort ---------------------------------------------------------

    def facts_for_user(self, user_id: str) -> tuple[Fact, ...]:
        rows = sorted(
            (r for r in self._messages.values() if r["user_id"] == user_id),
            key=lambda r: r["message_id"],
        )
        for row in rows:
            self._record(row)
        return self._reader().facts_for_user(user_id)

    def image_amount(self, event_id: str) -> Fact | None:
        return self._reader().image_amount(event_id)

    def _reader(self) -> FixtureExtractor:
        return FixtureExtractor(
            dataset=self.dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures_dir,
            contract_version=self.contract_version,
        )

"""Vision extraction for the 16 blank-amount events.

This is the only code path that reads a receipt image, and it is **development-time
only**: the recorded fixtures are the shipped path, so the submitted application needs
no vision key (CONTEXT D28). A future runtime provider plugs in behind `VisionClient`
and never becomes a dependency (D29).

The trust boundary is the same as the message channel, and narrower:

    PNG bytes  ->  vision model  ->  RawReading{amount_text, currency, ...}
                                          |
                             deterministic parse (amounts.parse_printed_amount)
                             digit-in-quote verification (V5)
                                          |
                                    one Fact / fixture

The model only ever returns the printed *string*; the number is computed here. A model
that transposes a digit produces a string that fails V5 rather than a plausible wrong
amount. A blank amount never becomes zero: every failure path yields no fixture, and
the engine falls back to deterministic imputation.

Two-call self-consistency: each image is read twice and the two readings must agree
after separator normalisation. A disagreement discards the result rather than guessing.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from engine.money import money, money_scale
from engine.types import Dataset, Event, Fact

from .amounts import (
    AmountParseError,
    digit_in_quote,
    normalize_separators,
    parse_printed_amount,
)
from .fixture_adapter import (
    FIXTURE_MODEL,
    FIXTURE_PROVIDER,
    FixtureExtractor,
    fixture_key,
)
from .prompts import (
    VISION_PROMPT_VERSION,
    VISION_SYSTEM_PROMPT,
    VISION_USER_PROMPT,
    build_image_fixture_payload,
)
from .usage_log import NullUsageLogger, UsageLogger
from .validate import validate_fact

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IMAGE_MIME = "image/png"

# The long-edge cap for a standard vision tier. The largest corpus image is 1628px
# wide; only images above the cap are resized, and every other image is passed through
# byte-for-byte so it is never needlessly re-encoded.
IMAGE_TIER_CAP = 1568

RECORDED = "recorded"
CACHED = "cached"
FAILED = "failed"
DISAGREED = "disagreed"


class VisionError(Exception):
    """A vision read that could not be completed. Never carries the key or content."""

    def __init__(
        self,
        error_class: str,
        *,
        status: int | None = None,
        retries: int = 0,
        detail: str = "",
    ):
        super().__init__(f"{error_class}: {detail}" if detail else error_class)
        self.error_class = error_class
        self.status = status
        self.retries = retries
        self.detail = detail


# --- image preparation --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """One image ready to send, with the provenance the fixture key needs.

    `sha256` is always the hash of the **original file**, not the resized bytes, so the
    fixture is content-addressed on the source image the recorder saw.
    """

    subject: str
    data: bytes
    mime_type: str
    sha256: str
    width: int
    height: int
    resized: bool


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG IHDR chunk without decoding the image."""
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise VisionError("not_a_png", detail="missing IHDR")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def prepare_image(
    path: Path | str, subject: str, tier_cap: int = IMAGE_TIER_CAP
) -> PreparedImage:
    """Return the bytes to send, resizing only when the long edge exceeds the cap.

    Never re-encodes to JPEG: an in-cap image is passed through untouched, and an
    oversized one is re-saved as PNG. This is deliberately the *only* place an image is
    transformed, so "never as JPEG" has a single definition.
    """
    image_path = Path(path)
    if not image_path.exists():
        raise FileNotFoundError(f"receipt image not found: {image_path}")
    data = image_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    width, height = png_dimensions(data)

    if max(width, height) <= tier_cap:
        return PreparedImage(subject, data, IMAGE_MIME, sha, width, height, False)

    resized = _resize_png(data, tier_cap)
    new_width, new_height = png_dimensions(resized)
    return PreparedImage(subject, resized, IMAGE_MIME, sha, new_width, new_height, True)


def _resize_png(data: bytes, tier_cap: int) -> bytes:
    """Downscale a PNG to the tier cap. Pillow is imported lazily and never at runtime.

    The shipped path reads fixtures and never touches an image, so this development-time
    dependency stays out of the zero-dependency runtime (D10).
    """
    try:
        from PIL import Image  # noqa: PLC0415 - deliberate lazy, dev-time dependency
    except ImportError as exc:  # pragma: no cover - exercised only with Pillow absent
        raise VisionError(
            "resize_unavailable",
            detail="Pillow is required to resize an image above the tier cap",
        ) from exc

    resampling = getattr(Image, "Resampling", Image)
    with Image.open(io.BytesIO(data)) as image:
        ratio = tier_cap / max(image.width, image.height)
        size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
        resized = image.resize(size, resampling.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue()


def build_vision_messages(image: PreparedImage) -> list[dict]:
    """System + user turns, with the image **before** the text instruction."""
    encoded = base64.b64encode(image.data).decode("ascii")
    data_uri = f"data:{image.mime_type};base64,{encoded}"
    return [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": VISION_USER_PROMPT},
            ],
        },
    ]


# --- readings and reconciliation ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawReading:
    """What one model call returns.

    `verbatim_amount_string` is the printed string; `amount` is the model's own plain
    decimal rendering of it. The number the engine uses comes from parsing the printed
    string, and the model's rendering is only ever a cross-check (V5 and equality), so a
    hallucinated figure is rejected rather than trusted.
    """

    verbatim_amount_string: str | None
    amount: str | None = None
    currency: str | None = None
    amount_in_words: str | None = None
    confidence: str = "high"

    @classmethod
    def from_payload(cls, payload: object) -> "RawReading":
        if not isinstance(payload, dict):
            raise VisionError("invalid_reading", detail="not a JSON object")
        verbatim = payload.get("verbatim_amount_string")
        amount = payload.get("amount")
        words = payload.get("amount_in_words")
        currency = payload.get("currency")
        return cls(
            verbatim_amount_string=str(verbatim) if verbatim is not None else None,
            amount=str(amount) if amount is not None else None,
            currency=str(currency) if currency else None,
            amount_in_words=str(words) if words else None,
            confidence=str(payload.get("confidence") or "high"),
        )


def _parsed_or_text(reading: RawReading) -> str:
    """A reading's identity for consistency: its parsed value, or its digits on failure."""
    verbatim = reading.verbatim_amount_string or ""
    try:
        return str(parse_printed_amount(verbatim, reading.currency))
    except AmountParseError:
        return normalize_separators(verbatim)


def _comparable(reading: RawReading) -> tuple[str, str]:
    """The separator- and case-insensitive identity two readings must share."""
    return (_parsed_or_text(reading), (reading.currency or "").upper())


def reconcile(first: RawReading, second: RawReading) -> tuple[RawReading | None, str]:
    """Two-call self-consistency. Disagreement discards rather than guesses."""
    if first.verbatim_amount_string is None or second.verbatim_amount_string is None:
        return None, "no_amount"
    if _comparable(first) != _comparable(second):
        return None, "disagreement"
    # Identical after normalisation; keep the first reading's verbatim string, which is
    # the string the V5 digit check will be run against.
    return first, ""


def _claimed_amount(raw: str | None):
    """The model's computed amount as a Decimal, or None when it is malformed."""
    if raw is None or not raw.strip():
        return None
    try:
        return money(raw)
    except (ArithmeticError, ValueError):
        return None


def reading_to_fact(
    reading: RawReading,
    *,
    image_id: str,
    event: Event,
    request_id: str | None,
    currency: str | None,
    extractor: dict,
) -> Fact:
    """Parse and verify one agreed reading into an `image_amount` Fact.

    Raises `AmountParseError` on an unparseable/zero amount and `VisionError` on a
    mismatch, so every caller failure path yields no fixture.
    """
    effective_currency = reading.currency or currency
    verbatim = reading.verbatim_amount_string or ""
    amount = money_scale(parse_printed_amount(verbatim, effective_currency))
    if amount <= 0:
        raise AmountParseError(f"image amount is not positive: {verbatim!r}")

    claimed = _claimed_amount(reading.amount)
    if reading.amount is not None and (
        claimed is None or money_scale(claimed) != amount
    ):
        raise VisionError(
            "amount_mismatch",
            detail="model amount disagrees with the printed string",
        )

    if not digit_in_quote(amount, verbatim):
        raise VisionError(
            "digit_mismatch",
            detail="parsed amount digits are absent from the printed string",
        )

    return Fact(
        fact_type="image_amount",
        subject=image_id,
        user_id=event.user_id,
        request_id=request_id or None,
        related_event_id=event.event_id,
        amount=amount,
        currency=effective_currency,
        verbatim_quote=verbatim,
        verbatim_amount_string=verbatim,
        source_type="merchant",
        source_language="en",
        confidence=reading.confidence,
        extractor=extractor,
    )


# --- clients ------------------------------------------------------------------------


class VisionClient(Protocol):
    provider: str
    model: str

    def read_amount(self, image: PreparedImage) -> RawReading: ...


class ScriptedVisionClient:
    """Replays pre-recorded readings, two per subject, for an offline recording run.

    The offline path is identical to the live one: the extractor still makes two calls
    per image and the two readings still have to agree.
    """

    def __init__(
        self,
        readings_by_subject: dict[str, list[RawReading]],
        *,
        provider: str = "opencode",
        model: str = "deepseek-v4.1-flash",
    ):
        self.provider = provider
        self.model = model
        self._queues = {
            subject: list(readings) for subject, readings in readings_by_subject.items()
        }

    def read_amount(self, image: PreparedImage) -> RawReading:
        queue = self._queues.get(image.subject)
        if not queue:
            raise VisionError(
                "no_scripted_reading", detail=f"no reading left for {image.subject}"
            )
        return queue.pop(0)


# --- the recording extractor --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImageExtraction:
    image_id: str
    event_id: str
    status: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (RECORDED, CACHED)


class VisionExtractor:
    """Reads every blank-amount event's image twice and commits one fixture per image.

    The fixture directory is the only state, so the run is resumable and re-running
    never re-calls the provider. A reading that fails validation is discarded, never
    repaired and never written as a zero.
    """

    def __init__(
        self,
        dataset: Dataset,
        dataset_dir: Path | str,
        fixtures_dir: Path | str,
        client: VisionClient,
        *,
        contract_version: str = "1.0.0",
        logger: UsageLogger | NullUsageLogger | None = None,
        tier_cap: int = IMAGE_TIER_CAP,
        now: Callable[[], str] | None = None,
    ):
        self.dataset = dataset
        self.dataset_dir = Path(dataset_dir)
        self.fixtures_dir = Path(fixtures_dir)
        self.image_dir = self.fixtures_dir / "image"
        self.client = client
        self.provider = client.provider
        self.model = client.model
        self.contract_version = contract_version
        self.tier_cap = tier_cap
        self.logger = logger or NullUsageLogger()
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())

        reader = FixtureExtractor(
            dataset=dataset,
            dataset_dir=self.dataset_dir,
            fixtures_dir=self.fixtures_dir,
            contract_version=contract_version,
        )
        self.images_by_id = reader.images_by_id
        self.events_by_id = reader.events_by_id
        self.blank_amount_events = reader.blank_amount_events
        # Lazily built and invalidated on every write, so a read after a recording run
        # sees the new fixtures without rebuilding the index on every port call.
        self._reader_cache: FixtureExtractor | None = None

    # --- recording -----------------------------------------------------------------

    def record_all(self) -> tuple[ImageExtraction, ...]:
        results = [
            self.record_image(row)
            for _image_id, row in sorted(self.images_by_id.items())
        ]
        return tuple(results)

    def record_image(self, image_row: dict[str, str]) -> ImageExtraction:
        image_id = image_row["image_id"]
        event_id = image_row.get("related_event_id") or ""
        event = self.events_by_id.get(event_id)
        if event is None or event.amount is not None:
            # Not a blank-amount event: an image_amount fact would fail V11.
            return ImageExtraction(image_id, event_id, FAILED, "not_a_blank_event")

        try:
            prepared = prepare_image(
                self.dataset_dir / "media" / "images" / f"{image_id}.png",
                image_id,
                self.tier_cap,
            )
        except (FileNotFoundError, VisionError) as exc:
            return ImageExtraction(image_id, event_id, FAILED, f"image:{exc}")

        # The key prefix uses the fixture identity, not the live client's, so the
        # adapter can recompute the expected key for its miss message. The real source
        # (provider/model/method) is recorded in the fact's provenance instead.
        payload = build_image_fixture_payload(image_id, event_id, prepared.sha256)
        key = fixture_key(
            self.contract_version,
            FIXTURE_PROVIDER,
            FIXTURE_MODEL,
            "image",
            image_id,
            payload,
        )
        path = self.image_dir / f"{key}.json"
        if path.exists():
            return ImageExtraction(image_id, event_id, CACHED, "fixture_exists")

        # Two calls. The second is the consistency check, so a provider that is
        # nondeterministic about a digit is caught rather than trusted.
        try:
            first = self.client.read_amount(prepared)
            self._log_call(image_id)
            second = self.client.read_amount(prepared)
            self._log_call(image_id)
        except VisionError as exc:
            self._log_call(image_id, error=exc.error_class)
            return ImageExtraction(image_id, event_id, FAILED, exc.error_class)

        reading, reason = reconcile(first, second)
        if reading is None:
            return ImageExtraction(image_id, event_id, DISAGREED, reason)

        extractor_meta = {
            "provider": self.provider,
            "model": self.model,
            "contract_version": self.contract_version,
            "extracted_at": self._now(),
            "fixture_key": key,
            "method": "vision",
            "self_consistency": "two_call_agree",
            "source_image_sha256": prepared.sha256,
            "prompt_version": VISION_PROMPT_VERSION,
        }
        try:
            fact = reading_to_fact(
                reading,
                image_id=image_id,
                event=event,
                request_id=image_row.get("request_id"),
                currency=event.currency,
                extractor=extractor_meta,
            )
        except (AmountParseError, VisionError) as exc:
            self.logger.record_violation(
                subject_id=image_id,
                rule="VISION",
                fact_type="image_amount",
            )
            return ImageExtraction(image_id, event_id, FAILED, str(exc))

        self._write_fixture(path, fact)
        self._validate(image_id, fact)
        return ImageExtraction(image_id, event_id, RECORDED)

    def _write_fixture(self, path: Path, fact: Fact) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"facts": [fact_to_json(fact)]}
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        # Atomic replace, so a killed run never leaves a partial file treated as a hit.
        import os  # noqa: PLC0415 - one call site, keeps the top clean

        os.replace(temp, path)
        self._reader_cache = None

    def _validate(self, image_id: str, fact: Fact) -> None:
        _valid, violations = validate_fact(
            fact,
            self.dataset,
            {},
            self.images_by_id,
            self.events_by_id,
            {r.user_id: r for r in self.dataset.requests},
        )
        for violation in violations:
            self.logger.record_violation(
                subject_id=image_id,
                rule=violation.rule,
                fact_type=violation.fact_type,
            )

    def _log_call(self, image_id: str, error: str | None = None) -> None:
        self.logger.record_call(
            provider=self.provider,
            model=self.model,
            purpose="image_amount",
            subject_id=image_id,
            error=error,
        )

    # --- ExtractionPort ------------------------------------------------------------

    def facts_for_user(self, user_id: str) -> tuple[Fact, ...]:
        return self._reader().facts_for_user(user_id)

    def image_amount(self, event_id: str) -> Fact | None:
        return self._reader().image_amount(event_id)

    def _reader(self) -> FixtureExtractor:
        if self._reader_cache is None:
            self._reader_cache = FixtureExtractor(
                dataset=self.dataset,
                dataset_dir=self.dataset_dir,
                fixtures_dir=self.fixtures_dir,
                contract_version=self.contract_version,
            )
        return self._reader_cache


def fact_to_json(fact: Fact) -> dict:
    """Serialise a Fact to the fixture JSON shape the adapter reads back."""
    return {
        "fact_type": fact.fact_type,
        "subject": fact.subject,
        "user_id": fact.user_id,
        "request_id": fact.request_id,
        "related_event_id": fact.related_event_id,
        "amount": None if fact.amount is None else str(fact.amount),
        "currency": fact.currency,
        "percent_change": None
        if fact.percent_change is None
        else str(fact.percent_change),
        "effective_date": None
        if fact.effective_date is None
        else fact.effective_date.isoformat(),
        "applies_to_cycles": fact.applies_to_cycles,
        "verbatim_quote": fact.verbatim_quote,
        "verbatim_amount_string": fact.verbatim_amount_string,
        "source_type": fact.source_type,
        "source_language": fact.source_language,
        "confidence": fact.confidence,
        "extractor": fact.extractor,
    }


def readings_from_json(document: object) -> dict[str, list[RawReading]]:
    """Build the offline `ScriptedVisionClient` input from a readings document.

    Accepts ``{"readings": {image_id: [reading, reading]}}`` where each reading is an
    object with ``amount_text``, ``currency``, and optional ``amount_in_words`` and
    ``confidence``.
    """
    if not isinstance(document, dict) or not isinstance(document.get("readings"), dict):
        raise VisionError("invalid_readings", detail="expected a 'readings' object")
    result: dict[str, list[RawReading]] = {}
    for image_id, readings in document["readings"].items():
        if not isinstance(readings, list):
            raise VisionError("invalid_readings", detail=f"{image_id} is not a list")
        result[str(image_id)] = [
            RawReading.from_payload(reading) for reading in readings
        ]
    return result


__all__ = [
    "CACHED",
    "DISAGREED",
    "FAILED",
    "IMAGE_TIER_CAP",
    "ImageExtraction",
    "PreparedImage",
    "RawReading",
    "RECORDED",
    "ScriptedVisionClient",
    "VisionClient",
    "VisionError",
    "VisionExtractor",
    "build_vision_messages",
    "fact_to_json",
    "png_dimensions",
    "prepare_image",
    "reading_to_fact",
    "readings_from_json",
    "reconcile",
]

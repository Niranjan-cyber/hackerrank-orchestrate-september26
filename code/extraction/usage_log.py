"""Append-only usage log for model calls and validation drops.

Design rules (CONTEXT.md section 16):

* append-only JSONL - a crashed run keeps its partial record;
* every write is wrapped so a logging failure never fails a run;
* only identifiers and token counts are written - never prompt content, response
  content, or credentials.

One line is written per model call and one per dropped fact. ``usage_report.md`` is
generated from this file after a run, so the numbers are measured rather than
reconstructed.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cost_to_text(value: Decimal | str | None) -> str | None:
    if value is None:
        return None
    return str(value)


class UsageSink(Protocol):
    """The two events the extraction layer emits. Both must be no-throw."""

    def record_call(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        subject_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        estimated_cost_usd: Decimal | str | None = None,
        retries: int = 0,
        error: str | None = None,
    ) -> None: ...

    def record_violation(
        self, *, subject_id: str, rule: str, fact_type: str
    ) -> None: ...


class NullUsageLogger:
    """A sink that discards everything. Used by tests and offline runs."""

    def record_call(self, **_kwargs) -> None:
        return None

    def record_violation(self, **_kwargs) -> None:
        return None


class UsageLogger:
    """Append-only JSONL writer that never raises."""

    def __init__(self, path: Path | str, now: Callable[[], str] | None = None):
        self.path = Path(path)
        self._now = now or _utc_now

    def record_call(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        subject_id: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        estimated_cost_usd: Decimal | str | None = None,
        retries: int = 0,
        error: str | None = None,
    ) -> None:
        self._write(
            {
                "timestamp": self._now(),
                "provider": provider,
                "model": model,
                "purpose": purpose,
                "subject_id": subject_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                "estimated_cost_usd": _cost_to_text(estimated_cost_usd),
                "retries": retries,
                "error": error,
            }
        )

    def record_violation(self, *, subject_id: str, rule: str, fact_type: str) -> None:
        """A fact failed contract validation and was dropped. Only the rule is logged."""
        self._write(
            {
                "timestamp": self._now(),
                "provider": "",
                "model": "",
                "purpose": "message_validation",
                "subject_id": subject_id,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "estimated_cost_usd": None,
                "retries": 0,
                "error": rule,
            }
        )

    def _write(self, record: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, sort_keys=True, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        except Exception:  # noqa: BLE001 - a logging failure must never fail a run
            return

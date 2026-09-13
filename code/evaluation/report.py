"""Ticket 15: generate ``evaluation/usage_report.md`` from ``evaluation/usage_raw.jsonl``.

    python code/evaluation/report.py

The final submitted run (``code/main.py``) reads every message and image fact from the
on-disk fixture cache (``fixtures/``) and makes **no live model call at all** - the
challenge explicitly permits a warm, cache-backed final run (CONTEXT.md D26). This
generator keeps that fact separate from the historical cost of *building* the cache
(the 215 Groq message calls and the 16 offline vision reads recorded in
``usage_raw.jsonl`` and ``fixtures/image_readings.json``), so the report never implies
a live call happened when it did not (CONTEXT.md D27, ticket 15 acceptance criteria).

Only ids, token counts, and cost estimates are read from the log - never prompt or
response content (see ``extraction/usage_log.py``).
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DEFAULT_USAGE_LOG = CODE_DIR / "evaluation" / "usage_raw.jsonl"
DEFAULT_REPORT = CODE_DIR / "evaluation" / "usage_report.md"
DEFAULT_FIXTURES_DIR = REPO_ROOT / "fixtures"
DEFAULT_IMAGE_READINGS = DEFAULT_FIXTURES_DIR / "image_readings.json"
DEFAULT_REQUESTS_CSV = REPO_ROOT / "dataset" / "requests.csv"

MESSAGE_FACT = "message_fact"
MESSAGE_CACHE_HIT = "message_cache_hit"
MESSAGE_VALIDATION = "message_validation"


def load_records(path: Path | str) -> list[dict]:
    """Read every non-blank JSONL line. Missing file reads as no records."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


@dataclass(frozen=True)
class ModelStats:
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class MessageExtractionSummary:
    attempted: int
    succeeded: int
    failed: int
    cache_hits: int
    dropped_facts: int
    error_counts: dict[str, int]
    violation_counts: dict[str, int]
    per_model: tuple[ModelStats, ...]
    total: ModelStats


def _empty_stats(provider: str = "", model: str = "") -> ModelStats:
    return ModelStats(
        provider=provider,
        model=model,
        calls=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=Decimal("0"),
    )


def summarize_message_extraction(records: list[dict]) -> MessageExtractionSummary:
    """Split the raw log into successes, failures, cache hits, and dropped facts.

    A ``message_fact`` line is a live-call attempt: it succeeded (has token counts)
    or failed (``error`` set, no tokens - the call still happened, it just was not
    billable). ``message_cache_hit`` means no call was made at all - a fixture the
    corpus already had was reused. ``message_validation`` is a fact dropped after
    contract validation, not a model call.
    """
    per_model: dict[tuple[str, str], dict] = {}
    attempted = succeeded = failed = cache_hits = dropped_facts = 0
    error_counts: dict[str, int] = {}
    violation_counts: dict[str, int] = {}
    total_input = total_output = total_tokens = 0
    total_cost = Decimal("0")

    for record in records:
        purpose = record.get("purpose")
        if purpose == MESSAGE_FACT:
            attempted += 1
            if record.get("error"):
                failed += 1
                error_counts[record["error"]] = error_counts.get(record["error"], 0) + 1
                continue
            succeeded += 1
            key = (record.get("provider") or "", record.get("model") or "")
            bucket = per_model.setdefault(
                key, {"calls": 0, "input": 0, "output": 0, "total": 0, "cost": Decimal("0")}
            )
            bucket["calls"] += 1
            input_tokens = record.get("input_tokens") or 0
            output_tokens = record.get("output_tokens") or 0
            call_total = record.get("total_tokens") or (input_tokens + output_tokens)
            cost = (
                Decimal(record["estimated_cost_usd"])
                if record.get("estimated_cost_usd") is not None
                else Decimal("0")
            )
            bucket["input"] += input_tokens
            bucket["output"] += output_tokens
            bucket["total"] += call_total
            bucket["cost"] += cost
            total_input += input_tokens
            total_output += output_tokens
            total_tokens += call_total
            total_cost += cost
        elif purpose == MESSAGE_CACHE_HIT:
            cache_hits += 1
        elif purpose == MESSAGE_VALIDATION:
            dropped_facts += 1
            rule = record.get("error") or "UNKNOWN"
            violation_counts[rule] = violation_counts.get(rule, 0) + 1

    per_model_stats = tuple(
        ModelStats(
            provider=provider,
            model=model,
            calls=bucket["calls"],
            input_tokens=bucket["input"],
            output_tokens=bucket["output"],
            total_tokens=bucket["total"],
            cost_usd=bucket["cost"],
        )
        for (provider, model), bucket in sorted(per_model.items())
    )
    total = ModelStats(
        provider="(all)",
        model="(all)",
        calls=succeeded,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_tokens,
        cost_usd=total_cost,
    )
    return MessageExtractionSummary(
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        cache_hits=cache_hits,
        dropped_facts=dropped_facts,
        error_counts=error_counts,
        violation_counts=violation_counts,
        per_model=per_model_stats,
        total=total,
    )


def count_fixtures(fixtures_dir: Path | str) -> dict[str, int]:
    """Count committed fixture files by kind - what the final run actually reads."""
    fixtures_dir = Path(fixtures_dir)
    counts = {}
    for kind in ("message", "image"):
        directory = fixtures_dir / kind
        counts[kind] = len(list(directory.glob("*.json"))) if directory.exists() else 0
    return counts


def load_image_provenance(path: Path | str) -> dict | None:
    """Read the offline vision-reading provenance (provider/model), if present."""
    path = Path(path)
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        "provider": document.get("provider", "unknown"),
        "model": document.get("model", "unknown"),
        "method": document.get("method", "unknown"),
    }


def count_requests(requests_csv: Path | str) -> int:
    path = Path(requests_csv)
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _fmt_cost(value: Decimal) -> str:
    return f"${value:.6f}" if value != 0 else "$0.00"


def _avg(total: int, count: int) -> str:
    if count == 0:
        return "0"
    quotient = Decimal(total) / Decimal(count)
    if quotient == quotient.to_integral_value():
        return str(quotient.to_integral_value())
    return str(quotient.quantize(Decimal("0.01")))


def _avg_cost(total: Decimal, count: int) -> str:
    if count == 0:
        return _fmt_cost(Decimal("0"))
    return _fmt_cost(total / Decimal(count))


def render_report(
    *,
    message_summary: MessageExtractionSummary,
    fixture_counts: dict[str, int],
    image_provenance: dict | None,
    request_count: int,
) -> str:
    fixture_total = fixture_counts.get("message", 0) + fixture_counts.get("image", 0)
    lines: list[str] = []
    lines.append("# Usage Report - Buy or Wait?")
    lines.append("")
    lines.append(
        f"Covers the final full-dataset run of `code/main.py` that produced the "
        f"submitted `output.csv` ({request_count} requests)."
    )
    lines.append("")

    lines.append("## 1. Live model calls made by this run")
    lines.append("")
    lines.append(
        "**0 live model calls.** The challenge permits a warm, cache-backed final run "
        "(no cold run required). Every extraction fact this run needed was already "
        "present on disk as a committed fixture, so `code/main.py` never imports a "
        "network client and never spends a token or a dollar."
    )
    lines.append("")
    lines.append("| Provider | Model | Live calls | Cost |")
    lines.append("|---|---|---|---|")
    lines.append("| - | - | 0 | $0.00 |")
    lines.append("")

    lines.append("## 2. Fixture / cache hits used by this run")
    lines.append("")
    lines.append(
        f"| Fact type | Cache hits used by this run |\n"
        f"|---|---|\n"
        f"| message_fact (from `fixtures/message/`) | {fixture_counts.get('message', 0)} |\n"
        f"| image_amount (from `fixtures/image/`) | {fixture_counts.get('image', 0)} |\n"
        f"| **Total** | **{fixture_total}** |"
    )
    lines.append("")
    lines.append(
        f"{fixture_total} facts served from cache across {request_count} evaluation "
        "requests."
    )
    lines.append("")

    lines.append("## 3. Fixture-cache provenance (historical, not this run's cost)")
    lines.append("")
    lines.append(
        "The figures below describe **when the fixture cache was built** "
        "(`code/extraction/record.py`, tickets 11-12) - a one-time, development-time "
        "cost. They are **not** incurred by this run and are reported separately, as "
        "the challenge's cost-analysis ask (`problem_statement.md`) still expects a "
        "true accounting of every model call the system ever made to produce its "
        "answers."
    )
    lines.append("")
    lines.append("### 3.1 Message extraction (Groq)")
    lines.append("")
    lines.append(
        f"{message_summary.attempted} call attempts: {message_summary.succeeded} "
        f"succeeded (one fixture each), {message_summary.failed} failed and were "
        f"retried or escalated to a different model. {message_summary.cache_hits} "
        "messages were already cached during recording. "
        f"{message_summary.dropped_facts} extracted facts were dropped after failing "
        "contract validation (never written as a fixture, never a model call)."
    )
    lines.append("")
    lines.append("| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Cost |")
    lines.append("|---|---|---|---|---|---|---|")
    for stats in message_summary.per_model:
        lines.append(
            f"| {stats.provider} | {stats.model} | {stats.calls} | "
            f"{stats.input_tokens} | {stats.output_tokens} | {stats.total_tokens} | "
            f"{_fmt_cost(stats.cost_usd)} |"
        )
    total = message_summary.total
    lines.append(
        f"| **(all)** | **(all)** | **{total.calls}** | **{total.input_tokens}** | "
        f"**{total.output_tokens}** | **{total.total_tokens}** | "
        f"**{_fmt_cost(total.cost_usd)}** |"
    )
    lines.append("")
    lines.append(
        f"Average per successful call: {_avg(total.input_tokens, total.calls)} input "
        f"tokens, {_avg(total.output_tokens, total.calls)} output tokens, "
        f"{_avg(total.total_tokens, total.calls)} total tokens, "
        f"{_avg_cost(total.cost_usd, total.calls)} cost."
    )
    lines.append(
        f"Averaged over all {request_count} evaluation requests instead (AGENTS.md "
        f"6.5's \"per request\" framing): {_avg(total.total_tokens, request_count)} "
        f"total tokens/request, {_avg_cost(total.cost_usd, request_count)}/request - "
        "this is the historical cache-build cost amortized over the requests it "
        "serves, not a cost this run itself paid (see section 4)."
    )
    if message_summary.error_counts:
        lines.append("")
        lines.append("Failed-call error classes: " + ", ".join(
            f"`{error}` x{count}" for error, count in sorted(message_summary.error_counts.items())
        ) + ".")
    if message_summary.violation_counts:
        lines.append("")
        lines.append("Dropped-fact validation rules: " + ", ".join(
            f"`{rule}` x{count}" for rule, count in sorted(message_summary.violation_counts.items())
        ) + ".")
    lines.append("")

    lines.append("### 3.2 Image amount extraction (vision)")
    lines.append("")
    if image_provenance is None:
        lines.append(
            f"{fixture_counts.get('image', 0)} image-amount fixtures are committed; "
            "no provenance file was found."
        )
    else:
        lines.append(
            f"Groq has no multimodal model (verified; see "
            "`docs/investigation/provider-capability.md`), so the 16 blank amounts "
            f"were read offline through **{image_provenance['provider']}** using "
            f"**{image_provenance['model']}** "
            f"({image_provenance.get('method', 'vision')}), and committed as "
            "fixtures under `fixtures/image/`. This was a one-time, human-driven "
            "session outside the submitted application's own credentials, so no "
            "token count or dollar cost was captured for it - reported here as "
            "**not tracked** rather than estimated, per the same honesty standard "
            "applied to every other number in this file."
        )
    lines.append("")

    lines.append("## 4. Grand total")
    lines.append("")
    lines.append(
        f"- This run (`output.csv`, {request_count} requests): **0 calls, $0.00** "
        "(0 tokens/request, $0.00/request).\n"
        f"- Lifetime cost to build the cache this run replays: "
        f"**{total.calls} calls, {_fmt_cost(total.cost_usd)}** "
        f"({_avg(total.total_tokens, request_count)} tokens/request, "
        f"{_avg_cost(total.cost_usd, request_count)}/request; message extraction "
        "only, image extraction cost not tracked, see 3.2)."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usage-log", type=Path, default=DEFAULT_USAGE_LOG)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--image-readings", type=Path, default=DEFAULT_IMAGE_READINGS)
    parser.add_argument("--requests-csv", type=Path, default=DEFAULT_REQUESTS_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    records = load_records(args.usage_log)
    message_summary = summarize_message_extraction(records)
    fixture_counts = count_fixtures(args.fixtures_dir)
    image_provenance = load_image_provenance(args.image_readings)
    request_count = count_requests(args.requests_csv)

    report = render_report(
        message_summary=message_summary,
        fixture_counts=fixture_counts,
        image_provenance=image_provenance,
        request_count=request_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8", newline="\n")
    print(f"usage report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

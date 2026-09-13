"""Buy or Wait? - entry point.

    python code/main.py

This is the imperative shell, and the only place the I/O sequence lives:

    load dataset -> extraction / fixtures -> run_pipeline -> validate -> output.csv

It contains no financial logic. That is the acceptance test for the seam: if an `if`
statement about money, dates or affordability appears in this file, it belongs in the
core instead.

Ticket 01 passes an empty fact tuple, so no extraction code and no API is involved.
Ticket 03 supplies the fixture-backed `ExtractionPort` that fills it in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.loaders import load_dataset  # noqa: E402
from engine.pipeline import run_pipeline, trace_request  # noqa: E402
from engine.trace import render_ledger, trace_payload  # noqa: E402
from engine.types import Config, Dataset, Fact  # noqa: E402
from engine.validate import validate_and_write  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def gather_facts(
    dataset: Dataset, dataset_dir: Path, fixtures_dir: Path | None
) -> tuple[Fact, ...]:
    """Evidence facts for the whole run.

    Ticket 01: empty. The core takes facts as a *value*, so the tracer bullet needs no
    extraction code at all - which is what lets ticket 01 run with no credential.
    Ticket 03 replaces this with a fixture-backed ExtractionPort.
    """
    if fixtures_dir is None:
        return ()

    # Import here so the tracer-bullet path (empty facts) does not depend on the
    # extraction package at all.
    from extraction.fixture_adapter import FixtureExtractor  # noqa: E402

    extractor = FixtureExtractor(
        dataset=dataset,
        dataset_dir=dataset_dir,
        fixtures_dir=fixtures_dir,
    )
    facts = tuple(
        fact
        for request in dataset.requests
        for fact in extractor.facts_for_user(request.user_id)
    )
    if extractor.violations:
        print(f"extraction warnings  : {len(extractor.violations)}")
        for violation in extractor.violations[:10]:
            print(f"  [{violation.rule}] {violation.subject}: {violation.detail}")
        if len(extractor.violations) > 10:
            print(f"  ... and {len(extractor.violations) - 10} more")
    return facts


def _trace(
    dataset: Dataset,
    facts: tuple[Fact, ...],
    config: Config,
    request_id: str,
    trace_out: Path | None,
) -> int:
    """Ticket 09: print one request's decision and day-by-day ledger.

    Deliberately does not call `validate_and_write` - a trace is a developer-facing
    diagnostic, and `code/engine/validate.py` stays the sole writer of `output.csv`.
    """
    trace = trace_request(request_id, dataset, facts, config)
    row = trace.row

    print(f"request_id         : {row.request_id}")
    print(f"affordability      : {row.affordability_status}")
    print(f"payment_method     : {row.recommended_payment_method}")
    print(f"amount_safe_to_pay : {row.amount_safe_to_pay}")
    print(f"decision_explanation:\n  {row.decision_explanation}")
    print()
    print(render_ledger(trace.ledger))

    if trace_out is not None:
        payload = trace_payload(request_id, trace.ledger, row.reasons)
        trace_out.parent.mkdir(parents=True, exist_ok=True)
        trace_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\ntrace written      : {trace_out}")

    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Buy or Wait? - produce output.csv for a requests file."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=REPO_ROOT / "dataset",
        help="Directory containing the participant-facing CSVs (default: dataset).",
    )
    parser.add_argument(
        "--requests-csv",
        type=Path,
        default=None,
        help="Requests file to process. Defaults to <dataset-dir>/requests.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "output.csv",
        help="Where to write the output CSV (default: output.csv).",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=REPO_ROOT / "fixtures",
        help="Directory of extraction fixtures (default: fixtures).",
    )
    parser.add_argument(
        "--no-extraction",
        action="store_true",
        help="Run with empty facts, matching the ticket 01 tracer bullet.",
    )
    parser.add_argument(
        "--trace-request",
        default=None,
        help=(
            "Print the day-by-day ledger for one request_id and skip writing "
            "output.csv. Off the output.csv path entirely (Ticket 09)."
        ),
    )
    parser.add_argument(
        "--trace-out",
        type=Path,
        default=None,
        help=(
            "With --trace-request, also write the full JSON trace (ledger + "
            "reasons) to this path."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = Config()

    dataset_dir = Path(args.dataset_dir)
    requests_path = (
        Path(args.requests_csv) if args.requests_csv else dataset_dir / "requests.csv"
    )
    output_path = Path(args.output)

    dataset = load_dataset(dataset_dir, requests_path)
    fixtures_dir = None if args.no_extraction else Path(args.fixtures_dir)
    facts = gather_facts(dataset, dataset_dir, fixtures_dir)

    if args.trace_request is not None:
        return _trace(dataset, facts, config, args.trace_request, args.trace_out)

    rows = run_pipeline(dataset, facts, config)
    violations = validate_and_write(rows, dataset, output_path)

    print(f"requests read      : {len(dataset.requests)}")
    print(f"rows written       : {len(rows)}")
    print(f"output             : {output_path}")

    if violations:
        print(f"\nvalidation violations: {len(violations)}")
        for violation in violations[:20]:
            print(f"  [{violation.rule}] {violation.request_id}: {violation.detail}")
        if len(violations) > 20:
            print(f"  ... and {len(violations) - 20} more")
        return 1

    print("validation         : clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Sample scorecard harness (ticket 02).

    python code/evaluation/main.py
    python code/evaluation/main.py --update

Default run:
  - drives the 25 solved samples through the full pipeline
  - compares the generated output against sample_requests.csv
  - prints a per-column scorecard and a per-request mismatch table
  - exits non-zero if any non-explanation column regresses

--update run:
  - drives the unlabelled 250 evaluation requests through the full pipeline
  - writes fixtures/golden_requests.csv so accepted deviations show as a commit diff
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIN_SCRIPT = REPO_ROOT / "code" / "main.py"
DATASET_DIR = REPO_ROOT / "dataset"
SAMPLE_CSV = DATASET_DIR / "sample_requests.csv"
REQUESTS_CSV = DATASET_DIR / "requests.csv"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
FIXTURES_DIR = REPO_ROOT / "fixtures"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.csv"
GOLDEN_OUTPUT = FIXTURES_DIR / "golden_requests.csv"

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

NON_EXPLANATION_COLUMNS = [c for c in OUTPUT_COLUMNS if c != "decision_explanation"]

CURRENCIES = {"INR", "IDR", "EUR", "USD", "ZAR"}


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _run_pipeline(requests_csv: Path, output_csv: Path) -> int:
    """Invoke code/main.py as a subprocess; return its exit code."""
    cmd = [
        sys.executable,
        str(MAIN_SCRIPT),
        "--dataset-dir",
        str(DATASET_DIR),
        "--requests-csv",
        str(requests_csv),
        "--output",
        str(output_csv),
    ]
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    return result.returncode


def _normalize_money(text: str) -> Decimal | None:
    """Parse a money string, tolerating comma thousands separators."""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normalize_field(name: str, value: str) -> str:
    """Normalize a cell for comparison."""
    value = (value or "").strip()
    if name == "amount_safe_to_pay":
        parsed = _normalize_money(value)
        return str(parsed) if parsed is not None else ""
    if name in {"payment_plan", "spending_changes_needed"}:
        return value if value else "none"
    if name == "earliest_date_for_full_payment":
        return value
    return value


def _extract_financial_facts(text: str) -> frozenset[tuple[str, Decimal]]:
    """Pull (currency, amount) pairs from an explanation for structural comparison."""
    facts: set[tuple[str, Decimal]] = set()
    # Match amounts with optional commas and decimals.
    amount_pattern = re.compile(r"[\d,]+(?:\.\d+)?")
    for match in amount_pattern.finditer(text):
        amount_text = match.group(0).replace(",", "")
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            continue
        # Look for a currency code immediately before or after the amount.
        window_start = max(0, match.start() - 10)
        window_end = min(len(text), match.end() + 10)
        window = text[window_start:window_end]
        found_currency = None
        for currency in CURRENCIES:
            if currency in window:
                found_currency = currency
                break
        if found_currency:
            facts.add((found_currency, amount))
    return frozenset(facts)


def _compare_explanation(expected: str, actual: str) -> tuple[bool, str]:
    """Structural comparison for decision_explanation.

    Returns (matches, note). A match means both strings mention the same
    (currency, amount) facts; wording differences are ignored.
    """
    expected_facts = _extract_financial_facts(expected)
    actual_facts = _extract_financial_facts(actual)
    if expected_facts == actual_facts:
        return True, "structural facts match"
    return False, f"expected facts {expected_facts!r}, actual {actual_facts!r}"


def _build_scorecard(
    golden_rows: list[dict[str, str]], actual_rows: list[dict[str, str]]
) -> dict:
    """Compare golden and actual rows and return a structured result."""
    golden_by_id = {row["request_id"]: row for row in golden_rows}
    actual_by_id = {row["request_id"]: row for row in actual_rows}

    all_ids = sorted(set(golden_by_id) | set(actual_by_id))
    column_hits = Counter()
    column_misses = Counter()
    mismatches: list[dict] = []
    explanation_results: list[dict] = []

    for request_id in all_ids:
        expected_row = golden_by_id.get(request_id, {})
        actual_row = actual_by_id.get(request_id, {})

        if request_id not in actual_by_id:
            for col in NON_EXPLANATION_COLUMNS:
                column_misses[col] += 1
            mismatches.append(
                {
                    "request_id": request_id,
                    "field": "<missing row>",
                    "expected": "<present>",
                    "actual": "<missing>",
                }
            )
            continue

        if request_id not in golden_by_id:
            for col in NON_EXPLANATION_COLUMNS:
                column_misses[col] += 1
            mismatches.append(
                {
                    "request_id": request_id,
                    "field": "<extra row>",
                    "expected": "<missing>",
                    "actual": "<present>",
                }
            )
            continue

        for col in OUTPUT_COLUMNS:
            expected = _normalize_field(col, expected_row.get(col, ""))
            actual = _normalize_field(col, actual_row.get(col, ""))

            if col == "decision_explanation":
                matches, note = _compare_explanation(expected, actual)
                if not matches:
                    column_misses[col] += 1
                else:
                    column_hits[col] += 1
                explanation_results.append(
                    {
                        "request_id": request_id,
                        "matches": matches,
                        "note": note,
                    }
                )
                continue

            if expected == actual:
                column_hits[col] += 1
            else:
                column_misses[col] += 1
                mismatches.append(
                    {
                        "request_id": request_id,
                        "field": col,
                        "expected": expected_row.get(col, ""),
                        "actual": actual_row.get(col, ""),
                    }
                )

    return {
        "column_hits": column_hits,
        "column_misses": column_misses,
        "mismatches": mismatches,
        "explanation_results": explanation_results,
        "total_ids": len(all_ids),
    }


def _print_scorecard(result: dict) -> None:
    print("\n=== Per-column scorecard ===")
    print(f"{'column':<30} {'matched':>8} {'mismatched':>12}")
    print("-" * 52)
    for col in OUTPUT_COLUMNS:
        matched = result["column_hits"].get(col, 0)
        mismatched = result["column_misses"].get(col, 0)
        print(f"{col:<30} {matched:>8} {mismatched:>12}")

    print("\n=== Structural decision_explanation summary ===")
    explanation_matches = sum(1 for r in result["explanation_results"] if r["matches"])
    explanation_total = len(result["explanation_results"])
    print(f"matches: {explanation_matches}/{explanation_total}")

    mismatches = result["mismatches"]
    if mismatches:
        print(f"\n=== Per-request mismatches ({len(mismatches)} total) ===")
        print(f"{'request_id':<14} {'field':<28} {'expected':<30} {'actual':<30}")
        print("-" * 104)
        for m in mismatches:
            print(
                f"{m['request_id']:<14} {m['field']:<28} "
                f"{m['expected'][:28]:<30} {m['actual'][:28]:<30}"
            )
    else:
        print("\n=== No mismatches ===")


def _default_mode() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Running pipeline on sample_requests.csv ...")
    pipeline_exit = _run_pipeline(SAMPLE_CSV, SAMPLE_OUTPUT)
    if pipeline_exit != 0:
        print(f"Pipeline exited with code {pipeline_exit}; aborting scorecard.")
        return pipeline_exit

    golden_rows = _load_csv(SAMPLE_CSV)
    actual_rows = _load_csv(SAMPLE_OUTPUT)
    result = _build_scorecard(golden_rows, actual_rows)
    _print_scorecard(result)

    non_explanation_misses = sum(
        result["column_misses"].get(col, 0) for col in NON_EXPLANATION_COLUMNS
    )
    if non_explanation_misses:
        print(f"\nFAIL: {non_explanation_misses} non-explanation field(s) regressed.")
        return 1

    print("\nPASS: all non-explanation columns match the sample golden file.")
    return 0


def _update_mode() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print("Running pipeline on requests.csv to regenerate golden file ...")
    pipeline_exit = _run_pipeline(REQUESTS_CSV, GOLDEN_OUTPUT)
    if pipeline_exit != 0:
        print(f"Pipeline exited with code {pipeline_exit}; golden file not updated.")
        return pipeline_exit

    print(f"Golden file written: {GOLDEN_OUTPUT}")
    print("Commit this file so future accepted deviations appear as diffs.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sample scorecard harness for Buy or Wait?"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Regenerate fixtures/golden_requests.csv from the unlabelled 250 requests "
            "instead of scoring the samples."
        ),
    )
    args = parser.parse_args(argv)

    if args.update:
        return _update_mode()
    return _default_mode()


if __name__ == "__main__":
    raise SystemExit(main())

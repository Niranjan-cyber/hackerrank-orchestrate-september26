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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.loaders import load_dataset  # noqa: E402
from engine.pipeline import run_pipeline  # noqa: E402
from engine.types import Config, Fact  # noqa: E402
from engine.validate import validate_and_write  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_PATH = REPO_ROOT / "output.csv"


def gather_facts() -> tuple[Fact, ...]:
    """Evidence facts for the whole run.

    Ticket 01: empty. The core takes facts as a *value*, so the tracer bullet needs no
    extraction code at all - which is what lets ticket 01 run with no credential.
    Ticket 03 replaces this with a fixture-backed ExtractionPort.
    """
    return ()


def main() -> int:
    config = Config()

    dataset = load_dataset(DATASET_DIR)
    facts = gather_facts()
    rows = run_pipeline(dataset, facts, config)
    violations = validate_and_write(rows, dataset, OUTPUT_PATH)

    print(f"requests read      : {len(dataset.requests)}")
    print(f"rows written       : {len(rows)}")
    print(f"output             : {OUTPUT_PATH}")

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

"""Development-time CLI: record the 16 blank-amount image fixtures.

    python code/extraction/record_images.py
    python code/extraction/record_images.py --readings fixtures/image_readings.json

No vision key is exportable from this environment, so the shipped recording replays
two pre-captured readings per image through the extractor's normal two-call path
(docs/investigation/provider-capability.md section 3, option 2): `reconcile` still
requires the two readings to agree, and the parse, digit check, and fixture write are
identical to a live run. A live provider is added later by implementing `VisionClient`
and building a `VisionExtractor` with it; it never becomes a runtime dependency (D29).

The run is resumable - a fixture that already exists is reused - and a reading that
fails validation is discarded, never written as a zero.

Run from the repository root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from engine.loaders import load_dataset  # noqa: E402
from extraction.fixture_adapter import FixtureExtractor  # noqa: E402
from extraction.vision import ScriptedVisionClient, VisionExtractor, readings_from_json  # noqa: E402

DEFAULT_READINGS = REPO_ROOT / "fixtures" / "image_readings.json"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=REPO_ROOT / "dataset")
    parser.add_argument("--fixtures-dir", type=Path, default=REPO_ROOT / "fixtures")
    parser.add_argument(
        "--readings",
        type=Path,
        default=DEFAULT_READINGS,
        help="Two captured readings per image (default: fixtures/image_readings.json).",
    )
    parser.add_argument("--contract-version", default="1.0.0")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.readings.exists():
        print(f"no readings file at {args.readings}", file=sys.stderr)
        return 2

    document = json.loads(args.readings.read_text(encoding="utf-8"))
    client = ScriptedVisionClient(
        readings_from_json(document),
        provider=str(document.get("provider") or "opencode"),
        model=str(document.get("model") or "scripted"),
    )
    print(f"offline recording from {args.readings}")

    dataset = load_dataset(args.dataset_dir)
    extractor = VisionExtractor(
        dataset=dataset,
        dataset_dir=args.dataset_dir,
        fixtures_dir=args.fixtures_dir,
        client=client,
        contract_version=args.contract_version,
    )
    results = extractor.record_all()

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(f"images             : {len(results)}")
    for status in sorted(counts):
        print(f"  {status:<14}: {counts[status]}")
    for result in results:
        if result.status not in ("recorded", "cached"):
            print(
                f"  not recorded: {result.image_id} [{result.status}] {result.reason}"
            )

    reader = FixtureExtractor(
        dataset=dataset,
        dataset_dir=args.dataset_dir,
        fixtures_dir=args.fixtures_dir,
        contract_version=args.contract_version,
    )
    resolved = 0
    for event_id in sorted(reader.blank_amount_events):
        if reader.image_amount(event_id) is not None:
            resolved += 1
    blanks = len(reader.blank_amount_events)
    print(f"blank events with an image fixture : {resolved}/{blanks}")

    failures = counts.get("failed", 0) + counts.get("disagreed", 0)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

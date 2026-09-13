"""Development-time CLI: record the 215 message fixtures from Groq.

    python code/extraction/record.py

Reads ``GROQ_API_KEY`` from the environment. One call per message, retry with
backoff, resumable because a fixture that already exists is reused. This is the only
code path in the whole solution that makes a text-model call; the shipped runtime uses
``FixtureExtractor`` and needs no key.

Run from the repository root. Pass ``--usage-log`` to write elsewhere (default:
``code/evaluation/usage_raw.jsonl``).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from engine.loaders import load_dataset  # noqa: E402
from extraction.fixture_adapter import FixtureExtractor  # noqa: E402
from extraction.groq_client import DEFAULT_MODEL, GroqClient, GroqError  # noqa: E402
from extraction.groq_extractor import CACHED, GroqExtractor  # noqa: E402
from extraction.usage_log import UsageLogger  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=REPO_ROOT / "dataset")
    parser.add_argument("--fixtures-dir", type=Path, default=REPO_ROOT / "fixtures")
    parser.add_argument(
        "--usage-log", type=Path, default=CODE_DIR / "evaluation" / "usage_raw.jsonl"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--contract-version", default="1.0.0")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Generous cap (see provider notes).",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high"),
        default=None,
        help="Passed through for reasoning models such as gpt-oss.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help=(
            "Seconds to sleep after each real model call. Groq's free tier is 8000 "
            "tokens/minute, so a pause keeps a run inside the limit instead of "
            "retrying. Cached messages never pause."
        ),
    )
    parser.add_argument(
        "--known-model",
        action="append",
        default=[],
        metavar="MODEL",
        help=(
            "A model this corpus may already have fixtures for. Cache detection checks "
            "these so switching model to escape a daily token cap does not re-record "
            "earlier work. Repeatable; --model is always included."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print(
            "GROQ_API_KEY is not set; refusal to run live extraction.", file=sys.stderr
        )
        return 2

    dataset = load_dataset(args.dataset_dir)
    extra_body = (
        {"reasoning_effort": args.reasoning_effort} if args.reasoning_effort else {}
    )
    client = GroqClient(
        api_key=api_key,
        model=args.model,
        max_retries=6,
        base_delay=2.0,
        max_delay=60.0,
        max_tokens=args.max_tokens,
        extra_body=extra_body,
    )
    logger = UsageLogger(args.usage_log)
    known_specs = [
        ("groq", model) for model in dict.fromkeys([*args.known_model, args.model])
    ]
    extractor = GroqExtractor(
        dataset=dataset,
        dataset_dir=args.dataset_dir,
        fixtures_dir=args.fixtures_dir,
        client=client,
        model=args.model,
        contract_version=args.contract_version,
        logger=logger,
        known_specs=known_specs,
    )

    def progress(index: int, total: int, status: str) -> None:
        if status != CACHED and args.pause:
            time.sleep(args.pause)
        if index % 25 == 0 or index == total:
            print(f"  [{index}/{total}] last={status}", flush=True)

    try:
        summary = extractor.record_all(on_progress=progress)
    except GroqError as exc:
        print(f"extraction aborted: {exc.error_class}", file=sys.stderr)
        return 1

    print(f"recorded           : {summary.recorded}")
    print(f"cached             : {summary.cached}")
    print(f"failed             : {summary.failed}")
    print(f"facts assembled    : {summary.facts_assembled}")

    reader = FixtureExtractor(
        dataset=dataset,
        dataset_dir=args.dataset_dir,
        fixtures_dir=args.fixtures_dir,
        contract_version=args.contract_version,
    )
    user_ids = {row["user_id"] for row in reader.messages_by_id.values()}
    validated = sum(len(reader.facts_for_user(user_id)) for user_id in sorted(user_ids))
    print(f"validated facts    : {validated}")
    print(f"violations         : {len(reader.violations)}")
    for violation in reader.violations[:15]:
        print(f"  [{violation.rule}] {violation.subject}: {violation.detail}")
    if len(reader.violations) > 15:
        print(f"  ... and {len(reader.violations) - 15} more")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

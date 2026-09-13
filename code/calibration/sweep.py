"""Ticket 14: sweep the unfrozen forecasting parameters against the 25 solved samples.

    python code/calibration/sweep.py --baseline
    python code/calibration/sweep.py --axes
    python code/calibration/sweep.py --coordinate
    python code/calibration/sweep.py --confirm
    python code/calibration/sweep.py --grid

Why this exists rather than looping the ticket 02 harness: that harness re-loads the
dataset, re-runs extraction and round-trips a CSV per configuration, which is about a
second a point. `run_pipeline` is a pure function of `(dataset, facts, config)`, so
loading once and calling it per configuration turns a grid of ~1,700 points from half
an hour into a few minutes. The score is proved equal to the ticket 02 scorecard's in
`code/engine/tests/test_calibration_sweep.py`, so the speed costs no fidelity.

**The ranking rule is the ticket's, not a preference.** The five discrete columns are
more tractable and carry more of the score than the one continuous column, so a point
is ordered by discrete matches first and only then by `amount_safe_to_pay` matches.

Nothing on the submission path imports this module.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Mapping, Sequence

CODE_DIR = Path(__file__).resolve().parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from engine.loaders import load_dataset  # noqa: E402
from engine.pipeline import run_pipeline  # noqa: E402
from engine.types import Config, Dataset, Fact  # noqa: E402
from engine.validate import rendered_rows  # noqa: E402

REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
FIXTURES_DIR = REPO_ROOT / "fixtures"
SAMPLE_CSV = DATASET_DIR / "sample_requests.csv"

# The five discrete columns, in output order. `request_id` is not scored: it is an
# identity, and every configuration reproduces all 25 of them.
DISCRETE_COLUMNS = (
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)
CONTINUOUS_COLUMN = "amount_safe_to_pay"
SCORED_COLUMNS = DISCRETE_COLUMNS + (CONTINUOUS_COLUMN,)

# The seven axes ticket 14 is allowed to sweep, and nothing else. Naming an axis
# outside this table is out of scope for the block.
#
# Order matters. `sweep` sorts descending with a stable sort, so among tied points the
# first one `itertools.product` reaches is the one printed at the top - and the frozen
# optimum is a 13-point plateau. Each tuple therefore leads with the value that is now
# frozen, so re-running `--confirm` reproduces the winner the calibration recorded
# rather than an arbitrary member of its plateau.
SWEEP_AXES: Mapping[str, tuple] = {
    "lookback_days": (90, 120, 180, 270, 365),
    "min_occurrences": (2, 3, 4),
    "variable_spend_estimator": (
        "max_median3_mean6",
        "median3",
        "mean6",
        "last_month",
    ),
    "project_income_beyond_confirmed": (True, False),
    "variable_spend_shape": ("individual_events", "monthly_total"),
    "variable_spend_placement": ("earliest", "median", "latest"),
    "same_day_ordering": (
        "debits_credits_payment",
        "credits_debits_payment",
        "payment_debits_credits",
    ),
}

# The confirmation grid holds two axes at the value separate, stronger evidence already
# selected, and sweeps the other five exhaustively. `project_income_beyond_confirmed` is
# not a live choice - switching it off costs 25 discrete matches on its own - and
# `same_day_ordering` is settled cell by cell rather than by total (see
# `docs/investigation/calibration.md` section 4). Holding them makes the confirmation
# 360 points instead of 2,160, which is minutes instead of an hour.
CONFIRMATION_HELD = {
    "project_income_beyond_confirmed": True,
    "same_day_ordering": "debits_credits_payment",
}
CONFIRMATION_AXES: Mapping[str, tuple] = {
    name: values for name, values in SWEEP_AXES.items() if name not in CONFIRMATION_HELD
}


@dataclass(frozen=True, slots=True)
class Score:
    """Per-column matches for one configuration against the 25 solved samples."""

    matched: Mapping[str, int]

    @property
    def discrete_total(self) -> int:
        return sum(self.matched[column] for column in DISCRETE_COLUMNS)

    @property
    def continuous_total(self) -> int:
        return self.matched[CONTINUOUS_COLUMN]

    @property
    def rank_key(self) -> tuple[int, int]:
        """Discrete first, continuous only as a tie-break. See the module docstring."""
        return (self.discrete_total, self.continuous_total)


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything a configuration is scored against, loaded exactly once."""

    dataset: Dataset
    facts: tuple[Fact, ...]
    golden: tuple[Mapping[str, str], ...]
    golden_path: Path = field(default=SAMPLE_CSV)

    def rendered(self, config: Config) -> tuple[Mapping[str, str], ...]:
        """The submission cells this configuration would produce for the samples."""
        rows = run_pipeline(self.dataset, self.facts, config)
        return rendered_rows(rows, self.dataset)


def load_inputs(
    dataset_dir: Path = DATASET_DIR,
    requests_csv: Path = SAMPLE_CSV,
    fixtures_dir: Path = FIXTURES_DIR,
) -> Inputs:
    """Load the dataset, the extraction facts and the golden sample answers once."""
    dataset = load_dataset(dataset_dir, requests_csv)

    from extraction.fixture_adapter import FixtureExtractor

    extractor = FixtureExtractor(
        dataset=dataset, dataset_dir=dataset_dir, fixtures_dir=fixtures_dir
    )
    facts = tuple(
        fact
        for request in dataset.requests
        for fact in extractor.facts_for_user(request.user_id)
    )
    with requests_csv.open(encoding="utf-8-sig", newline="") as handle:
        golden = tuple(dict(row) for row in csv.DictReader(handle))
    return Inputs(dataset=dataset, facts=facts, golden=golden, golden_path=requests_csv)


# --- scoring ------------------------------------------------------------------------


def _normalize(column: str, value: str) -> str:
    """Normalize one cell exactly as the ticket 02 scorecard does."""
    value = (value or "").strip()
    if column == CONTINUOUS_COLUMN:
        text = value.replace(",", "")
        if not text:
            return ""
        try:
            return str(Decimal(text))
        except InvalidOperation:
            return ""
    if column in {"payment_plan", "spending_changes_needed"}:
        return value or "none"
    return value


def score_rows(
    golden: Sequence[Mapping[str, str]], actual: Sequence[Mapping[str, str]]
) -> Score:
    """Per-column matches between a golden answer set and a produced one."""
    actual_by_id = {row["request_id"]: row for row in actual}
    matched = dict.fromkeys(SCORED_COLUMNS, 0)
    for golden_row in golden:
        actual_row = actual_by_id.get(golden_row["request_id"])
        if actual_row is None:
            continue
        for column in SCORED_COLUMNS:
            if _normalize(column, golden_row.get(column, "")) == _normalize(
                column, actual_row.get(column, "")
            ):
                matched[column] += 1
    return Score(matched=matched)


def score_config(inputs: Inputs, config: Config) -> Score:
    """Run the whole pipeline under `config` and score it against the samples."""
    return score_rows(inputs.golden, inputs.rendered(config))


# --- sweeping -----------------------------------------------------------------------


def grid_configs(base: Config, axes: Mapping[str, Sequence]) -> Iterator[Config]:
    """Every configuration in the cartesian product of `axes`, based on `base`.

    Parameters not named in `axes` keep their `base` value, so a sweep can never move
    a knob it did not ask for.
    """
    names = list(axes)
    for combination in itertools.product(*(axes[name] for name in names)):
        yield replace(base, **dict(zip(names, combination)))


def sweep(
    inputs: Inputs,
    base: Config,
    axes: Mapping[str, Sequence],
    progress_every: int = 0,
) -> list[tuple[Config, Score]]:
    """Score every point of the grid, best first under the ticket's ranking rule.

    `progress_every` prints a heartbeat every N points. A point costs about a quarter
    of a second at the shipped defaults but four times that under `individual_events`,
    so a silent run leaves no way to tell slow from stuck.
    """
    results: list[tuple[Config, Score]] = []
    for index, config in enumerate(grid_configs(base, axes), start=1):
        results.append((config, score_config(inputs, config)))
        if progress_every and index % progress_every == 0:
            best = max(results, key=lambda pair: pair[1].rank_key)[1]
            print(
                f"  ... {index} points scored, best so far {best.rank_key}", flush=True
            )
    results.sort(key=lambda pair: pair[1].rank_key, reverse=True)
    return results


def coordinate_descent(
    inputs: Inputs,
    base: Config,
    axes: Mapping[str, Sequence],
    rounds: int = 4,
) -> tuple[Config, Score, list[str]]:
    """Improve one axis at a time until a full round changes nothing.

    Run alongside the full grid, not instead of it: agreement between the two says the
    grid's winner is a broad optimum rather than one lucky corner. The transcript it
    returns is the evidence trail for what moved and by how much.
    """
    current = base
    best = score_config(inputs, current)
    transcript = [f"start {best.rank_key}"]
    for round_index in range(rounds):
        improved = False
        for name, values in axes.items():
            for value in values:
                if getattr(current, name) == value:
                    continue
                candidate = replace(current, **{name: value})
                score = score_config(inputs, candidate)
                if score.rank_key > best.rank_key:
                    transcript.append(
                        f"round {round_index + 1}: {name}={value!r} "
                        f"{best.rank_key} -> {score.rank_key}"
                    )
                    current, best, improved = candidate, score, True
        if not improved:
            transcript.append(f"round {round_index + 1}: no change, settled")
            break
    return current, best, transcript


# --- reporting ----------------------------------------------------------------------

_SHORT = {
    "affordability_status": "status",
    "recommended_payment_method": "method",
    "payment_plan": "plan",
    "earliest_date_for_full_payment": "date",
    "spending_changes_needed": "chg",
    "amount_safe_to_pay": "safe",
}


def format_score(score: Score) -> str:
    columns = " ".join(
        f"{_SHORT[column]}={score.matched[column]:>2}" for column in SCORED_COLUMNS
    )
    return (
        f"discrete={score.discrete_total:>3}/125 "
        f"safe={score.continuous_total:>2}/25  {columns}"
    )


def changed_from(base: Config, config: Config) -> str:
    changes = [
        f"{name}={getattr(config, name)!r}"
        for name in SWEEP_AXES
        if getattr(config, name) != getattr(base, name)
    ]
    return ", ".join(changes) or "<shipped defaults>"


def _print_baseline(inputs: Inputs, base: Config) -> None:
    print("=== baseline (shipped Config defaults) ===")
    print(format_score(score_config(inputs, base)))


def _print_axis_sensitivity(inputs: Inputs, base: Config) -> None:
    """One axis at a time off the baseline: which knobs move the score at all."""
    print("\n=== single-axis sensitivity (all other axes at baseline) ===")
    for name, values in SWEEP_AXES.items():
        print(f"\n{name}")
        for value in values:
            score = score_config(inputs, replace(base, **{name: value}))
            marker = "  (baseline)" if getattr(base, name) == value else ""
            print(f"  {value!r:<24} {format_score(score)}{marker}")


def _print_coordinate(inputs: Inputs, base: Config) -> None:
    print("\n=== coordinate descent ===")
    best_config, best_score, transcript = coordinate_descent(inputs, base, SWEEP_AXES)
    for line in transcript:
        print(f"  {line}")
    print(f"  settled at: {changed_from(base, best_config)}")
    print(f"  {format_score(best_score)}")


def _print_grid(
    inputs: Inputs, base: Config, top: int, axes: Mapping[str, Sequence], label: str
) -> None:
    total = 1
    for values in axes.values():
        total *= len(values)
    print(f"\n=== {label} ({total} points) ===", flush=True)
    results = sweep(inputs, base, axes, progress_every=max(1, total // 20))
    for config, score in results[:top]:
        print(f"  {format_score(score)}  {changed_from(base, config)}")
    baseline_rank = next(
        index
        for index, (config, _) in enumerate(results, start=1)
        if all(getattr(config, name) == getattr(base, name) for name in axes)
    )
    print(f"\n  shipped defaults rank {baseline_rank} of {total}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ticket 14 calibration sweep over the 25 solved samples."
    )
    parser.add_argument("--baseline", action="store_true", help="Score the defaults.")
    parser.add_argument(
        "--axes", action="store_true", help="Single-axis sensitivity off the baseline."
    )
    parser.add_argument(
        "--coordinate", action="store_true", help="Coordinate descent over all axes."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Cartesian sweep of the five live axes, with the two settled by separate "
            "evidence held fixed. 360 points."
        ),
    )
    parser.add_argument(
        "--grid", action="store_true", help="Full cartesian sweep of all seven axes."
    )
    parser.add_argument(
        "--top", type=int, default=15, help="How many grid rows to print."
    )
    args = parser.parse_args(argv)

    inputs = load_inputs()
    base = Config()

    if args.baseline or not (args.axes or args.coordinate or args.confirm or args.grid):
        _print_baseline(inputs, base)
    if args.axes:
        _print_axis_sensitivity(inputs, base)
    if args.coordinate:
        _print_coordinate(inputs, base)
    if args.confirm:
        held = replace(base, **CONFIRMATION_HELD)
        _print_grid(
            inputs, held, args.top, CONFIRMATION_AXES, "confirmation grid (5 live axes)"
        )
    if args.grid:
        _print_grid(inputs, base, args.top, SWEEP_AXES, "full grid (all 7 axes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

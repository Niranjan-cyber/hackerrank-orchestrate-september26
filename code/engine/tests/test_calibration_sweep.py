"""The ticket 14 sweep harness.

Placed under `code/engine/tests/` because that is the discovery root the engine suite
runs (`python -m unittest discover -s code/engine/tests -t code`); the module under
test lives in `code/calibration/`.

Two properties matter, and only two. The harness must score the *same thing* the
ticket 02 subprocess scorecard scores - otherwise the block would tune against a
private opinion of accuracy - and a sweep must visit exactly the grid it was given.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[2]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from calibration.sweep import (  # noqa: E402
    DISCRETE_COLUMNS,
    grid_configs,
    load_inputs,
    score_config,
)
from engine.types import Config  # noqa: E402


class ScoreAgreesWithTheScorecardTest(unittest.TestCase):
    """The in-process score must equal the ticket 02 harness's own scorecard."""

    def test_default_config_score_matches_the_ticket_02_scorecard(self):
        sys.path.insert(0, str(CODE_DIR / "evaluation"))
        from evaluation.main import _build_scorecard, _load_csv  # noqa: PLC0415

        inputs = load_inputs()
        score = score_config(inputs, Config())

        # Independently: render through the same writer the submission uses, then run
        # the ticket 02 comparison over the golden sample file.
        actual_rows = [dict(row) for row in inputs.rendered(Config())]
        golden_rows = _load_csv(inputs.golden_path)
        card = _build_scorecard(golden_rows, actual_rows)

        for column in DISCRETE_COLUMNS + ("amount_safe_to_pay",):
            self.assertEqual(
                score.matched[column],
                card["column_hits"].get(column, 0),
                f"{column} disagrees with the ticket 02 scorecard",
            )

    def test_discrete_total_is_the_sum_of_the_five_discrete_columns(self):
        inputs = load_inputs()
        score = score_config(inputs, Config())
        self.assertEqual(
            score.discrete_total,
            sum(score.matched[column] for column in DISCRETE_COLUMNS),
        )
        self.assertEqual(len(DISCRETE_COLUMNS), 5)


class GridTest(unittest.TestCase):
    def test_grid_configs_is_the_cartesian_product_of_the_named_axes(self):
        axes = {
            "min_occurrences": (2, 3),
            "variable_spend_placement": ("earliest", "median", "latest"),
        }
        configs = list(grid_configs(Config(), axes))
        self.assertEqual(len(configs), 6)
        self.assertEqual(
            sorted((c.min_occurrences, c.variable_spend_placement) for c in configs),
            sorted(
                (occurrences, placement)
                for occurrences in (2, 3)
                for placement in ("earliest", "median", "latest")
            ),
        )

    def test_grid_configs_leaves_every_unnamed_parameter_at_the_base_value(self):
        base = Config(lookback_days=365)
        configs = list(grid_configs(base, {"min_occurrences": (2, 4)}))
        for config in configs:
            self.assertEqual(config.lookback_days, 365)
            self.assertEqual(
                config.variable_spend_estimator, base.variable_spend_estimator
            )


if __name__ == "__main__":
    unittest.main()

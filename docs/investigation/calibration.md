# Calibration block (2026-09-13) — measured, then frozen

Ticket 14. One timeboxed block, seven named parameters, then a freeze. This file is the record:
what was measured, what each measurement selected, and what the parameters are now fixed at.

**The parameters below are FROZEN. Do not re-tune them.** `code/engine/tests/test_frozen_calibration.py`
fails if one moves, so a later change is a deliberate edit to that file rather than a silent drift.

---

## 1. The frozen values

| Parameter | Was | **Frozen at** | Selected by |
|---|---|---|---|
| `lookback_days` | 180 | **90** | grid + descent; moves with `min_occurrences` |
| `min_occurrences` | 3 | **2** | grid + descent; +6 discrete alone, +11 paired |
| `variable_spend_estimator` | `max_median3_mean6` | **`max_median3_mean6`** | tie on score; kept as the most conservative |
| `project_income_beyond_confirmed` | `True` | **`True`** | switching off costs 25 discrete matches |
| `variable_spend_shape` | `monthly_total` | **`individual_events`** | +8 discrete alone, the block's largest single win |
| `variable_spend_placement` | `earliest` | **`earliest`** | inert under the frozen shape; all three tie |
| `same_day_ordering` | `debits_credits_payment` | **`debits_credits_payment`** | cell-level evidence, section 4 |

Three of the seven moved. The other four are frozen at the value they already had, each for a
reason recorded below rather than by default.

## 2. Per-column accuracy, before and after

Both rows are the ticket 02 scorecard (`python code/evaluation/main.py`) over the 25 solved samples.

| Column | Before | After | |
|---|---|---|---|
| `affordability_status` | 15/25 | **19/25** | +4 |
| `recommended_payment_method` | 16/25 | **19/25** | +3 |
| `payment_plan` | 15/25 | **17/25** | +2 |
| `earliest_date_for_full_payment` | 11/25 | **14/25** | +3 |
| `spending_changes_needed` | 22/25 | **21/25** | −1 |
| **five discrete columns** | **79/125** | **90/125** | **+11** |
| `amount_safe_to_pay` | 1/25 | 1/25 | 0 |
| `decision_explanation` (structural) | 10/25 | 11/25 | +1 |

Per request: **five improved, none regressed** — `request_04` 1→3, `request_06` 0→1, `request_12`
1→3, `request_13` 1→5, `request_21` 2→4. The single `spending_changes_needed` loss is inside
`request_21`, which still nets +2 across its five discrete cells.

`amount_safe_to_pay` did not move. That is the ticket's stated priority working as intended: the
five discrete columns carry more of the score and are more tractable, so a point is ranked by
discrete matches first and by `amount_safe_to_pay` only as a tie-break. The trade-off was live and
is recorded in section 6.

## 3. The harness

`code/calibration/sweep.py`. `run_pipeline` is a pure function of `(dataset, facts, config)`, so the
dataset and the extraction facts load once and each configuration is one in-process call — about a
quarter-second, against roughly a second for the ticket 02 subprocess-and-CSV path.

The speed costs no fidelity: `code/engine/tests/test_calibration_sweep.py` proves the harness's
per-column score equals the ticket 02 scorecard's for the same rows, and the rows it scores come
from `validate.rendered_rows`, the same code path that writes the submission — conservative
substitution for a failed row included.

Nothing on the submission path imports the harness. `code/main.py` does not know it exists.

## 4. Same-day ordering — the evidence that selected it

The three conventions barely separate on the total (90, 90 and 73 discrete at the frozen point), so
the choice was made cell by cell.

**`payment_debits_credits` is refused by the data.** Against the shipped convention it moves 17
cells, and every one is discrete: a plan payment landing a day later on `request_02`, `03`, `04`,
`06`, `07`, `13`, `17`, `18`, `21`, `22` and `23`, dragging `payment_plan` with it in six of them.
The published dates match the convention where the payment settles *after* the day's dataset
movements. That is direct evidence, not a preference: 73 against 90.

**`credits_debits_payment` is not refused, but it is not selected either.** It moves 16 cells, only
one of them discrete, so the discrete columns are nearly silent about it. On the 15
`amount_safe_to_pay` cells the published figure is nearer the debits-first reading on **12**:

| Request | Published | debits-first | credits-first | Closer |
|---|---|---|---|---|
| `request_02` | 17,229,139.20 | 20,237,973.24 | 22,056,808.26 | debits |
| `request_03` | 873,000.00 | 1,201,861.90 | 1,258,962.97 | debits |
| `request_04` | 8,401,800.00 | 10,747,931.93 | 11,026,984.97 | debits |
| `request_06` | 603.30 | 517.35 | 563.06 | credits |
| `request_07` | 87,170.56 | 100,639.06 | 117,294.06 | debits |
| `request_13` | 433.40 | 740.78 | 756.79 | debits |
| `request_14` | 597.74 | 734.98 | 761.97 | debits |
| `request_15` | 83.05 | 68.27 | 122.17 | debits |
| `request_17` | 243,849.58 | 244,224.65 | 251,454.26 | debits |
| `request_20` | 5,400.00 | 9,691.64 | 11,788.79 | debits |
| `request_21` | 1,543.35 | 1,539.83 | 1,570.97 | debits |
| `request_22` | 475.46 | 492.39 | 497.36 | debits |
| `request_23` | 9,152.00 | 5,827.87 | 10,098.07 | credits |
| `request_24` | 13,420.00 | 10,642.36 | 11,405.17 | credits |
| `request_25` | 1,425,000.00 | 3,703,598.42 | 3,876,105.73 | debits |

Twelve of fifteen, and credits-first is the less conservative reading in every case — it assumes the
day's money arrives before it leaves. **Frozen at `debits_credits_payment`.**

## 5. Single-axis sensitivity

Each axis moved alone, everything else at the pre-block baseline of 79/125 discrete.

| Axis | Values, discrete score |
|---|---|
| `lookback_days` | 180: 79 · **90: 80** · 120: 79 · 270: 79 · 365: 79 |
| `min_occurrences` | 3: 79 · **2: 85** · 4: 84 (safe 3/25) |
| `variable_spend_estimator` | `max_median3_mean6`: 79 · `median3`: 79 · **`mean6`: 81** · `last_month`: 77 |
| `project_income_beyond_confirmed` | True: 79 · **False: 54** |
| `variable_spend_shape` | `monthly_total`: 79 · **`individual_events`: 87** |
| `variable_spend_placement` | `earliest`: 79 · `median`: 80 · `latest`: 79 |
| `same_day_ordering` | `debits_credits_payment`: 79 · `credits_debits_payment`: 79 · `payment_debits_credits`: 73 |

Two readings worth keeping. **Income projection is not a live choice** — switching it off costs 25
discrete matches, more than every other axis combined can recover, so the confirmation grid holds it
fixed rather than spending half its points on a settled question. And **`individual_events` is the
block's largest single win**, which is the measurement `CONTEXT.md` section 9 predicted: placing a
whole month's variable spend on one day forecast entire weeks of zero variable spend, mis-timing both
the squeeze and the saving meant to answer it.

## 6. Descent and grid

**Coordinate descent** over all seven axes, ranked discrete-first, settled in three rounds:

```text
start                                              (79, 1)
round 1  lookback_days=90                          (79, 1) -> (80, 1)
round 1  variable_spend_estimator='median3'        (80, 1) -> (81, 2)
round 1  variable_spend_shape='individual_events'  (81, 2) -> (83, 2)
round 2  min_occurrences=2                         (83, 2) -> (90, 1)
round 3  no change, settled
```

**Confirmation grid**, 360 points: the five live axes swept exhaustively, with
`project_income_beyond_confirmed` and `same_day_ordering` held at the values sections 4 and 5 already
settled. The pre-block defaults ranked **272 of 360**. Re-run from the frozen point after the
corrections in section 7, the frozen configuration ranks **4 of 360**, and descent from it finds no
single-axis improvement in any direction.

A full 2,160-point sweep of all seven axes was started and abandoned: half its points use
`individual_events`, which costs four times as much per point, and it was over-running the block. The
five-axis grid answers the same question, since the two held axes were settled by stronger evidence
than a total. `--grid` still runs it for anyone who wants the exhaustive version.

**Choosing within the plateau.** The 90/125 points differ only in `variable_spend_estimator`
(`max_median3_mean6` or `median3`) and in the placement axis that is inert under the frozen shape.
The frozen point is the one that moves fewest parameters from what already shipped: `lookback_days=90`,
`min_occurrences=2`, `variable_spend_shape='individual_events'`, and nothing else. The estimator
genuinely does not matter here — the two tie — so it stays at `max_median3_mean6`, the most
conservative of the four, being the maximum of two estimators and therefore never forecasting less
than either.

**Why the window and the threshold move together.** A 90-day window holds at most three monthly
occurrences, so requiring three inside it is nearly unsatisfiable; two is the matching threshold. The
pair reads as "a recurrence that is currently active", where 180/3 read as "a recurrence with a long
history". Neither is wrong; the samples prefer the first, by 11 discrete matches.

**The trade-off the ticket anticipated.** `min_occurrences=4` with `mean6` scores 89 discrete but
**3/25** on `amount_safe_to_pay`, against the frozen point's 90 and 1. The ticket's rule decides it:
the discrete columns take priority, so 90/1 wins over 89/3. Recorded here because it is a real cost,
not a free choice.

### A 91/125 point exists, and was declined

`lookback_days=120` with `variable_spend_estimator='mean6'` scores **91/125** — one cell better than
the frozen point. It was not taken, and the reason is a measurement rather than a preference:

- **Each half of it is harmful alone.** From the frozen point, `lookback_days=120` on its own scores
  84 (−6) and `mean6` on its own scores 87 (−3). Only together do they reach 91. A pair whose
  components each destroy matches is compensating errors, not modelling the data better.
- **The whole gain is one cell**, `request_21`'s `spending_changes_needed` — `stop:event_1815|reduce_to:…`
  against the frozen point's `stop:event_1816`. Nothing else in 125 cells moves.
- **The frozen point is a local optimum in every single direction**; the 91 point is reachable only
  by a two-axis jump across a valley. On 25 samples generalising to 250 unlabelled requests, that
  shape is overfitting.

One cell out of 125 is not worth a configuration that is worse along both of its own axes. The point
is recorded here so the decision is reversible by someone who disagrees with the reasoning.

## 7. Corrections the code review forced

The freeze was measured once, reviewed, and re-measured. Four defects the `individual_events` shape
introduced were found by review and fixed before the values were settled; all four share one root
cause — every per-day slot of a variable stream carries the same cited `latest_event_id`.

1. **A spending change was credited once per slot instead of once per month** (`spending.apply_changes`).
   On the real samples this over-credited `request_12` by 3.2× — 8,407.56 removed from the ledger for
   a change the engine values at 870.85 an occurrence — and the ledger certifies plans against that
   position, so the error ran in the *unsafe* direction. A saving is now a monthly quantity, spread
   across the month's slots in proportion to what each carries.
2. **Projected effect ids collided** (`recurrence._project_stream`). Month-end clamping and weekend
   rolling routinely land two placement days on one date; the real 250-request run produced 1,670
   duplicate ids, and evidence amendment and the trace ledger both address effects by id. The
   placement day is now part of the id.
3. **A stated absolute amount was applied per slot** (`evidence._raise_expense`). "Your groceries
   budget is now 600 a month" forecast N×600. Latent on this dataset — every current target is a
   fixed-stream category — but fixed with the same monthly-total semantics.
4. **The split could produce a negative slot.** The earliest day carried the rounding remainder, and
   a share rounding to nothing while later days round up drove it below zero — a projected debit
   below zero is a phantom credit. The remainder now goes on the month's last slot and is clamped.

Each is pinned by a test that fails against the old behaviour. The corrections did not change the
frozen point's score — 90/125 before and after — but they did change the landscape around it, which
is why section 6's grid and descent were both re-run afterwards rather than trusted from the first
pass.

## 8. What the freeze changed elsewhere

**`protected_two_occurrence_project` is now inert.** It projected a two-occurrence stream when the
category was protected. At the frozen threshold of 2 every two-occurrence stream projects anyway, so
the carve-out never fires. It was not removed: it is not one of the seven parameters this block was
allowed to touch. The three tests that pin it now name `min_occurrences=3` explicitly, so they test
the rule at the threshold where it is observable and say why.

**`Stream.variable_monthly_total` was removed.** Under the split shape it held a per-day share, not a
monthly total, and no code outside the field's own assignment ever read it.

## 9. The freeze

Frozen **2026-09-13**, at the end of the block, at the values in section 1. Recorded in three places
that have to agree: the `Config` defaults in `code/engine/types.py`, this document, and
`code/engine/tests/test_frozen_calibration.py`, which fails if any of the seven moves.

Verification at the freeze: 342 engine tests and 196 top-level tests green, ruff clean on every file
the block touched, the 250-request run clean through the validator in ten seconds.

**No sample answer is hard-coded anywhere.** No engine module branches on a `request_id` or a
`user_id`; the only two occurrences of either are generic lookups by key
(`pipeline.py:107`, `validate.py:49`). No per-request special-casing was introduced, and only the
seven named parameters were swept.

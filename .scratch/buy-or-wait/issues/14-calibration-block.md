# 14: Calibration block - tune, then freeze

**Status: COMPLETE (2026-09-13). Parameters FROZEN.** Full record in
`docs/investigation/calibration.md`. Harness: `code/calibration/sweep.py`
(`--baseline --axes --coordinate --confirm --grid`).

Five discrete columns **79/125 -> 90/125**; five sample requests improved (04, 06, 12, 13, 21),
none regressed. `amount_safe_to_pay` unchanged at 1/25 - the ticket's own priority rule, applied to
a live trade-off (a 89/3 point existed and lost to the 90/1 one).

| Parameter | Was | Frozen at |
|---|---|---|
| `lookback_days` | 180 | **90** |
| `min_occurrences` | 3 | **2** |
| `variable_spend_estimator` | `max_median3_mean6` | `max_median3_mean6` |
| `project_income_beyond_confirmed` | `True` | `True` |
| `variable_spend_shape` | `monthly_total` | **`individual_events`** |
| `variable_spend_placement` | `earliest` | `earliest` |
| `same_day_ordering` | `debits_credits_payment` | `debits_credits_payment` |

Two axes had no knob before this ticket (`variable_spend_shape`, `variable_spend_placement`); both
were added to `Config` so the sweep could measure them rather than argue them. The freeze is
enforced by `code/engine/tests/test_frozen_calibration.py`.

**The shape change exposed five defects, four of them caught by the code review**, all from the same
root cause: every per-day slot of a variable stream carries the same cited `latest_event_id`. The
serious one was `spending.apply_changes` crediting a saving once per slot instead of once per month
(3.2x over-credit on `request_12`, in the **unsafe** direction, since the ledger certifies plans
against that position). All five are fixed and pinned by tests, and the grid and descent were
re-run afterwards rather than trusted from the first pass. A 91/125 point exists
(`lookback_days=120` + `mean6`) and was declined: each half costs matches alone, the whole gain is
one cell, and it is reachable only by a two-axis jump across a valley. Calibration doc section 6-7.


**What to build:** The unfrozen forecasting parameters are settled against the 25 solved samples and
then frozen, so accuracy is measured rather than assumed.

**Timeboxed to one three-hour block.** At the end the parameters are frozen and not touched again,
whatever the score. The five discrete columns are more tractable and carry more of the score than the
one continuous column, so they take priority when a trade-off appears.

Calibration is only meaningful once the sample-user fixtures exist: 17 of 25 sample users have a
message and 5 have an image, so tickets 11 and 12 genuinely gate this.

**Sweep exactly these, and nothing else:** lookback window; occurrence threshold; variable-spend
estimator; whether recurring income projects beyond the explicit confirmed salary row; variable spend
as individual events versus one monthly total; day-of-month placement; and same-day ordering
convention.

**Blocked by:** 02, 10, 11, 12.

**Owner:** Claude - calibration

**Status:** complete

- [x] Per-column accuracy is recorded before and after, from the ticket 02 scorecard - section 2
- [x] Only the named parameters are swept; no per-request special-casing is ever introduced - section 8
- [x] The chosen same-day ordering convention is recorded with the evidence that selected it - section 4
- [x] Final parameter values are written to the config defaults and to `docs/investigation/calibration.md`
- [x] Parameters are frozen at the end of the block and the freeze is recorded - section 8, plus a test
- [x] No sample answer is hard-coded anywhere - verified, section 8

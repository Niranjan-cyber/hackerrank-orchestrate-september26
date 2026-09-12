# 14: Calibration block - tune, then freeze

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

**Status:** ready-for-agent

- [ ] Per-column accuracy is recorded before and after, from the ticket 02 scorecard
- [ ] Only the named parameters are swept; no per-request special-casing is ever introduced
- [ ] The chosen same-day ordering convention is recorded with the evidence that selected it
- [ ] Final parameter values are written to the config defaults and to `docs/investigation/calibration.md`
- [ ] Parameters are frozen at the end of the block and the freeze is recorded
- [ ] No sample answer is hard-coded anywhere

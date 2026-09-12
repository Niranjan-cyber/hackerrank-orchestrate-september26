# 05: Recurrence detection and forward projection

**What to build:** Recurring income and expenses are identified from history and projected into the
forecast window, so the safe-to-pay figure reflects real commitments rather than today's balance
alone. Fixed streams project at their last observed amount on their own day of month; variable
essential categories project as a conservative monthly total.

**Every parameter here is explicitly unfrozen** and must be reachable through the injected `config`
object, never hard-coded: lookback window, occurrence threshold, amount and date tolerances, the
variable-spend estimator, whether recurring income is projected beyond the explicit confirmed salary
row, and day-of-month placement. Ticket 14 settles the values.

**Blocked by:** 04.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Stream detection groups by category, direction, and normalised description similarity
- [ ] Occurrence threshold, lookback window, and both tolerances are `config` fields with documented defaults
- [ ] Semi-monthly patterns are modelled as two monthly streams keyed on day of month, never one 15-day stream
- [ ] Day of month is clamped to month end, unit-tested across February and a leap year
- [ ] The variable-spend estimator is selectable through `config`
- [ ] An explicit future scheduled or pending row suppresses the projected occurrence it duplicates
- [ ] No parameter value appears as an inline literal in engine logic

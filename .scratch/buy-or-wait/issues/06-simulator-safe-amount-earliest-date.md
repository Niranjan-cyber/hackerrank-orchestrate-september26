# 06: 90-day simulator, amount_safe_to_pay, and earliest_date_for_full_payment

**What to build:** For any request, the engine can project the balance day by day across the fixed
90-day window, report the largest amount safe to pay today, and report the earliest date a single
full payment becomes safe - both computed **before and without** any spending changes.

The window is fixed and anchored at `request_date`; the maximum deadline distance in the dataset is
86 days, so the window always covers the deadline. The floor is tested after **every** applied event,
not at end of day.

**Same-day ordering is unfrozen.** Implement it as one named sort key so all three candidate
conventions can be swapped and scored in ticket 14. The current inference is dataset debits, then
dataset credits, then the plan payment last.

**Blocked by:** 05.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] `amount_safe_to_pay` equals the window-minimum projected balance minus the floor, clamped to 0 and requested_amount
- [ ] `earliest_date_for_full_payment` is the first date in the window where a single full payment holds the floor, empty when never
- [ ] Both values are computed without applying any spending change
- [ ] `earliest_date_for_full_payment` is independent of the user's accepted payment methods
- [ ] Same-day ordering lives in exactly one named, swappable sort key
- [ ] Window boundary behaviour is unit-tested at request_date and at request_date plus 90
- [ ] An accepted plan's future payments are injected as scheduled debits and re-checked for the plan's full duration

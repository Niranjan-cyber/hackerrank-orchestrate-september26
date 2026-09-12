# 07: Candidate plan generation, lexicographic ranking, and status mapping

**What to build:** For each request the engine enumerates every eligible way to proceed - pay in
full, pay partially, each supplied installment option, or wait - discards those that are ineligible
or unsafe, and picks the winner by the challenge's stated priority order.

Ranking is a single `rank_key(plan)` returning a tuple, compared lexicographically: completes by
deadline, then no spending changes, then lowest fee-inclusive total, then earlier start, then fewer
payments, then lowest payment_option_id, with request_id appended last for a guaranteed total order.
Never a weighted score.

`wait` is expected to beat installments whenever both are eligible, because it pays exactly the
requested amount while all 515 installment options carry a non-zero financing fee. That must be an
**emergent** result of the ranking, never a hard-coded rule.

**Blocked by:** 06.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Ineligible options are pruned **before** ranking, never ranked then rejected
- [ ] Installment plans match a supplied option exactly and respect max_installment_months
- [ ] Installment schedules are generated as first_payment_date plus k times payment_frequency_days
- [ ] Partial payment is offered only when the request allows it, the user accepts it, the safe amount is strictly between zero and the requested amount, and earliest is on or before the deadline
- [ ] Partial plans contain exactly two payments summing to requested_amount
- [ ] `wait` plans contain exactly one payment, on the earliest date, for the full amount
- [ ] `affordable_now` requires both a safe amount equal to the requested amount and full_payment among accepted methods
- [ ] Sample 12 reproduces: capacity today but full_payment not accepted yields affordable_with_plan with installments
- [ ] Sample 19 reproduces: partial beats an eligible installment option on fee-inclusive total
- [ ] No hard-coded preference between wait and installments exists anywhere

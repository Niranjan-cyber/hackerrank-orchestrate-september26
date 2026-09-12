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

**Status:** done

- [x] Ineligible options are pruned **before** ranking, never ranked then rejected
- [x] Installment plans match a supplied option exactly and respect max_installment_months
- [x] Installment schedules are generated as first_payment_date plus k times payment_frequency_days
- [x] Partial payment is offered only when the request allows it, the user accepts it, the safe amount is strictly between zero and the requested amount, and earliest is on or before the deadline
- [x] Partial plans contain exactly two payments summing to requested_amount
- [x] `wait` plans contain exactly one payment, on the earliest date, for the full amount
- [x] `affordable_now` requires both a safe amount equal to the requested amount and full_payment among accepted methods
- [x] Sample 12 reproduces: capacity today but full_payment not accepted yields affordable_with_plan with installments
- [x] Sample 19 reproduces: partial beats an eligible installment option on fee-inclusive total
- [x] No hard-coded preference between wait and installments exists anywhere

---

## Implementation notes (Claude Code)

- `code/engine/plans.py` is the new seam: `Plan`, `Candidates`, `installment_schedule`,
  `option_sort_key`, `rank_key`, `best_plan`, `candidate_plans`. `pipeline._decide` now only
  gathers the position and the two capacity figures, calls the seam, and renders the winner.
- Every candidate is re-simulated with its payments injected and kept only if `Ledger.holds_floor`.
  `amount_safe_to_pay` / `earliest_date_for_full_payment` generate candidates; they never certify one.
- `option_sort_key` orders ids numerically. The supplied ids are zero-padded to two digits but run to
  `payment_option_790`, so a plain string sort would rank `payment_option_100` below `payment_option_33`.
- **Samples 12 and 19 are pinned at the plan seam, not end to end** (`test_sample_plans.py`).
  `amount_safe_to_pay` currently matches 0 of 25 samples - ticket 14's calibration debt - so an
  end-to-end assertion today would test ticket 14 and fail. Each test builds a position designed to
  reproduce the sample's published capacity figures, asserts it does with the real simulator, then
  asserts ticket 07 turns them into the sample's published recommendation. Real profile, real request,
  real supplied options, real ledger, real `rank_key`.
- **New decision, CONTEXT.md D12**: `desired_completion_date` is a hard gate for `partial` and
  `installments` and a ranking level for `wait`. A uniform hard gate makes ranking criterion 1 dead
  code. Changed one ticket-06 assertion in `test_pipeline_decide.py`. Both gates are `Config` flags.
- Emergent `wait` > `installments` is pinned in both directions in `test_rank.py`. It is not yet
  *exercised* on the 250 requests: only 4 of them currently produce more than one candidate, and none
  produces `wait` and `installments` together, because the conservative capacity figures prune most
  installment options at the floor test. Expect that to change when ticket 14 calibrates.
- 250-request run: 48 `full_payment`, 48 `installments`, 43 `wait`, 6 `partial_payment`,
  105 `not_recommended`. Validator clean; byte-identical across runs and under `PYTHONHASHSEED=1`.

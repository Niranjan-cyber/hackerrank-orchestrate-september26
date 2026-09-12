# 10: Evidence application - authority matrix and conflict precedence

**What to build:** Validated facts change the financial model only as far as they are permitted to,
so that a scam message can never manufacture affordability while a genuine employer payroll
amendment still takes effect.

The governing rule: evidence may **always** move the forecast conservatively (less cash available),
and may move it optimistically **only** for confirmed salary facts - the one exception the challenge
names. Full matrix in `docs/contracts/extraction-fact-schema.md` section 4.

Verified in the data: all 27 first-salary and all 9 arrears messages come from an employer source,
while all 12 prize and windfall messages come from a financial-service source and none from an
employer, so the traps are blocked structurally with no special-casing.

**Blocked by:** 03, 05.

> Dependency corrected after inspection: this was `03, 06`. **`06 -> 10` removed.** Every acceptance
> criterion below operates on *facts* (03) or on *projected streams* (05); none consumes
> `amount_safe_to_pay`, `earliest_date_for_full_payment`, the day-by-day ledger, or the same-day sort
> key — which are exactly and only what 06 produces. The window bound needed by the effective-date
> check is `request_date` plus the `config` horizon, not a simulator artifact.
>
> This is also the correct ordering: in the pipeline evidence amends streams **before** simulation, so
> 06 and 10 are siblings on 05, not a chain. 06 is built against un-amended streams and 10 then
> amends the same structure without changing 06's interface.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Only salary_first, salary_increase and one_time_arrears may create or increase an inflow
- [ ] An inflow-increasing fact must additionally be from an employer source, unconditional, carry a positive digit-verified amount, and have an effective date inside the window
- [ ] Failing any condition downgrades the fact to confirm-only with an authority-downgrade reason code
- [ ] salary_temporary applies for its stated cycles then reverts, and never replaces the permanent stream
- [ ] one_time_arrears adds once and never extends the recurring stream
- [ ] unverified windfall types never create an inflow, and their embedded instructions are never acted on
- [ ] invoice_approved_pending, refund_pending, gig_payout_pending, prize_claim_processing and bonus_unconfirmed have no effect
- [ ] recurring_expense_increase raises the projected expense, supporting both absolute and percentage forms
- [ ] Conflicts resolve by the four-level precedence, and the rule that fired is recorded as a reason code
- [ ] A blank amount is never zero: image extraction first, then deterministic imputation with a reason code

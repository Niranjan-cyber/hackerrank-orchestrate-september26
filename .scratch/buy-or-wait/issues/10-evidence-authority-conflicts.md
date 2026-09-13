# 10: Evidence application - authority matrix and conflict precedence

**Status: COMPLETE (2026-09-13).** Implemented in `code/engine/evidence.py` (new, the only module
that decides how far a fact may move the forecast), wired into `code/engine/pipeline.py` at two
points, with `monthly_occurrences()` extracted in `code/engine/recurrence.py` so an evidence-created
stream and a detected one place their occurrences by one rule. Two new `Config` knobs for ticket 14:
`income_categories` and `blank_amount_estimator`. Tests: `code/engine/tests/test_evidence.py` (85)
and `test_monthly_occurrences.py` (7); full suite 319, all passing.

**Review follow-up (2026-09-13).** A two-axis review found two latent bugs, both fixed with
regression tests and both behaviour-preserving on the corpus (`output.csv` is byte-identical before
and after): extractor `confidence` was folded into precedence rule 3, so it could emit
`CONFLICT_SETTLED_OVER_ESTIMATE` for two facts that are neither settled nor estimates — it is now a
tiebreak below all four rules; and `internal_transfer` treated a same-direction `linked_event_id`
(the ticket-04 failed→retry lifecycle link) as a matching leg, which would have dropped a real
charge — a partner now has to move the other way. Two spec tensions were found and deliberately left
alone rather than silently redesigned; see "Spec tensions left unresolved" below.

**Measured effect.** Sample scorecard 79 -> 74 mismatched fields, no column regressing and
`amount_safe_to_pay` matching for the first time. `request_16` now matches the published sample on
all five of its previously-failing fields. On the 250 evaluation rows exactly one row changed
(`request_64`, a degenerate `0 / not_affordable` becoming a real `affordable_with_plan` forecast);
every other row is byte-identical, and two consecutive runs still are.

**Decisions worth knowing** (full reasoning in `CONTEXT.md` section 6, "Settled by ticket 10"):

1. The authority matrix is **one directional measurement**, not 25 per-type permissions: each
   proposed amendment is costed against the balance series it would replace, and an optimistic one
   is accepted only from the three authorised inflow types passing all five conditions. The scam
   traps are inert with no rule naming them, and so are three cases no per-type rule anticipated.
2. **A fact may only amend what it names.** No `related_event_id` means confirm-only
   (`EVIDENCE_TARGET_UNRESOLVED`), because `Fact` has no category field and reading the target out of
   the message text would let untrusted prose pick the commitment to change.
3. `percent_change` is in **percentage points** (`12` = 12%).
4. A **stated** date is never weekend-rolled; an inferred one is.
5. A blank amount resolves image -> median-of-same-category -> unpriced. **Never zero.**

**Two items for OpenCode, both in `code/extraction/**`:**

- **`related_event_id` on the rent and transfer facts.** All 7 "renewed lease increases monthly rent
  by 12%" messages and all 6 internal-transfer messages carry no `related_event_id`, so the engine
  has nothing to point the amendment at and they are inert. Setting it to the stream occurrence the
  message amends (the user's latest rent event; the named transfer leg) makes both capabilities live.
  The engine side is implemented and tested either way.
- **`validate.py`'s V9 drops the fact it downgrades.** The V9 branch rewrites `fact_type` to
  `salary_confirmed_unchanged`, then falls through to `if violations: return None, ...`, so the
  downgraded fact never reaches the engine. Contract section 9 assigns the authority matrix to the
  core, and the core now enforces it independently, so nothing is unsafe - but the fixture loader
  reports a violation where the contract says a confirm-only fact should pass.

**Spec tensions left unresolved (deliberately, by the 2026-09-13 review).** Two places where the
written contract and the ticket's governing rule disagree. Neither is changed here, because changing
either is a redesign decision for the human rather than a review fix:

1. **`salary_temporary` with no detected prior stream.** `docs/contracts/extraction-fact-schema.md`
   section 5 says *"If the prior stream amount is unknown, fall back to `amount` for the stated cycle
   and project nothing afterwards."* Section 4 says only `salary_first`, `salary_increase` and
   `one_time_arrears` may **create** an inflow. The implementation follows section 4 (the ticket's
   "governing rule", lines 46-48): such a fact is recorded and projects nothing
   (`EVIDENCE_AUTHORITY_DOWNGRADE`), and `test_with_no_prior_stream_it_creates_no_income` enshrines
   it. Section 5's fallback is therefore **not** implemented.
2. **`disputed_duplicate_charge` and precedence rule 1.** Contract section 2 calls the dispute
   message *"an explicit statement that no reversal exists — precedence rule 1 territory"*, but
   `disputed_duplicate_charge` is absent from `EXPLICIT_RECORD_TYPES`, so it resolves at level 3
   (`CONFLICT_SETTLED_OVER_ESTIMATE`) when grouped with `payment_retry_pending`
   (`test_level_three_a_statement_of_fact_beats_a_forecast`). Both facts produce the same reserve
   effect, so only the recorded reason code differs. The confidence fix above does not affect this.

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

**Status:** COMPLETE

- [x] Only salary_first, salary_increase and one_time_arrears may create or increase an inflow
- [x] An inflow-increasing fact must additionally be from an employer source, unconditional, carry a positive digit-verified amount, and have an effective date inside the window
- [x] Failing any condition downgrades the fact to confirm-only with an authority-downgrade reason code
- [x] salary_temporary applies for its stated cycles then reverts, and never replaces the permanent stream
- [x] one_time_arrears adds once and never extends the recurring stream
- [x] unverified windfall types never create an inflow, and their embedded instructions are never acted on
- [x] invoice_approved_pending, refund_pending, gig_payout_pending, prize_claim_processing and bonus_unconfirmed have no effect
- [x] recurring_expense_increase raises the projected expense, supporting both absolute and percentage forms
- [x] Conflicts resolve by the four-level precedence, and the rule that fired is recorded as a reason code
- [x] A blank amount is never zero: image extraction first, then deterministic imputation with a reason code

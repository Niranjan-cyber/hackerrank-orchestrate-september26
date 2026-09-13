# 09: Reason codes, deterministic explanation, and the trace ledger

**What to build:** Every number the engine derives carries reason codes naming the events behind it.
Those codes render the `decision_explanation` column, and they dump to a trace file so a developer
can see exactly why a request came out the way it did.

No language model is involved. The explanation is templated from engine state, which is what keeps it
consistent across 250 rows and stops any extracted text reaching a graded column.

**Blocked by:** 07.

**Owner:** Claude - `code/engine/**`

**Status:** done (with one noted gap)

- [x] Derived values carry a reasons tuple of (code, event_id, amount) entries
- [ ] Codes exist at least for reserved pending debits, excluded pending credits, excluded unrealized valuations, netted internal transfers, floor breach dates, pruned options, imputed blank amounts, and authority downgrades

  Five of eight already had real codes from tickets 04-08 (`PENDING_DEBIT_RESERVED`/`SCHEDULED_DEBIT_RESERVED`, `UNSETTLED_CREDIT_NOT_COUNTED`, the `OPTION_*`/`INSTALLMENTS_*` prunes, `PLAN_BREACHES_MINIMUM_BALANCE` naming the breach date). This ticket added the sixth: `UNREALIZED_VALUATION_EXCLUDED` in `cash.py`, split out of `NON_CASH_IGNORED` so an excluded mark-to-market valuation is named distinctly from an ordinary non-cash row.

  **Left unchecked on purpose:** *netted internal transfers* and *authority downgrades* are `Fact`-driven (`docs/contracts/extraction-fact-schema.md` §4, ticket 10's own acceptance criteria) and *imputed blank amounts* needs a resolved `amount_overrides` value (ticket 10's last line, fed by ticket 12). `extraction_facts` reaches `pipeline._build_decision` as a parameter but nothing reads it yet - ticket 10 is what applies the authority matrix and imputation, not this ticket. Manufacturing those three codes now would mean either wiring fact-consumption early (ticket 10's job, cross-ownership) or shipping dead constants no code path can produce. Renderer and trace stayed generic on `Reason.code` for exactly this reason: ticket 10 only has to start appending `Reason(code="EVIDENCE_AUTHORITY_DOWNGRADE", ...)` etc. and both the explanation and the trace pick it up with no further change here.

- [x] `decision_explanation` is rendered from reason codes and names real amounts and dates - the floor is now read off each request's own `MINIMUM_BALANCE_FLOOR` reason rather than re-derived from the profile, and amounts are comma-grouped in prose (`money.format_explanation_amount`), matching the samples (`ZAR 25,256`) while the graded `payment_plan`/`reduce_to` columns keep their unformatted form.
- [x] No extracted evidence text is ever emitted verbatim into any output column - true by construction (facts are unread) and pinned by `test_trace.py`'s type-level check that `Reason` has no `verbatim_quote`-shaped field.
- [x] A per-request trace file can be written, and a human-readable day-by-day ledger printed for any single request - `engine/trace.py` + `pipeline.trace_request` + `code/main.py --trace-request <id> [--trace-out <path>]`.
- [x] The ledger shows date, event, amount, running balance, floor, and a breach marker - `trace.render_ledger` / `trace.ledger_rows`.
- [x] Trace output is off the `output.csv` path and cannot affect it - `validate.py` remains the sole writer of `OUTPUT_COLUMNS`; `trace.py` is not imported by it, and `--trace-request` skips `validate_and_write` entirely.

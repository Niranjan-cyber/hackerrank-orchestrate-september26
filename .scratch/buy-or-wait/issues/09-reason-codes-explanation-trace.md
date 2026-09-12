# 09: Reason codes, deterministic explanation, and the trace ledger

**What to build:** Every number the engine derives carries reason codes naming the events behind it.
Those codes render the `decision_explanation` column, and they dump to a trace file so a developer
can see exactly why a request came out the way it did.

No language model is involved. The explanation is templated from engine state, which is what keeps it
consistent across 250 rows and stops any extracted text reaching a graded column.

**Blocked by:** 07.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Derived values carry a reasons tuple of (code, event_id, amount) entries
- [ ] Codes exist at least for reserved pending debits, excluded pending credits, excluded unrealized valuations, netted internal transfers, floor breach dates, pruned options, imputed blank amounts, and authority downgrades
- [ ] `decision_explanation` is rendered from reason codes and names real amounts and dates
- [ ] No extracted evidence text is ever emitted verbatim into any output column
- [ ] A per-request trace file can be written, and a human-readable day-by-day ledger printed for any single request
- [ ] The ledger shows date, event, amount, running balance, floor, and a breach marker
- [ ] Trace output is off the `output.csv` path and cannot affect it

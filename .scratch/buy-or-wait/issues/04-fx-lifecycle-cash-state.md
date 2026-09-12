# 04: Currency conversion, event lifecycle, and cash-state classification

**What to build:** The user's cash position as of `request_date` is reconstructed correctly from the
25,342 event rows: foreign amounts converted at the right dated rate, the seven `linked_event_id`
lifecycle patterns resolved, and every row correctly counted, reserved, or excluded.

Rules are specified in `CONTEXT.md` sections 3 and 5 and are not restated here. The two cases that
are easiest to get wrong, both verified against the data: a pending debit described as a possible
duplicate **is reserved** (all six carry a dispute message confirming no reversal was posted), and a
matching debit/credit pair between the user's own accounts **nets to zero**.

**Blocked by:** 01.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Foreign amounts convert using the rate row for settlement_date, from_currency, home_currency
- [ ] Pending debits are reserved; pending credits are never counted
- [ ] Cancelled, failed, and unrealized/non-cash rows never affect cash
- [ ] A settled investment_sale counts; an investment_valuation never does
- [ ] A failed-to-scheduled retry counts the scheduled child, not the failed parent
- [ ] A cancelled-to-settled authorization counts only the settled child
- [ ] All six possible-duplicate pending debits are **reserved**
- [ ] Internal-transfer debit and credit legs net to zero
- [ ] Events dated on or before request_date are treated as already inside the opening balance

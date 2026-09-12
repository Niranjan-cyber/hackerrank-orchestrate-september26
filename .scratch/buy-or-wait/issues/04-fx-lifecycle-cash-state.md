# 04: Currency conversion, event lifecycle, and cash-state classification

**COMPLETE** (2026-09-12). `code/engine/cash.py` + 50 stdlib-`unittest` tests in
`code/engine/tests/`, all passing. Run them with:

```text
python -m unittest discover -s code/engine/tests -t code
```

Wired into `pipeline.py`, so every run classifies all 25,342 rows. Full-dataset output is
byte-identical to the ticket-01 baseline. **What that does and does not prove:** it proves the
classification runs over every row without raising (so no `MissingRateError` and no unhandled state
anywhere in the data) and that the integration changed no graded column. It does *not* demonstrate
the position improving an answer — `amount_safe_to_pay` is still `opening_balance - minimum_balance`,
because spending the reserves against it needs the horizon and same-day ordering that belong to
ticket 06.

Suite validated by mutation, not just by passing: excluding the duplicate rows fails 4 tests,
treating a blank future amount as zero fails 4, counting pending credits fails 2, and converting at
`event_date` instead of the settlement date fails 1.

**What to build:** The user's cash position as of `request_date` is reconstructed correctly from the
25,342 event rows: foreign amounts converted at the right dated rate, the seven `linked_event_id`
lifecycle patterns resolved, and every row correctly counted, reserved, or excluded.

Rules are specified in `CONTEXT.md` sections 3 and 5 and are not restated here. The two cases that
are easiest to get wrong, both verified against the data: a pending debit described as a possible
duplicate **is reserved** (all six carry a dispute message confirming no reversal was posted), and a
matching debit/credit pair between the user's own accounts **nets to zero**.

**Blocked by:** 01.

**Owner:** Claude - `code/engine/**`

**Status:** done

- [x] Foreign amounts convert using the rate row for settlement_date, from_currency, home_currency
- [x] Pending debits are reserved; pending credits are never counted
- [x] Cancelled, failed, and unrealized/non-cash rows never affect cash
- [x] A settled investment_sale counts; an investment_valuation never does
- [x] A failed-to-scheduled retry counts the scheduled child, not the failed parent
- [x] A cancelled-to-settled authorization counts only the settled child
- [x] All six possible-duplicate pending debits are **reserved**
- [x] Internal-transfer debit and credit legs net to zero
- [x] Events dated on or before request_date are treated as already inside the opening balance

## Verification notes

**The rule is smaller than the lifecycle table.** Measured across all 58 linked rows: the
`linked_event_id` link *never* decides cash treatment. `status` + `direction` decide it alone, and
all seven documented patterns fall out of that single rule. So `cash.py` does no parent lookup and
no graph walk. `test_lifecycle_real_data.py` asserts every one of the 58 pairs against the
seven-pattern table, so the equivalence breaks loudly if it ever stops holding.

**The status split and the date split coincide exactly.** Every `settled`/`cancelled`/`failed`/
`unrealized` row lands on or before its user's `request_date`; every `pending`/`scheduled` row lands
strictly after it. That is *why* classifying by status is sufficient rather than merely convenient.
The stale-row branches in `cash.py` (reserve a past-due pending debit immediately; never count a
scheduled credit that failed to arrive) are therefore unexercised by this dataset but pinned by
tests, and `test_closed_statuses_are_past_and_open_statuses_are_future` is the warning if a future
dataset breaks the invariant.

**Three corrections pushed back into `CONTEXT.md`:**

1. Sections 3 and 5 said to *ignore* duplicate-marked rows, contradicting decision D9 in section 13,
   which had already reversed that. Independently re-verified here: all 6 rows carry a bank message
   stating the dispute is open and no reversal has been posted. Reserved. Sections 3, 5 and the
   implementation guardrail now match D9.
2. D9's parenthetical claimed `internal_transfer` was "the true de-duplication case, where a matching
   debit+credit must net to zero". **There are no such event rows.** All 6 messages carry a blank
   `related_event_id`; a full scan finds no equal-magnitude debit/credit pair for 5 of the 6 users in
   any currency, on any date, in any status, and the 6th (`user_261`) has only the ordinary settled
   card-reversal pair. There is no `transfer` event type at all - credits are only
   `income`/`refund`/`investment_sale`. The criterion is satisfied with no code: nothing exists to
   net, and any equal-and-opposite settled pair is already inside the opening balance. Pinned by
   `test_no_internal_transfer_pair_exists_as_event_rows` so tickets 05 and 10 do not hunt for a
   stream to suppress.
3. FX coverage is 140 foreign rows, of which **139** are priceable. The 140th (`event_7307`, a
   settled USD taxi fare for an INR user) has a blank amount, but it is settled history already
   inside the opening balance, so the forecast never needs to price it. No inverse-rate or
   nearest-date fallback is implemented; a missing rate raises `MissingRateError` rather than
   defaulting.

## Changes made in response to `/code-review`

Both axes found real defects. All four are fixed in this commit:

1. **Standards, hard:** the suite was written with `pytest`, violating D24 and
   `docs/agents/tooling.md`, which fix the runner as stdlib `unittest`. D24's *second* reason —
   "stdlib keeps the zero-dependency promise for graders" — holds regardless of what is installed
   locally, so the decision stood and the suite was wrong. Converted to `unittest`; `conftest.py`
   became `support.py`; zero `pytest` imports remain.
2. **Standards + Spec, hard:** `CashEffect.signed_amount` returned `ZERO` for `UNKNOWN_AMOUNT`, so
   `balance_on()` silently overstated the balance by the unresolved charge — a blank-as-zero path
   against the guardrail "never treat a blank `amount` as zero … or fail loudly". Now raises
   `UnresolvedAmountError`, naming the event, from `signed_amount`, `net_forecast_change`, and
   `balance_on` (the last only when the unpriced row falls on or before the cutoff, since a later
   one cannot affect an earlier total). Ticket 06 must catch it and degrade conservatively for that
   user, never swallow it.
3. **Spec:** `test_an_internal_debit_credit_pair_nets_to_zero` was **vacuous** — both legs were
   settled and pre-request, so the sum was zero for *any* amounts (it passed with 1500 vs 999).
   Replaced with a pair of *future* legs that genuinely reach the forecast, asserting the balance is
   unmoved and both legs are present, plus `test_unequal_future_legs_do_not_net_to_zero` as a
   non-vacuity guard. The settled-pair shape is now asserted separately for what it actually is.
4. **Spec:** the criterion names `settlement_date`, but conversion passes `Event.cash_date`, which
   falls back to `event_date`. Behaviour is identical on this data, but the fallback was unstated
   drift. Pinned by `test_every_foreign_row_has_an_explicit_settlement_date`, which asserts the
   fallback can never fire for a foreign row.

Also addressed from the smell baseline: dropped the unused `CashEffect.moves_cash`, and removed the
`category=""` / `flexibility="fixed"` defaults — `"fixed"` is the least-changeable value, which
ticket 08 would have read as "this expense may never be reduced". `CLOSED_STATUSES`/`OPEN_STATUSES`
are now imported by the tests rather than re-spelled inline in three places.

**Two review notes accepted but deliberately not acted on:** `pipeline.py` emitting a `Reason` per
reserved debit is ticket 09's surface, but ticket 01 already established reason emission there and
the change is behaviour-neutral, so it stays. `CashEffect.category`/`flexibility` are unused until
ticket 08, but carrying them avoids making 08 re-join effects to events by id.

**Environment side effect to be aware of:** `pip install iniconfig` was run to repair the local
`pytest` before the D24 violation was caught, so `pytest` (8.4.2) now imports on this machine where
`docs/agents/tooling.md` says it does not. Nothing in the repo depends on it and the suite needs
only stdlib, but the doc's factual claim is now stale even though its guidance is still correct.

## Hand-off notes for downstream tickets

- **Ticket 05 (recurrence):** consume `CashPosition.effects`. Explicit open rows are already
  classified and dated - never re-derive them from `status`, and never project a stream occurrence
  onto a date an explicit row already covers. The 6 `internal_transfer` messages carry no stream to
  suppress (see correction 2).
- **Ticket 06 (simulator):** `cash.py` orders effects by `(cash_date, event_id)` only. The
  debits-before-credits same-day rule is deliberately *not* applied here - it is yours.
  `CashPosition.balance_on()` is explicit rows only, so it is an upper bound on the balance and not
  a figure a plan may be certified against.
- **Ticket 12 (vision):** only **4** of the 16 blank-amount rows are future cash events and so
  actually affect a forecast - `event_6033` (user_64, EVAL), `event_6859` (user_73, EVAL),
  `event_1442` (user_16, sample), `event_1786` (user_20, sample). The other 12 are settled history
  already inside the opening balance. Feed resolved values in through the `amount_overrides`
  mapping (`event_id -> home-currency Decimal`); `cash.py` never reads a message, an image or a
  `Fact`. Until they are resolved these rows classify as `UNKNOWN_AMOUNT`, never zero.

# 08: Spending-change generation and the proven pruning rule

**Status: COMPLETE (2026-09-13).** Implemented in `code/engine/spending.py` (new),
`code/engine/plans.py` (variant generation behind the pruning rule),
`code/engine/recurrence.py` (`detect_streams` made public, projections carry
`source_event_id`), `code/engine/pipeline.py` and `code/engine/validate.py` (output
invariant 11 in full). Tests: `tests/test_spending.py` (24), `tests/test_sample_changes.py`
(9), `SpendingChangePruningRuleTest` in `tests/test_plans.py` (6). Two corrections to the
written spec were forced by the samples and are recorded in CONTEXT.md section 10 and D8 —
see the acceptance notes below.

**What to build:** When nothing safe completes the request by its deadline, the engine finds the
smallest set of permitted flexible-spending changes that unlocks it, and reports them.

The pruning rule is a **biconditional**, not a shortcut: expand change-set variants if and only if no
safe no-change plan completes the full request by `desired_completion_date`. Proof: a safe no-change
plan that completes has rank key starting (0, 0, ...), while any change-requiring plan has
needs_changes = 1 and therefore loses at level 1 or level 2. The converse is load-bearing - samples
06, 11 and 21 all have an earliest date **after** the deadline, so no no-change plan completes,
variants must be explored, and they win at level 1. Naive pruning loses all three.

**Blocked by:** 07.

**Owner:** Claude - `code/engine/**`

**Status:** done

- [x] Variants are expanded only when no safe no-change plan completes by the deadline
- [~] Samples 06, 11 and 21 are reproduced, including their exact cited event ids — **06 and 21
  exactly**; **11 cites the right event (`event_989`) inside a set that carries one extra change**,
  for two reasons that both sit upstream of this ticket. See the note below.
- [x] Only non-fixed events qualify; stop requires stoppable or reducible_or_stoppable, reduce_to requires reducible or reducible_or_stoppable
- [x] The category must appear in the user's matching willing list and must not be protected
- [x] Only events belonging to a detected recurring stream qualify
- [x] The cited event id is the most recent settled occurrence before request_date
- [x] `reduce_to` targets minimum_allowed_amount exactly and never goes below it
- [x] Selection picks the smallest sufficient set — **but the order in this line was wrong.** It
  reads "fewest changes, then smallest total saving"; `request_21` closes its 31.05 gap with *two*
  one-change sets (`stop:event_1816`, saving 47, and `reduce_to:event_1817`, saving 76.78) and the
  published answer is nonetheless the *two*-change 34.50 set. Shipped as **smallest total saving →
  fewest changes → lowest event ids**, which produces the published answer exactly. CONTEXT.md D8
  corrected.
- [x] At most three changes; stop and reduce never target the same event; stop entries precede reduce_to entries
- [x] Sample 21 reproduces the case where reducing is chosen over stopping for an event eligible for
  both — and it falls out of the ordering above rather than needing a rule of its own.

### Sample 11, in detail

Published `reduce_to:event_989:665950`; the engine's *selection* picks
`stop:event_949|reduce_to:event_989:665950`. Two separate things are going on, and neither is the
selection rule:

1. **End to end it never reaches the change search.** Our `earliest` for `user_11` is 2025-05-15,
   *before* the 2025-06-12 deadline, so a no-change `wait` completes and the pruning rule correctly
   declines to look for changes. The published `earliest` is 2025-07-15. That is ticket 06/14 capacity
   calibration - and it is the pruning rule working, not failing.
2. **At the published shortfall nothing certifies, the published change included.** Measured, not
   assumed: the binding window minimum is on **2025-05-14** (the projected cloud-storage occurrence),
   and `user_11`'s dining stream is *variable*, so ticket 05 places its whole monthly total on the
   earliest observed day-of-month - the 2nd - putting its first projected occurrence on **2025-06-02**,
   three weeks past the minimum. `reduce_to:event_989` alone leaves the minimum at 33,541,245 against
   a 34,140,600 floor. So the real gap is **projection placement (ticket 05/14)**: for the whole of
   May, user_11 is forecast to spend nothing on dining, so there is no dining saving to be had before
   the squeeze. Flagged for calibration - placing variable spend on the earliest observed day is
   conservative for the *debit* and makes the matching *saving* land as late as possible.

   (The selection difference is smaller and downstream of that: the conservative one-occurrence dining
   saving, 1,163,530.49 − 665,950 = 497,580.49, does not reach the 599,355 shortfall alone, so
   `stop:event_949` joins it.)

All of it is asserted in `Sample11Test`, so closing any part is a visible change, not a silent drift.

### Added after review: installments can be unlocked too

The first cut built change variants for a full payment on `request_date` only. `AGENTS.md` section 6.2
names installments as one of the routes to `affordable_with_plan` "through ... permitted spending
changes", and 48 of the users whose request finds no plan do not accept `full_payment` at all - so
that restriction was an unasked-for narrowing, not a contract requirement. Variants are now built for
both, and **4 further rows** (`request_61`, `request_110`, `request_166`, `request_203`) moved from
`not_recommended` to a ledger-certified installment plan the user had already been offered and could
not previously afford. `wait` and `partial_payment` genuinely cannot be expressed as change plans:
both are pinned to columns published *before* changes.

---

## Carried in from Ticket 07 - a checkable prediction

Ticket 07 emits a late `wait` rather than `not_recommended` when `earliest` falls after the deadline
(CONTEXT.md D12): the candidate carries `completes_by_deadline = False` and loses at level 1 to
anything that completes, which is exactly what this ticket's change-variants are. On the 250
evaluation requests that produces **4 rows whose recommended plan finishes after the deadline**:

| request | method | plan finishes | deadline | miss |
|---|---|---|---|---|
| `request_78`  | `wait` | 2025-10-15 | 2025-10-14 | 1 day |
| `request_117` | `wait` | 2026-07-15 | 2026-07-14 | 1 day |
| `request_120` | `wait` | 2026-04-15 | 2026-04-14 | 1 day |
| `request_121` | `wait` | 2024-04-15 | 2024-03-14 | 32 days |

Three of the four miss by a single day, which is the samples `06`/`11`/`21` signature exactly
(`earliest` one day past the deadline, ground truth a spending-change plan that completes on time).
**Prediction: this ticket should convert most of these 4 rows to a deadline-meeting change plan.** If
one of them does not convert, that is worth understanding before shipping - either no permitted
change closes the gap, in which case a late `wait` is the honest answer, or the change-set search is
missing something. Re-run the check with:

    python code/main.py --output "%TEMP%/ow.csv"   # then compare each plan's last date to its deadline

### Prediction settled: 1 of the 4 converted, and the other 3 are the honest branch

| request | outcome | why |
|---|---|---|
| `request_78`  | **converted** | `reduce_to:event_7224:805` (video streaming) closes a 789.33 shortfall; now `full_payment` on `request_date`, `affordable_with_plan` |
| `request_117` | stands | a set exists (22 + 25.61 = 47.61 against 46.35) but the dining half first lands 2026-08-03, three weeks after the window minimum on 2026-07-10, so only 22 of it is real and the ledger refuses the plan |
| `request_120` | stands | shortfall 858,412.97; the only permitted change is one streaming stop worth 630,800 |
| `request_121` | stands | shortfall 364.76; the four permitted changes total 45.01 |

That is the prediction's "no permitted change closes the gap, in which case a late `wait` is the
honest answer" branch in three cases, and `request_117` is the sharper version of it: the arithmetic
closes but the *timing* does not, which is exactly what having the ledger certify every variant is
for. No sufficiency shortcut would have caught it.

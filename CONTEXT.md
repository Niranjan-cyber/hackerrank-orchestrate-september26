# CONTEXT.md — Buy or Wait? (HackerRank Orchestrate, Sept 2026)

Phase 1 domain brief. Evidence-backed; every claim below was verified against the actual dataset,
not inferred from the README.

> Scope note: the `domain-modeling` skill treats `CONTEXT.md` as a pure glossary. This file is
> deliberately broader (a Phase-1 brief) because that was explicitly specified. Implementation
> detail still belongs in the spec, not here.

---

## 1. Challenge understanding

For each of the 250 rows in `dataset/requests.csv`, decide whether the user should pay in full,
pay partially, use installments, wait, or not proceed — and write one row to root `output.csv`.

A recommendation is **safe** only if every payment in the plan can be made, the request completes
by `desired_completion_date`, essential spending is covered, and the projected balance never drops
below `minimum_balance_to_keep` across a 90-day forecast.

Graded on: `amount_safe_to_pay` accuracy, `affordability_status`, `recommended_payment_method` +
`payment_plan`, `earliest_date_for_full_payment`, `spending_changes_needed` validity, and the
usefulness/consistency of `decision_explanation`.

Deliverables: `code.zip` (incl. `evaluation/usage_report.md` for the final full run), `output.csv`,
and `log.txt` as the chat transcript.

---

## 2. Dataset / data model — verified facts

| File | Rows | Verified facts |
|---|---|---|
| `requests.csv` | 250 | `request_date` spans 2023-01-20 … 2026-09-04. **One request per user**, no reuse. `allows_partial_payment`: 170 false / 80 true. 9 request types, ~28 each. |
| `sample_requests.csv` | 25 | Users `user_01`…`user_25`. **Disjoint from the 250 evaluation users** — zero leakage, pure format/behaviour guide. |
| `financial_profiles.csv` | 275 | = 250 eval + 25 sample users. Currencies: INR 67, EUR 62, IDR 55, ZAR 51, USD 40. |
| `financial_events.csv` | 25,342 | 56–129 events per user. |
| `exchange_rates.csv` | 134 | 39 distinct dates. Only 5 directed pairs: USD→INR, USD→IDR, USD→EUR, EUR→USD, EUR→ZAR. |
| `request_payment_options.csv` | 790 | 2–4 options per request. **Only `full_payment` and `installments` exist** — never `partial_payment`. Exactly one `full_payment` option per request (275 total). |
| `messages.csv` | 215 | Exactly **one message per user**, for 215 of 275 users. Valid UTF-8; 60 contain smart quotes/en-dashes. |
| `images.csv` | 16 | **Exactly the 16 blank-amount events, 1:1.** All 16 PNGs present on disk. |

### Controlled vocabularies (exhaustive, from the data)

- `event_type`: `expense` 20525, `subscription` 2488, `income` 1696, `debt_payment` 567,
  `investment_purchase` 29, `refund` 22, `investment_valuation` 10, `investment_sale` 5
- `status`: `settled` 25148, `pending` 71, `scheduled` 70, `cancelled` 22, `failed` 21, `unrealized` 10
- `direction`: `debit` 23609, `credit` 1723, `non_cash` 10
- `flexibility`: `fixed` 21138, `reducible` 2682, `stoppable` 1297, `reducible_or_stoppable` 225
- `payment_methods_user_will_consider`: 7 observed combinations of the 3 methods
- Protectable categories: rent, groceries, transport, utilities, education, debt_repayment, insurance, healthcare, housing, family_support
- Reducible categories: dining, shopping, streaming, entertainment, gym
- Stoppable categories: cloud_storage, streaming, music_subscription, delivery_membership, gym

### Hard structural invariants discovered

1. **`minimum_allowed_amount` is populated exactly when `flexibility ∈ {reducible, reducible_or_stoppable}`** — perfect correlation, 2907/2907. It is the floor for `reduce_to`.
2. **`max_installment_months` is blank exactly when `installments ∉ payment_methods_user_will_consider`** — 119 blank/absent, 156 set/present, zero off-diagonal.
3. **FX coverage is complete**: all 140 foreign-currency events have an exact rate row for both their `settlement_date` and their `event_date`. 139/140 are credits. No missing-rate fallback is needed.

---

## 3. Financial-state model — what affects available cash, and when

`current_available_balance` is the balance **as of `request_date`**; all `settled` history up to that
date is already baked in. The forecast therefore starts from that number and only applies events
dated after `request_date`.

| Record | Counts toward cash? | When |
|---|---|---|
| `settled` (any direction) on or before `request_date` | No — already in the balance | — |
| `pending` **debit** | **Yes, reserve it** | `settlement_date` |
| `pending` **credit** (refunds, bonuses, commissions, lottery, investment gains) | **No** — never count until settled | — |
| `scheduled` **debit** (incl. failed-payment retries) | **Yes** | `settlement_date` |
| `scheduled` **credit** (`Next confirmed salary`) | **Yes** | `settlement_date` |
| `cancelled` | No | — |
| `failed` | No (but see lifecycle: the retry child counts) | — |
| `unrealized` / `non_cash` `investment_valuation` | **No** — not available cash | — |
| `settled` `investment_sale` (realized) | Yes | `settlement_date` |
| Row whose description marks it a **duplicate** | **No** — ignore, even though it is a pending debit | — |

Foreign-currency events convert at the rate row for **(settlement_date, from_currency, home_currency)**.

---

## 4. Recurrence rules

Recurrence must be **inferred** — the dataset contains only history plus a single explicit
`Next confirmed salary` row. Sample forensics prove inferred recurrence is required: for every
uncapped sample, the implied reserve far exceeds the sum of explicit future events
(e.g. `request_22`: implied reserve 157.00 vs. one explicit pending debit of 43.00).

The data is highly regular per user (verified on `user_22`): monthly rent on a fixed day, monthly
utilities, 4–5 grocery events/month, 4–5 transport events/month, 2–3 dining, fixed-price monthly
subscriptions, and monthly salary on the 15th.

**Adopted heuristic** (parameters are tunable, but named and few — see D2):

- **Stream key**: `(category, direction, event_type)` plus normalized description similarity.
  Normalize: uppercase → strip digits/`#`/`*`/ref numbers/dates/city tails → collapse whitespace.
  Compare with stdlib `difflib.SequenceMatcher`, **threshold 0.85**, gated on a 3-token prefix match.
- **Confirmation threshold**: **≥ 3 occurrences** within the lookback window (Plaid's maturity rule).
  2 occurrences → project only if the category is protected/essential, else ignore (conservative).
- **Lookback window**: **180 days** before `request_date`, so cancelled streams age out.
- **Amount tolerance**: same stream if `abs(a-b) <= max(0.05 * ref, 5.0 home-currency units)` —
  relative-OR-absolute, so small subscriptions survive % noise and rent survives absolute noise.
- **Date tolerance ladder**: weekly ±2d, biweekly ±3d, monthly ±5d, quarterly ±7d, annual ±10d.
- **Semi-monthly** (1st + 15th) must be modelled as two monthly streams keyed on day-of-month,
  never as one 15-day stream — a naive median-interval detector misreads it as biweekly and drifts.
- **Projection**: clamp day-of-month to month end (`min(dom, last_day)`). Roll **expenses forward**
  and **income backward** off weekends — the asymmetry is the conservative direction for a floor test.
- **Variable essential spending** (groceries/transport/dining: many small events/month) is forecast
  as a conservative monthly total placed on the observed days-of-month, not as individual events.
- **Explicit future rows always win** over an inferred projection for the same stream and date;
  never double-count a projected occurrence that an explicit `scheduled`/`pending` row already covers.

---

## 5. Event lifecycle rules (`linked_event_id`)

58 rows carry a link. Seven patterns exist, all verified with concrete examples:

| Parent → child | Meaning | Cash treatment |
|---|---|---|
| `settled` expense → `settled` refund | charge reversed / expense reimbursed | both count (net ~0) |
| `settled` expense → `pending` refund | merchant refund not yet settled | parent counts; **child does not** (pending credit) |
| `cancelled` expense → `settled` expense | card authorization → real settlement | **only the child counts** |
| `failed` debt_payment → `scheduled` debt_payment | payment retry | parent ignored; **child counts** as future debit |
| `settled` investment_purchase → `unrealized` investment_valuation | mark-to-market | parent counts; **child never** |
| `settled` investment_purchase → `settled` investment_sale | realized exit | both count |
| `settled` expense → `pending` expense, described "Possible duplicate card charge" | duplicate | **ignore the child** |

The last row is a trap: the generic rule "reserve all pending debits" is **wrong** for these 6 rows.
A link alone does not decide cash treatment — status + direction + duplicate-marking do.

**Conflict precedence** (from the spec, adopted verbatim): explicit cancellation/settlement/amendment
→ newer record from the same source → settled over estimate/forecast → financially safer reading.

---

## 6. Evidence rules — messages and images

### Images

Images exist for exactly one purpose: **recovering the 16 blank `amount` values**. `images.csv` maps
1:1 onto the 16 blank-amount events. 5 belong to sample users (usable to validate extraction),
11 to evaluation users. A blank amount must never be treated as zero.

### Messages

215 messages, one per user. 128 carry `request_id`, 39 carry `related_event_id`, 76 are user-level
only. A blank `related_event_id` means no single supplied event row describes the fact.
Multilingual (Indonesian for IDR users, English elsewhere).

Observed fact types (regex census): salary increase 22, salary decrease 19, contract ended 12,
unconfirmed bonus 8, invoice approved 14, refund 14, subscription change 11, generic confirmation 83,
cancellation 1, delay 0. Because recurring income **is** projected (§4), salary-change messages
move the forecast materially — this is the highest-value extraction target, not a nice-to-have.

Decision-relevant readings: a confirmed salary change amends the projected income stream from its
effective date; "contract ended, no renewal confirmed" **stops** projected income; an unconfirmed
bonus or an unsettled invoice is **not** income.

### Trust boundary (non-negotiable)

```text
untrusted evidence (message text, image pixels)
        |  extractor - may ONLY emit facts from a closed enum
structured facts + provenance {fact_type, event_id?, amount?, date?, currency?, verbatim_quote, confidence}
        |  deterministic validation (digits of amount must appear in verbatim_quote)
deterministic financial engine  <- sole authority over every output column
```

Injection defences, in order of importance:

1. **Structural**: the extractor's schema has no field able to express a decision. An injected
   "approve this payment" has nowhere to land. (OWASP LLM01 *enforce privilege control* +
   *define and validate output formats*.)
2. **Spotlighting** (MSR arXiv 2403.14720): wrap every untrusted payload in a per-request random
   delimiter and state in the system prompt that its content carries no authority. Reported effect:
   attack success >50% → <2%.
3. **Verbatim-quote verification**: reject any numeric fact whose digits do not appear in the quoted
   source span. Converts a hallucination into an auditable rejection.
4. **Never** let extracted free text reach `decision_explanation` verbatim — template it from engine
   state, or an injection propagates into a graded column.

---

## 7. 90-day simulation semantics

- **Start**: `request_date`. **Horizon**: `request_date + 90` days inclusive.
- **Opening balance**: `current_available_balance` (already reflects settled history).
- **Same-day ordering**: sort by `(date, kind_rank, event_id)` with **debits before credits**
  (`kind_rank` 0 = debit, 1 = credit). This is the only ordering that cannot certify a plan that
  "works" purely because salary landed before rent. It decides borderline
  `affordable_now` vs `affordable_later` rows. Assert it as an invariant.
- **Floor test**: after every single applied event, `balance >= minimum_balance_to_keep`.
  The floor is checked at every step, not only at day end.
- **`amount_safe_to_pay`** = `min over t in horizon (projected_balance(t)) - minimum_balance_to_keep`,
  clamped to `[0, requested_amount]`, computed **before** any optional spending changes.
  Structure confirmed empirically: for all 25 samples,
  `implied_reserve = (balance - minimum) - amount_safe_to_pay` is the worst cumulative drawdown,
  and a model projecting recurring expenses **and** recurring income lands in the right band
  (`request_17` 140430 vs 138790; `request_02` 13996350 vs 13827629) whereas an
  explicit-events-only model is off by orders of magnitude.
- **`earliest_date_for_full_payment`** = first date in the horizon at which paying the full
  `requested_amount` as a single payment passes the floor test **without** spending changes;
  empty if it never does. Equals `request_date` for `affordable_now`. Verified: it is independent of
  the user's method preferences (`request_02` reports 2025-09-15 while full payment is not even an
  accepted method; `request_12` reports `request_date` while recommending installments).
- Most sample `earliest` dates fall on the 15th — the salary landing date. The metric is driven by
  the next income event, which is a useful sanity check.
- **Accepted plans feed back into the forecast**: an installment plan's future payments must be
  injected as scheduled debits and the floor re-checked for the plan's whole duration, not only day 1.

### Off-by-one hazards to pin down in tests

Horizon inclusive vs exclusive; events exactly on `request_date` (excluded — already in balance);
events exactly on `request_date + 90`; month-end clamping (Jan 31 → Feb 28/29);
`settlement_date` vs `event_date` (use settlement, fall back to event when blank);
a payment and an expense on the same date (debit ordering); leap years.

---

## 8. Payment eligibility

An immediate method is eligible only if it appears in `payment_methods_user_will_consider`.

| Method | Eligibility conditions |
|---|---|
| `full_payment` | in methods; full amount passes the floor test on `request_date` (optionally after ≤3 permitted spending changes) |
| `partial_payment` | in methods **and** `allows_partial_payment` is true **and** `0 < amount_safe_to_pay < requested_amount` **and** `earliest_date_for_full_payment` is non-empty and `<= desired_completion_date`. Exactly two payments; need not match any supplied option. |
| `installments` | in methods; must **exactly** match a supplied option (`payment_amount`, `number_of_payments`, `first_payment_date`, `payment_frequency_days`); `number_of_payments <= max_installment_months`; every payment passes the floor test; final payment `<= desired_completion_date` |
| `wait` | `full_payment` in methods; full payment not safe today but `earliest_date_for_full_payment` is non-empty. Plan = one payment on that date. |
| `not_recommended` | fallback when no eligible safe plan exists. `payment_plan` = `none`, `spending_changes_needed` = `none`, `earliest` empty. |

Installment schedule generation: `first_payment_date + k * payment_frequency_days`, k = 0…n-1.
Verified exactly against `request_02` (3 × 30d), `request_07` (3 × 28d), `request_22` (3 × 28d).

### Status mapping (derived from samples, incl. a subtle one)

- `affordable_now` ⟺ `amount_safe_to_pay == requested_amount` **and** `full_payment` is in methods.
- `affordable_with_plan` — completed via installments, partial payment, or permitted spending changes.
  **`request_12` proves the subtle case**: capacity exists today (`safe == requested_amount`,
  `earliest == request_date`) but `full_payment` is not an accepted method, so the status is
  `affordable_with_plan` with `installments`, **not** `affordable_now`.
- `affordable_later` — `wait`.
- `not_affordable` — nothing eligible and safe.

---

## 9. Six-level ranking

Strictly **lexicographic**, never a weighted score — a weighted score can trade a deadline miss for
a cost saving, which the spec forbids:

1. completes the full request by `desired_completion_date`
2. requires no spending changes
3. minimises total amount paid (**fee-inclusive `total_payable_amount`**, not principal)
4. starts payment earlier
5. fewer payments
6. lowest `payment_option_id`

**Verified on `request_19`**: partial payment (total 39,660) beat an eligible 2-payment installment
option (total 41,246.40) — same deadline outcome, same zero spending changes, so level 3 decided it.

Prune ineligible options **before** ranking (over `max_installment_months`, final payment after the
deadline, method not accepted), rather than ranking then rejecting.

---

## 10. Spending-change semantics

Format: up to three `stop:<event_id>` / `reduce_to:<event_id>:<new_amount>` joined by `|`,
or `none`. Stop and reduce must target **different** events.

An event is changeable only if **all** hold:

- `flexibility != fixed`
- `stop` requires `flexibility ∈ {stoppable, reducible_or_stoppable}`
- `reduce_to` requires `flexibility ∈ {reducible, reducible_or_stoppable}`
- the event's `category` appears in the user's corresponding willing-to-reduce / willing-to-stop list
- the category is **not** in `expense_categories_to_protect`
- it is part of a detected recurring stream (the spec restricts changes to recurring expenses)

**Verified rules from samples:**

- **Which `event_id` to cite**: the **most recent settled occurrence of the stream before
  `request_date`**. `event_476` is the last of 5 monthly streaming rows; `event_1815` and `event_1816`
  are likewise each the 5th of 5.
- **`reduce_to` target** = the event's `minimum_allowed_amount`, exactly.
  `event_989` min 665950 → `reduce_to:event_989:665950`; `event_1816` min 23.5 → `...:23.50`.
- **Minimal sufficient saving**: `request_21` needed a 31.05 gap closed. `event_1816` is
  `reducible_or_stoppable` and `streaming` is in **both** the user's reduce and stop lists, yet the
  sample chose `reduce_to` (saves 23.50) over `stop` (saves 47) — combined with `stop:event_1815`
  (11) that is 34.50, which just covers 31.05. The rule is *smallest sufficient change set*, not
  *maximum saving*.
- **Output order**: `stop:` entries precede `reduce_to:` entries.
- Spending changes do **not** alter `amount_safe_to_pay` (explicitly "before optional spending
  changes") and do **not** alter `earliest_date_for_full_payment` (explicitly "without optional
  spending changes"). They only unlock `affordable_with_plan`.

---

## 11. Output invariants (deterministic validator must enforce all)

1. Exactly 250 data rows, one per `request_id`, in the 8 required columns in the required order.
2. `0 <= amount_safe_to_pay <= requested_amount`.
3. `affordability_status ∈ {affordable_now, affordable_with_plan, affordable_later, not_affordable}`.
4. `recommended_payment_method ∈ {full_payment, partial_payment, installments, wait, not_recommended}`.
5. `affordable_now` ⟹ `earliest_date_for_full_payment == request_date`.
6. `payment_plan` is chronological `YYYY-MM-DD:amount` joined by `|`, or `none`.
7. `partial_payment` ⟹ exactly 2 payments; `p1 == amount_safe_to_pay` on `request_date`;
   `p2 == requested_amount - amount_safe_to_pay` on `earliest_date_for_full_payment`;
   `p1 + p2 == requested_amount`; `allows_partial_payment` true; status is `affordable_with_plan`.
8. `installments` ⟹ plan matches a supplied option exactly; `n <= max_installment_months`.
9. `wait` ⟹ exactly one payment, on `earliest_date_for_full_payment`, for `requested_amount`.
10. `not_recommended` ⟹ `payment_plan == none`, `spending_changes_needed == none`, `earliest` empty.
11. `spending_changes_needed`: ≤3 entries; every event exists, is non-protected, has permitted
    flexibility and category; `reduce_to` amount `>= minimum_allowed_amount`; no event appears twice.
12. **Number formatting** (reverse-engineered — easy to get wrong):
    - `payment_plan` and `reduce_to` amounts: **2 decimals when fractional**, bare integer otherwise
      (`620.40`, `23.50`, `15952906.67`, but `25256`).
    - `amount_safe_to_pay`: natural shortest representation, **no trailing-zero padding**
      (`603.3`, `17229139.2`, `243849.58`).
13. Use `decimal.Decimal` with `ROUND_HALF_UP`, quantized at every write. Float drift across
    25k rows × 250 requests flips borderline floor comparisons and destroys reproducibility.

---

## 12. Sample-forensics behaviour table

| Samples | Pattern established |
|---|---|
| 01, 09, 16 | `safe == req` + `full_payment` accepted → `affordable_now` / `full_payment` / `earliest = request_date` |
| 12 | `safe == req` but `full_payment` **not** accepted → `affordable_with_plan` / `installments` |
| 02, 07, 17, 22 | installments: `n <= max_installment_months`, schedule = `first + k*freq`, last payment ≤ deadline |
| 19 | partial beat installments on fee-inclusive total → level-3 lexicographic tie-break |
| 06, 11, 21 | spending changes unlock full payment today; `safe` and `earliest` stay pre-change |
| 03, 04, 08, 13, 18, 23 | `wait`: single payment on `earliest`, status `affordable_later` |
| 05, 10, 14, 15, 20, 24, 25 | `earliest` empty ⟹ `not_affordable` / `not_recommended` / `none` / `none` |
| 10, 14, 24 | partial ineligible purely because `earliest` is empty, despite `0 < safe < req` |
| 03, 05, 23, 25 | every installment option exceeded `max_installment_months` ⟹ all pruned |

---

## 13. Decisions made

Record format: **Decision / Context / Options / Chosen / Why / Trade-offs / Evidence / Impact.**
Full records to be written as ADRs under `docs/adr/` during `/to-spec`; summarised here.

| # | Decision | Chosen | Why |
|---|---|---|---|
| D1 | Engine vs LLM authority | Deterministic Python owns every output column; LLM only extracts facts | Reproducibility, auditability, and the spec's determinism requirement; also the primary injection defence |
| D2 | Recurrence heuristic | ≥3 occurrences / 180-day lookback / 5%-or-absolute amount tolerance / per-frequency date ladder | Matches Plaid's maturity rule and BBVA's tolerance ladder; few named knobs instead of per-case tuning |
| D3 | Recurring income projection | **Project** recurring salary streams, not just the one explicit `Next confirmed salary` row | Sample evidence is decisive: explicit-only under-reserves by orders of magnitude. A ≥3-occurrence history is *supported*, not *invented* |
| D4 | Same-day ordering | Debits before credits, then `event_id` | Only ordering that cannot certify a plan on the strength of salary landing before rent |
| D5 | Arithmetic | `Decimal` from `str`, quantize on write, **directional rounding**: `ROUND_DOWN` for `amount_safe_to_pay`, `ROUND_UP` for projected expenses | Prevents non-reproducible floor-comparison flips. Directional beats half-up: it is aligned with the floor test we certify (supersedes the earlier blanket ROUND_HALF_UP) |
| D6 | Ranking | Lexicographic comparison on a 6-tuple | A weighted score can trade away a deadline; the spec forbids that |
| D7 | `reduce_to` target | Always `minimum_allowed_amount` | Matches every sample exactly |
| D8 | Change-set selection | Smallest sufficient set: fewest changes → smallest total saving that still passes → lowest `event_id` | `request_21` chose reduce-over-stop when stop would have over-saved |
| D9 | Duplicate handling | Ignore rows marked as possible duplicates even when `pending debit` | Spec says ignore duplicates; 6 such rows would otherwise be double-reserved |
| D10 | Dependencies | stdlib only, plus the `anthropic` SDK | Nothing researched justified a dependency; pandas adds dtype surprises for a 25k-row job |
| D11 | Explanations | Deterministic templates **rendered from reason codes** | Consistency is graded; stops injected text reaching a graded column; reason codes make it grounded rather than generic |

### Confirmed with the user (2026-09-12)

| # | Decision | Chosen |
|---|---|---|
| D12 | Calibration stopping rule | Ship the tracer bullet first, then **timebox recurrence / `amount_safe_to_pay` calibration to one 3-hour block** and freeze the parameters at the end of it |
| D13 | API key posture | **No API key is assumed or required.** The extraction layer is built behind the agreed interface with recorded fixtures so the engine develops without it |
| D14 | `decision_explanation` | Deterministic and template-based, generated from verified engine facts. The final financial decision stays entirely deterministic |
| D15 | Agent division | Proposed Claude Code ↔ opencode split with strict file ownership; **opencode does not start until the extraction fact-schema contract is written and persisted** |

### Added from the research pass (details in `docs/research/` and `docs/architecture/`)

| # | Decision | Chosen | Why |
|---|---|---|---|
| D16 | Architecture shape | **Ports & Adapters** + **Functional Core / Imperative Shell**: pure core over frozen dataclasses, `ExtractionPort` with `AnthropicAdapter` and `FixtureExtractor`, shell confined to `main.py` | Cockburn's stated purpose is exactly "developed and tested in isolation from its eventual run-time devices" — i.e. D13 |
| D17 | Fixture strategy | **Content-addressed fixtures** keyed by `sha256(template_version｜model_id｜canonical_input)`, hard-fail on miss; recording is a byproduct of the first real call | One code path for fixtures and live. Putting the template version in the key makes a stale fixture impossible. `vcrpy` rejected: dependency, and it records below the SDK |
| D18 | Fixed vs variable spend | **Two-tier**: fixed streams at their **last observed amount** on the exact date; variable categories at `max(median of last 3 monthly totals, mean of last 6 monthly totals)` | Monzo carries forward last month's amount; Monarch uses a 6-month average. `max` leans conservative in the one direction CONC 5.2A.19G(1) cares about. **p90 rejected** — no source, and it would over-reject |
| D19 | Evidence authority | Evidence may **cancel, delay, or reduce** a fact freely; **creating or increasing an inflow** requires an authoritative third-party source **and** an unconditional statement | Closed enums stop format attacks, not content attacks. This makes injection financially inert in the dangerous direction while still honouring genuine employer payroll amendments. Grounded in CONC 5.2A.15R(5)/5.2A.16G(3) |
| D20 | Untrusted transport | Untrusted text goes in **`tool_result` blocks, JSON-encoded**, not plain user text with delimiters | Anthropic's explicit guidance; JSON escaping removes break-out, and the model is trained to be sceptical of tool-result instructions. Supersedes plain spotlighting as the transport |
| D21 | Cost levers | **One call per item. No prompt caching, no Batches API, no multi-item packing** | Caching is impossible (our prompt is ~600–900 tokens vs a 1,024–4,096-token minimum prefix); Batches saves ~$0.35 for an unbounded wait; packing costs accuracy **and** allows cross-item contamination. Whole run is under $1 |
| D22 | Validator failure mode | Validator **returns a violations list** (Fowler's Notification), never raises; plans carry a `reasons` tuple | A malformed request must not kill the batch at row 300 of 250 — that is a submission-integrity property. Reason codes double as the provenance feed for D11/D14 |
| D23 | Output byte-stability | CSV writer uses `newline=""` + `lineterminator="\n"` + `str(Decimal)`; `sorted()` around every set iteration; `PYTHONHASHSEED=0` documented | We are on Windows and the grader is likely Linux. This is the cheapest high-value determinism fix in the project |
| D24 | Test runner | stdlib **`unittest`** | `pytest` is not installed here (broken `iniconfig`), and stdlib keeps the zero-dependency promise for graders |
| D25 | Harness shape | Golden-file + `--update` flag + **per-column scorecard**; `decision_explanation` reported separately, never a pass/fail gate | Characterization testing (Feathers) + the Go `testdata` convention: accepting a deviation becomes a visible diff in the commit |

## 14. Decisions still requiring confirmation

Carried to the next grilling round:

1. **`affordable_now` philosophy** — YNAB deliberately does not forecast at all. Should
   `affordable_now` rest on settled cash only, with projections confined to
   `earliest_date_for_full_payment`? Sample evidence constrains `amount_safe_to_pay` to be net of
   projections, but the *status* assignment is a separate choice.
2. **Issue tracker viability** — Issues are **disabled** on the fork (`has_issues: false`), so
   `gh issue create` fails today. Enable them, or drop to a local markdown tracker for a solo build?
3. **Image model routing** — cost is ~5 cents either way, so this is purely an accuracy call:
   Sonnet 5 for all 16 images, and do we spend ~$0.13 on two-call self-consistency?
4. **`verification-before-completion` skill** — install it, or rely on the validator plus a manual
   packaging checklist?
5. **Evidence-authority strictness (D19)** — confirm the asymmetric rule is the right reading of
   "use messages to amend facts", given 22 of our messages are genuine salary *increases*.

---

## 15. AI / LLM boundary

LLM calls are justified in exactly two places, both pure extraction:

1. **16 image calls** — recover the 16 blank `amount` values. Unavoidable: the number exists only
   in pixels. 5 of the 16 belong to sample users, so extraction accuracy is directly verifiable.
2. **215 message calls** — one per message, emitting closed-enum facts. Necessary because messages
   change projected recurring income, which drives `amount_safe_to_pay` and `earliest`.

Everything else — state reconstruction, recurrence, simulation, plan generation, ranking, validation,
formatting, explanations — is deterministic Python. No LLM call may return a decision, an amount that
is not verbatim-verifiable, or text that reaches a graded column unmediated.

---

## 16. Token / cost logging design

Two distinct things, not to be conflated:

- **Development-agent usage** — Claude Code / opencode during the build. Captured in `log.txt`
  (the required transcript). **Not** what `usage_report.md` is about.
- **Application-model usage** — LLM calls made by the submitted application. **This** is the
  `evaluation/usage_report.md` subject, and it must describe the actual final full-dataset run.

```text
application
   |
LLM extraction interface         <- single choke point; every call goes through it
   |
usage logger (append-only JSONL) <- never throws; failures degrade to a counter
   |
evaluation/usage_raw.jsonl
   |
report generator -> evaluation/usage_report.md
```

Per call, record: ISO-8601 timestamp, provider, model id, purpose (`image_amount` | `message_fact`),
subject id (`request_id` / `event_id` / `message_id`), input tokens, output tokens, cache-read and
cache-creation tokens, total, estimated cost, retry count, error class if any.

Fail-safe rules: the logger wraps every write in try/except and never propagates — a logging failure
must not fail a run; it appends (never rewrites) so a crashed run keeps its partial record; it writes
only ids and token counts, **never** prompt content, response content, API keys, or PII. The report
is generated **from** the JSONL after the final run, so the numbers are measured, not reconstructed.

Pricing for the cost column (per MTok in/out): Opus 5 $5/$25, Sonnet 5 $2/$10, Haiku 4.5 $1/$5.

---

## 17. Claude Code / opencode workflow

Single source of truth: this repo + GitHub issues on `origin`. opencode is a second terminal for the
same solo author, never a co-author. Ownership is split by **file**, so the two never edit the same
path; integration happens through short-lived branches merged frequently.

---

## 18. Skills, MCPs, and tooling

**Full inventory and workflow integration: [`docs/agents/tooling.md`](docs/agents/tooling.md).**

Conclusions:

- **Skills**: the installed set covers this build — `tdd`, `diagnosing-bugs`, `to-spec`,
  `to-tickets`, `code-review`, `domain-modeling`, `writing-for-agents`, plus Anthropic's
  `claude-api`. **One install recommended**: `obra/superpowers@verification-before-completion` as the
  packaging-stage guard (open item, §14.4).
- **MCPs**: **no MCP server is load-bearing for this project; install and wire none.** Of those
  present, only `ide → getDiagnostics` is even mildly useful, and `ide → executeCode` is
  **non-functional here** (no IPython/jupyter_client). Rejected as additions: filesystem (redundant),
  sqlite (undermines determinism), fetch (native `WebSearch`/`WebFetch` already exist).
- **Environment constraints that bind the build**: Python 3.13.9 is `python`, **`python3` is a broken
  WindowsApps stub**; **`pytest` is not installed** → stdlib `unittest` (D24); `gh` is authenticated
  but **Issues are disabled on the fork** (§14.2).

---

## 19. Research conclusions

Full sourced detail lives in three artifacts; only the conclusions belong here:

- [`docs/research/external-references.md`](docs/research/external-references.md) — recurrence
  detection parameters, Anthropic structured-outputs/vision/cost mechanics, prompt-injection analysis.
- [`docs/research/competitive-insights.md`](docs/research/competitive-insights.md) — FCA CONC 5.2A,
  UK SFS, Monzo/Simple/Monarch/YNAB/NatWest product practice.
- [`docs/architecture/reference-patterns.md`](docs/architecture/reference-patterns.md) — Ports &
  Adapters, Functional Core/Imperative Shell, content-addressed fixtures, golden-master harness,
  Python determinism, decision tables, Notification/reason codes.

Conclusions that changed the design:

1. **The 90-day horizon is production-normal** (NatWest forecasts exactly 90 days). Settled — no
   longer a risk item.
2. **Our affordability predicate has a regulated analogue.** CONC 5.2A.12R(5) defines sustainable
   repayment as meeting commitments and essential living expenses without adverse impact — a
   three-part test, not "balance stays positive". Adopted as the explanation wording.
3. **Unconfirmed income exclusion is regulator-backed**, not merely cautious: CONC 5.2A.15R(5)
   requires appropriate evidence, and 5.2A.16G(3) says a self-statement is not sufficient. Our
   messages are **all third-party** (`employer`/`bank`/`merchant`/`service_provider`/
   `financial_service`; no self-reported category exists), so a confirmed employer amendment *is*
   evidence while anything conditional is not. This resolves the apparent spec tension.
4. **Different estimators for different spend types** (D18) — last-observed for fixed streams,
   conservative statistic for variable categories. Each half has a production source.
5. **Three of my cost assumptions were wrong** (D21): caching is unusable at our prompt size,
   batching trades money for unbounded latency, and multi-item packing costs accuracy *and* opens
   cross-item injection. One call per item; the whole run is under $1.
6. **Closed enums defend format, not semantics** — hence the evidence-authority rule (D19) and
   `tool_result` transport (D20). This was the most significant gap the research pass found.
7. **No external library earned a dependency.** Verdict stands: stdlib + `anthropic` only.

---

## 20. Architecture diagrams

### System architecture

```mermaid
flowchart TD
    A[dataset CSVs] --> B[loaders + FX normalisation]
    B --> C[financial state reconstruction]
    M[messages.csv] --> E[LLM extraction layer]
    I[images.csv + PNGs] --> E
    E --> F[structured facts + provenance]
    F --> G[deterministic fact validation]
    G --> C
    C --> H[recurrence detection]
    H --> J[90-day simulator]
    J --> K[candidate plan generation]
    K --> L[lexicographic ranker]
    L --> N[output validator]
    N --> O[output.csv]
    E --> P[usage logger]
    P --> Q[evaluation/usage_report.md]
```

### Evidence flow and trust boundary

```mermaid
flowchart LR
    subgraph UNTRUSTED
        A[message text]
        B[image pixels]
    end
    subgraph EXTRACTION
        C[closed-enum fact schema]
    end
    subgraph DETERMINISTIC
        D[digit-in-quote verification]
        E[conflict precedence]
        F[financial state]
    end
    A --> C
    B --> C
    C --> D
    D -->|verified| E
    D -->|rejected + logged| X[discarded]
    E --> F
```

### Payment decision flow

```mermaid
flowchart TD
    A[request] --> B[compute amount_safe_to_pay]
    B --> C[compute earliest_date_for_full_payment]
    C --> D[enumerate candidate plans]
    D --> E{eligible?}
    E -->|no| F[prune]
    E -->|yes| G{passes 90-day floor test?}
    G -->|no| H{3 or fewer permitted changes fix it?}
    H -->|no| F
    H -->|yes| I[candidate plus change set]
    G -->|yes| I
    I --> J[lexicographic rank on the 6-tuple]
    J --> K{any candidate?}
    K -->|yes| L[emit winner]
    K -->|no| M[not_recommended]
```

### Development workflow

```mermaid
flowchart TD
    H[solo author] --> CC[Claude Code]
    H --> OC[opencode]
    CC --> R[repo plus GitHub issues]
    OC --> R
    R --> S[file-level ownership, no shared paths]
    S --> T[short-lived branches]
    T --> U[integration plus code review]
    U --> V[full run: output.csv plus usage_report.md]
```

---

## 21. Interview-worthy decisions

D1 (deterministic authority), D3 (projecting recurring income, justified by sample evidence rather
than assumption), D4 (debits-before-credits as a safety-conservative invariant), D6 (lexicographic
over weighted ranking), D8 (smallest sufficient change set, discovered from `request_21`), and the
§6 trust boundary with verbatim-quote verification. Each has a real alternative that was rejected
for a stated reason — that is what makes them worth discussing.

---

## 22. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Recurrence model mis-calibrated → `amount_safe_to_pay` systematically off | **Highest** | Score against the 25 samples continuously; keep knobs few and named |
| Over-fitting to 25 samples (9% of users) | High | Only tune named parameters with a stated rationale; never special-case a request |
| Formatting mismatch (decimals, empty vs `none`) | High — can zero columns | Validator enforces §11.12 on every row |
| Blank amount treated as zero | High | Validator asserts all 16 blank-amount events resolved before any run |
| Duplicate pending debit double-reserved | Medium | D9 |
| Injected instruction reaching a graded column | Medium | §6 layers 1–4 |
| Float drift flipping floor comparisons | Medium | D5 |
| `python3` unavailable locally (WindowsApps stub) | Low | Use `python`; document both in the README |
| Usage report not matching the final run | Medium — hard requirement | Generate from JSONL written during that run only |

---

## 23. Testing strategy

- **Unit**: FX conversion; recurrence detection on hand-built streams (monthly, semi-monthly,
  irregular, 2-occurrence); each of the 7 lifecycle patterns; same-day ordering; month-end clamping;
  number formatting per §11.12; `Decimal` rounding.
- **Property**: `0 <= amount_safe_to_pay <= requested_amount`; plan amounts always sum to
  `requested_amount` for partial; a plan certified safe never breaches the floor when re-simulated.
- **Golden**: score all 25 samples per column; track per-column accuracy as the primary metric and
  treat regressions as build failures.
- **Adversarial**: a message instructing approval must change nothing; a numeric fact whose digits
  are absent from its verbatim quote must be rejected; an installment option exceeding
  `max_installment_months` must be pruned, never ranked.
- **Contract**: full-dataset run produces 250 rows, exact columns, all enums valid, and
  `evaluation/usage_report.md` present and non-empty.

---

## Implementation Guardrails

Read before touching the implementation:

- [ ] Deterministic Python decides **every** output column. No LLM output is ever a decision.
- [ ] LLM calls happen in exactly two places: 16 image amounts, 215 message facts. Nowhere else.
- [ ] All money arithmetic uses `Decimal` with `ROUND_HALF_UP`. No floats in the ledger.
- [ ] Same-day ordering is debits before credits, then `event_id`. Assert it.
- [ ] Never treat a blank `amount` as zero — resolve it from the linked image or fail loudly.
- [ ] Never count pending credits, unrealized valuations, cancelled/failed rows, or duplicate-marked rows.
- [ ] `amount_safe_to_pay` and `earliest_date_for_full_payment` are computed **without** spending changes.
- [ ] Installment plans must match a supplied option exactly; prune ineligible options before ranking.
- [ ] Ranking is lexicographic on the 6-tuple. Never a weighted score.
- [ ] `reduce_to` amounts never go below `minimum_allowed_amount`; changes only on non-protected,
      permitted-category, correctly-flexible events; cite the latest pre-request occurrence.
- [ ] Respect the §11.12 formatting rules exactly.
- [ ] Read secrets from environment variables only. Never modify anything under `dataset/`.
- [ ] Never hardcode sample answers or any per-request special case.
- [ ] The validator is the only writer to `output.csv`, and it runs on every row before the write.

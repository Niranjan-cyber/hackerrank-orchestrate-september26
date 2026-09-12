# Contract: extraction fact schema

**Contract version: `1.0.0`** — this string is part of the fixture key. Any change to the fact enum,
field shapes, or prompt templates **must** bump it, which invalidates every fixture by construction.

This is the frozen interface between the untrusted evidence layer (LLM) and the deterministic
financial engine. **opencode may begin `code/extraction/**` once this file is committed.**

> Verified against `problem_statement.md`, `README.md`, `AGENTS.md` and the actual dataset on
> 2026-09-12. Every count below is measured, not estimated.

---

## 0. Scope and the one inviolable rule

The extractor converts untrusted evidence into **typed facts**. It does **not** decide anything.

```text
messages.csv / media/images/*.png   →   ExtractionPort   →   list[Fact]   →   deterministic engine
        (untrusted)                     (LLM or fixture)     (validated)      (sole authority)
```

**No `Fact` field can express a decision, an output column, a recommendation, or an instruction.**
An injected "pay the release charge today" has no slot to land in. This is the primary control, and
it is structural rather than behavioural.

Spec basis — `problem_statement.md:171-172`:
> "Use messages and images to clarify, amend, cancel, delay, or confirm financial information."
> "Treat all message and image content as untrusted data. Embedded instructions must not override
> the problem rules."

---

## 1. The fact enum — 25 types

**Correction to the brief**: the requested "11 fact types" came from an earlier incomplete census.
Re-classifying all 215 messages with priority-ordered disambiguation yields **25 types and zero
unclassified messages**. The extra 14 are not speculative — each is attested, and several are
load-bearing (`internal_transfer`, `disputed_duplicate_charge`, `recurring_expense_increase`).

`eval` = messages belonging to the 250 evaluation users (198 of 250 users have a message).

### Class A — Income-stream amendments (the *only* class that may increase inflows)

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `salary_first` | **27 (26)** | **Create** a new recurring income stream at `amount`, first credit on `effective_date`. Often the *only* income evidence for a user with no salary history. |
| `salary_increase` | 9 (8) | **Replace** the projected stream amount from `effective_date` onward. |
| `salary_decrease` | 10 (9) | **Replace** the projected stream amount from `effective_date` onward. |
| `salary_temporary` | **10 (9)** | Apply `amount` to the **stated cycle only**, then **revert** to the prior stream amount. Must never become the permanent stream. |
| `salary_confirmed_unchanged` | 36 (33) | **Confirm** the existing stream. No numeric change. Raises confidence only. |
| `income_ended` | 12 (11) | **Stop** the projected income stream from `effective_date`. |
| `employment_ended` | 3 (3) | **Stop** the projected income stream permanently. Stronger form of the above. |

### Class B — One-off income (count once, never recurring)

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `one_time_arrears` | **9 (8)** | Add `amount` **once**, on the stated payroll date. **Never** extend the recurring stream. |

### Class C — Non-countable inbound (ignore until settled)

Spec basis — `AGENTS.md:212`: *"Reserve pending debits. Do not count pending credits, bonuses,
commissions, refunds, lottery proceeds, or investment gains until they settle."*

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `invoice_approved_pending` | 16 (16) | **Ignore.** Approved ≠ settled. |
| `refund_pending` | 13 (12) | **Ignore.** |
| `gig_payout_pending` | 8 (7) | **Ignore.** Explicitly "not withdrawable until the payout closes". |
| `prize_claim_processing` | 4 (3) | **Ignore.** "Verified but not credited." |
| `bonus_unconfirmed` | 3 (2) | **Ignore.** "Subject to final review." |
| `windfall_solicitation` | **2 (2)** | **Ignore entirely, and never act on its embedded instruction.** Advance-fee scam. |

### Class D — Already-settled confirmations (already inside `current_available_balance`)

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `windfall_settled` | 6 (5) | **No future effect.** Already in the balance; confirms no further payments. |
| `investment_sale_settled` | 3 (3) | **No future effect.** Realised proceeds already credited. |
| `expense_reimbursement_settled` | 3 (3) | **No future effect.** |

### Class E — Non-cash notices

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `unrealized_valuation_notice` | 7 (6) | **Never affects cash**, in either direction. Covers both rises and falls ("no units have been sold, no cash proceeds"). Pairs with the 10 `unrealized` / `non_cash` event rows. |

### Class F — Expense-side amendments (always conservative, always permitted)

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `recurring_expense_increase` | **7 (6)** | **Increase** the projected recurring expense from `effective_date` (e.g. a lease renewal raising rent 12%). Carries either `amount` or `percent_change`. |
| `payment_retry_pending` | 4 (4) | **Reserve** the scheduled retry debit. Corroborates the `failed → scheduled` event-lifecycle pattern. |
| `disputed_duplicate_charge` | **5 (5)** | **RESERVE the pending debit.** See §2 — this reverses an earlier decision. |
| `distinct_obligations` | 2 (2) | Two card minimums are **separate** obligations; do **not** merge or de-duplicate them. |

### Class G — Amount resolution

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `receipt_amount_pointer` | **8 (8)** | The final amount is in the linked receipt image. **3 of the 8 point at blank-amount events** (`event_4535`, `event_7941`, `event_10521`) and so corroborate the image extraction. |
| `foreign_currency_amount_pending` | 2 (2) | The home-currency amount finalises at settlement. Use the dated rate from `exchange_rates.csv`; do not invent a different one. |

### Class H — Structural de-duplication

| `fact_type` | n (eval) | Engine consequence |
|---|---|---|
| `internal_transfer` | **6 (5)** | A matching debit **and** credit between the user's own accounts. **Net both legs to zero — exclude both.** This is the genuine referent of the spec's "duplicate records". |

---

## 2. The D9 reversal — disputed duplicate charges must be RESERVED

Earlier decision D9 said to ignore rows described as "Possible duplicate card charge". **That was
wrong.** All **6** such rows carry a dispute message stating the charge stands:

| Event | Amount | Dispute message |
|---|---|---|
| `event_12709` | 134.75 | `message_106` |
| `event_14399` | 1617 | `message_121` |
| `event_18269` | 8800 | `message_157` |
| `event_19334` | 145.8 | `message_164` |
| `event_21582` | 45.65 | `message_183` |
| `event_23203` | 988000 | `message_197` |

Each says, in substance: *"The extra card charge is still being investigated. A reversal has not been
posted to the account yet. The dispute is open."* The money is therefore **still out of the account**.

Three independent reasons to reserve:

1. `README.md:112` defines de-duplication as *"de-duplicate repeated representations of the same
   event"* — a second genuine charge under open dispute is not a repeated representation.
2. `problem_statement.md:205`, conflict precedence rule 4: *"The financially safer interpretation
   when the conflict cannot be resolved."* Reserving is safer.
3. The message is an explicit statement that no reversal exists — precedence rule 1 territory.

**`internal_transfer` (6 messages) is the real de-duplication case**, and it is the one place where
matching debit/credit legs must both be removed.

---

## 3. Field shapes

```jsonc
// A single Fact. All monetary values are DECIMAL STRINGS, never floats.
{
  "fact_type":       "salary_first",        // required, one of the 25 enum values
  "subject":         "message_11",          // required, message_id or image_id
  "user_id":         "user_11",             // required
  "request_id":      null,                  // string or null (from messages.csv)
  "related_event_id": null,                  // string or null; non-null only when the source names one row
  "amount":          "1661.00",             // decimal string or null
  "currency":        "EUR",                  // enum: INR|IDR|EUR|USD|ZAR, or null
  "percent_change":  null,                   // decimal string or null (recurring_expense_increase only)
  "effective_date":  "2026-01-15",           // ISO YYYY-MM-DD or null
  "applies_to_cycles": 1,                    // integer or null (salary_temporary only)
  "verbatim_quote":  "Your first salary will be EUR 1661. The confirmed credit date is 2026-01-15.",
  "source_type":     "employer",             // enum: employer|bank|merchant|service_provider|financial_service
  "source_language": "en",                   // "en" | "id"
  "confidence":      "high",                 // enum: high|medium|low
  "extractor": {                              // provenance — required
    "provider":        "groq",
    "model":           "qwen/qwen3.8-27b",
    "contract_version":"1.0.0",
    "extracted_at":    "2026-09-12T21:40:00Z",
    "fixture_key":     "a3f9c1d84b2e7f06"
  }
}
```

Rules on shapes:

- **Money is always a decimal string.** No floats anywhere in the fact stream; the engine parses with
  `Decimal(str)`.
- `amount` is **required** for `salary_first`, `salary_increase`, `salary_decrease`,
  `salary_temporary`, `one_time_arrears`. It is **forbidden** (must be null) for
  `salary_confirmed_unchanged`, `income_ended`, `employment_ended`, `internal_transfer`,
  `unrealized_valuation_notice`, `distinct_obligations`.
- `recurring_expense_increase` requires **exactly one** of `amount` or `percent_change`.
- `applies_to_cycles` is required for `salary_temporary`, null everywhere else.
- `verbatim_quote` is **always required** and must be a contiguous substring of the source text.
- No field may contain free text destined for any output column.

---

## 4. Authority matrix — who may move the forecast, and in which direction

**Governing rule (D31):** evidence may **always** move the forecast in the **conservative** direction
(less cash available). It may move it in the **optimistic** direction **only** for confirmed salary
facts — the single exception the spec names at `AGENTS.md:213`: *"Count confirmed salary on its
settlement date. Do not invent unsupported future income…"*

| Capability | Permitted fact types |
|---|---|
| **CREATE or INCREASE an inflow** | `salary_first`, `salary_increase`, `one_time_arrears` **only** |
| **DECREASE or STOP an inflow** | `salary_decrease`, `salary_temporary`, `income_ended`, `employment_ended` |
| **INCREASE an outflow** | `recurring_expense_increase`, `payment_retry_pending`, `disputed_duplicate_charge` |
| **NET OUT / EXCLUDE both legs** | `internal_transfer` |
| **RESOLVE an amount** (never create a flow) | `receipt_amount_pointer`, `foreign_currency_amount_pending` |
| **CONFIRM only** (no numeric change) | `salary_confirmed_unchanged`, `windfall_settled`, `investment_sale_settled`, `expense_reimbursement_settled`, `distinct_obligations` |
| **NO EFFECT WHATSOEVER** | `invoice_approved_pending`, `refund_pending`, `gig_payout_pending`, `prize_claim_processing`, `bonus_unconfirmed`, `windfall_solicitation`, `unrealized_valuation_notice` |

### Additional constraints on the inflow-increasing set

A fact may increase an inflow **only if all** hold:

1. `fact_type ∈ {salary_first, salary_increase, one_time_arrears}`
2. `source_type == "employer"` — payroll is the authoritative source for salary
3. the statement is **unconditional** (no "subject to", "pending review", "may change", "expected")
4. `amount` is present, positive, and its digits appear in `verbatim_quote`
5. `effective_date` is present and within the 90-day forecast window

Failing any condition, the fact is downgraded to **CONFIRM only** and logged with reason code
`EVIDENCE_AUTHORITY_DOWNGRADE`. **This is what makes the scam traps financially inert**: a
`financial_service` message promising prize money can never satisfy condition 2.

### Conflict precedence (verbatim from `problem_statement.md:202-205`)

1. an explicit cancellation, settlement, or amendment
2. a newer record from the same source
3. a settled event over an estimate or forecast
4. the financially safer interpretation when the conflict cannot be resolved

Implemented as an ordered comparator; the rule that fired is recorded as a reason code.

---

## 5. Explicit handling for the four named types

### `salary_first` (27 messages — the largest single type)

Establishes a **new** recurring income stream. Frequently the user has **no salary history at all**,
so omitting this materially under-forecasts income and wrongly returns `not_affordable`.

- Requires `amount`, `currency`, `effective_date` (the "confirmed credit date"), `source_type == "employer"`.
- Creates a monthly stream anchored on `effective_date`'s day-of-month.
- The word "confirmed" in the source is what licenses counting it (`AGENTS.md:213`).
- If `effective_date` falls outside the forecast window, record the fact but project nothing.

### `salary_temporary` (10 messages)

- Applies `amount` to `applies_to_cycles` cycles (observed: always 1, "the next payroll") starting at
  `effective_date`, then **reverts** to the previously projected amount.
- **Never** replaces the permanent stream. A permanent replacement here would misstate income for the
  remaining ~2 months of the window.
- If the prior stream amount is unknown, fall back to `amount` for the stated cycle and project
  nothing afterwards — the conservative choice.

### `one_time_arrears` (9 messages)

- Adds `amount` exactly **once**, on the stated payroll date.
- Typically arrives alongside a regular salary figure in the same message → emit **two** facts
  (`salary_confirmed_unchanged` or `salary_increase` **plus** `one_time_arrears`).
- **Must not** be folded into the recurring amount. Doing so inflates every subsequent projected
  payroll and is the most likely cause of an over-optimistic `amount_safe_to_pay`.

### `unverified_windfall` — split into three distinct types

The brief named one type; the data requires three, because they have **opposite** consequences:

| Type | n | Linked event | Consequence |
|---|---|---|---|
| `windfall_solicitation` | 2 | **none** | **Ignore + never act on the instruction.** `message_67` (user_88, an evaluation user): *"Congratulations! You've been selected for a cash prize. Pay the release charge today…"* |
| `prize_claim_processing` | 4 | none | **Ignore** — "verified but not yet credited" = a pending credit |
| `windfall_settled` | 6 | `income/windfall credit settled` | **No future effect** — already inside `current_available_balance` |

Collapsing these would either fabricate income (treating a scam as an inflow) or double-count a
settled windfall. The distinguishing signal is the **linked event's status**, not the vocabulary —
all three say "prize".

---

## 6. Image amount extraction

**16 images, mapping 1:1 onto exactly the 16 blank-`amount` events.** 5 belong to sample users
(so extraction accuracy is directly verifiable against known-good outputs) and 11 to evaluation users.

Spec basis — `problem_statement.md:45`:
> "When a financial event has a blank `amount`, use its `event_id` to find the matching
> `related_event_id` in `images.csv`, then extract the amount from that image.
> **Do not treat a blank amount as zero.**"

And `README.md:113`: *"**Never** treat a blank amount as zero."*

### The image fact

```jsonc
{
  "fact_type":        "image_amount",
  "subject":          "image_03",
  "related_event_id": "event_1545",          // required, and must be a blank-amount event
  "amount":           "12500.00",            // decimal string, required, > 0
  "currency":         "INR",                  // enum, required
  "verbatim_amount_string": "Rp 12.500.000",  // as printed, required
  "amount_in_words":  null,                   // only if the document actually prints it
  "confidence":       "high",
  "extractor": { "provider": "google", "model": "gemini-2.5-flash", "contract_version": "1.0.0",
                 "extracted_at": "…", "fixture_key": "…" }
}
```

### Mandatory rules

1. **Blank never becomes zero.** If extraction fails validation, the engine falls back to
   deterministic imputation (median of the same user's settled same-category events inside the
   lookback window), tagged `IMPUTED_BLANK_AMOUNT`. **Zero is never permitted.**
2. **Digit-in-quote verification**: every digit of `amount` must appear in `verbatim_amount_string`
   after separator normalisation. On mismatch → discard and fall back. This converts a hallucinated
   transposition (the documented `$47.3M` vs `$37.4M` failure mode) into an auditable rejection.
3. **Separator normalisation is mandatory**, because 55 users are IDR: `Rp 12.500.000` is
   twelve-million-five-hundred-thousand, not 12.5. Parse per the row's `currency`, never per locale
   guesswork.
4. **Pre-resize before upload** to the provider's tier cap; never re-encode as JPEG. One image
   (`image_01`, 1628×1366) exceeds the 1568px standard-tier long edge and would otherwise be
   silently downscaled. Total measured cost for all 16: **~24,155 image tokens**.
5. **Images before text** in the request content array.
6. **Two-call self-consistency** on all 16; discard on disagreement. Affordable at this volume.
7. **Never invent evidence when an image is absent** (`AGENTS.md:187`). All 16 PNGs are verified present.

---

## 7. Fixture / content-addressing key

```text
fixtures/<kind>/<key>.json

key = sha256(
        contract_version | provider | model | kind | subject_id | canonical_input
      )[:16]

canonical_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
kind            = "message" | "image"
```

- **`contract_version` is inside the key**, so any change to this contract or a prompt template makes
  every affected fixture a **miss** rather than a silent stale hit. This is the determinism trap that
  `vcrpy`'s default `match_on` (which omits the body) would have left open.
- `FixtureExtractor`: key → read file; **miss → hard fail, printing the missing key.**
- Live adapter: computes the same key and **writes the fixture as a byproduct**, so recording is free.
- Fixtures are **committed** to the repo. The shipped warm cache is what lets a grader reproduce
  `output.csv` with zero API calls and no key (**D26/D28**).

---

## 8. Deterministic validation rules

Applied by the engine to **every** fact before it may influence anything. A fact failing any rule is
**dropped**, not repaired, and a violation is recorded (never raised — Notification, per D22).

| # | Rule |
|---|---|
| V1 | `fact_type` ∈ the 25-value enum |
| V2 | `subject` resolves to a real `message_id` / `image_id` |
| V3 | `user_id` exists in `financial_profiles.csv`; `request_id`/`related_event_id`, when non-null, exist |
| V4 | `verbatim_quote` is a contiguous substring of the source text |
| V5 | **Digit-in-quote**: every digit of `amount` appears in `verbatim_quote` / `verbatim_amount_string` after separator normalisation |
| V6 | `amount`, when present, parses as `Decimal` and is **> 0** (no negative or zero amounts) |
| V7 | `currency` ∈ {INR, IDR, EUR, USD, ZAR} and matches the user's `home_currency` **or** has a dated rate available |
| V8 | `effective_date` is valid ISO `YYYY-MM-DD` and within `[request_date − 400d, request_date + 90d]` |
| V9 | **Authority check** (§4) — an inflow-increasing fact failing any of the five conditions is downgraded to CONFIRM-only |
| V10 | Field presence matches the per-type requirements in §3 |
| V11 | `image_amount` facts point at an event that genuinely has a **blank** `amount` |
| V12 | At most one fact per `(subject, fact_type)` pair; duplicates are collapsed deterministically by `fixture_key` |
| V13 | No field contains text that will be emitted into any output column |

---

## 9. The interface consumed by the deterministic engine

```python
# code/extraction/port.py — the ONLY boundary the engine sees.

class ExtractionPort(Protocol):
    def facts_for_user(self, user_id: str) -> tuple[Fact, ...]:
        """All validated facts for one user, deterministically ordered by
        (fact_type, subject). Returns () when no evidence exists — never None."""

    def image_amount(self, event_id: str) -> Fact | None:
        """The resolved amount for a blank-amount event, or None if unresolvable.
        None triggers deterministic imputation. NEVER returns zero."""
```

Guarantees the engine may rely on:

1. Every returned `Fact` has already passed **V1–V13**.
2. Ordering is deterministic and independent of dict/set iteration order.
3. No network access occurs during an engine run when the cache is warm.
4. The port never raises for missing evidence — absence is `()` / `None`.
5. Implementations are interchangeable: `FixtureExtractor` (default, offline),
   `GroqExtractor` (messages), `VisionExtractor` (images, development-time). **D29**: a future
   runtime vision provider plugs in here and never becomes a dependency.

**Engine-side obligations** (the other half of the contract):

- The engine never reads `messages.csv`, `images.csv`, or any PNG directly — only via this port.
- The engine applies the §4 authority matrix; the extractor does **not** enforce it.
- The engine, and only the engine, writes `output.csv`.

---

## 10. Verification record

| Claim | How verified |
|---|---|
| 25 types cover the corpus | Priority-ordered single-assignment classification of all 215 messages → **0 unclassified** |
| 198 of 250 evaluation users have a message | Join `messages.csv` × `requests.csv` |
| Rule-based parsing is insufficient | 215 messages → **198 distinct normalised shapes, 183 singletons** |
| Images map 1:1 onto blank amounts | 16 images, 16 blank-amount events, exact `related_event_id` match, all 16 PNGs present |
| Disputed duplicates must be reserved | All 6 duplicate rows carry a dispute message stating no reversal posted |
| `internal_transfer` is the real de-dup case | 6 messages describing matching debit/credit between own accounts |
| Only salary may increase inflows | `AGENTS.md:212-213`, `problem_statement.md:207` |
| Blank ≠ zero | `problem_statement.md:45`, `README.md:113` |
| FX has no date ambiguity | 0 foreign events where the settlement-date rate differs from the event-date rate; 0 blank settlement dates outside `unrealized` |
| **Authority condition 2 is satisfiable** | `salary_first` **27/27 from `employer`**; `one_time_arrears` **9/9 from `employer`**. The rule downgrades nothing legitimate. |
| **The scam traps cannot pass** | All **12** windfall/prize messages are `financial_service`; **zero** from `employer`. Condition 2 blocks them structurally, with no special-casing. |
| `salary_first` required fields are always present | **27/27** carry both an ISO date and an explicit currency amount |
| `one_time_arrears` emits two facts | **9/9** also state a regular salary figure in the same message |
| `salary_temporary` `applies_to_cycles = 1` | **10/10** say "next payroll" / "affected pay cycle" |
| Types must be keyed on consequence, not vocabulary | The word **"increase"** spans three unrelated types: 9 `employer` → `salary_increase`, **6 `service_provider` → `recurring_expense_increase`** ("renewed lease increases monthly rent by 12%"), 4 `financial_service` → `unrealized_valuation_notice` ("displayed market value has increased"). A keyword classifier conflates all three; only `source_type` + consequence separate them. |
| `percent_change` is genuinely needed | Rent increases are expressed as **"by 12%"**, not as an absolute amount |
| Amount-bearing sources are concentrated | `employer` 94/126 and `service_provider` 15/31 carry amounts; `bank` **0/18** and `merchant` **0/17** carry none. A future amount-bearing fact from `bank`/`merchant` is therefore suspect. |

## 11. Explicitly out of scope

Not in this contract, by decision: spotlighting delimiters, `llama-prompt-guard` calls,
`injection_suspected` self-report fields (the threat surface is fully known and contains no model-
directed injection — see `docs/investigation/message-corpus.md`); retrieval; any LLM involvement in
mathematics, simulation, ranking, validation, or explanation text.

**Unfrozen and deliberately absent from this contract** (calibration-dependent): recurrence
parameters, the variable-spend estimator, and same-day event/payment ordering. None of them belong to
the extraction boundary.

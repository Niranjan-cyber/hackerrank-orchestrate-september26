# Spec: Buy or Wait? — affordability decision engine

**Status:** ready-for-agent · **Contract:** `docs/contracts/extraction-fact-schema.md` v1.0.0
**Domain model:** `CONTEXT.md` · **Patterns:** `docs/architecture/reference-patterns.md`

> Published to the filesystem rather than the issue tracker: GitHub Issues are disabled on the fork
> (`has_issues: false`) and coordination is filesystem-only by decision (Q5a). The `ready-for-agent`
> label is recorded here in the header instead.

---

## Problem Statement

A user with a real financial life wants to know whether they can afford a specific purchase or
payment — *"Can I afford this laptop?"* — and the honest answer is almost never in their current
balance. Rent is due in four days, a pending card charge hasn't cleared, salary lands on the 15th,
their employer just emailed that the next payroll is reduced, and they have told us they never want
their balance to fall below a floor they chose.

Answering badly is costly in both directions. Say "yes" and they breach their floor in six weeks.
Say "no" and they miss a deadline they could have met by pausing one streaming subscription. Two
users with identical balances deserve different answers, because their commitments, priorities,
accepted payment methods, and willingness to cut flexible spending differ.

The user also cannot be expected to trust an answer they can't interrogate. "You can pay 603.30
today" is useless without "because rent of 178.20 lands on the 3rd and your floor is 800".

## Solution

For each of the 250 requests, reconstruct the user's cash position, project it forward 90 days, and
recommend the safest way to proceed — pay in full, pay part now and the rest later, use one of the
seller's installment options, wait for a specific date, or don't proceed — together with the largest
amount safe to pay today, the earliest date a full payment becomes safe, up to three flexible
spending changes that would unlock the request, and a grounded explanation.

A recommendation is **safe** only if every payment in it can be made, the request completes by
`desired_completion_date`, essential spending stays covered, and the projected balance never falls
below `minimum_balance_to_keep` at any point in the 90-day window.

Two properties are non-negotiable, and they are what make the solution trustworthy rather than merely
plausible:

1. **Deterministic Python decides everything.** A language model is used only to read facts out of
   untrusted messages and document images. It never computes, ranks, validates, or decides.
2. **Every number traces to an event.** Each derived value carries reason codes, so the explanation is
   rendered from engine state rather than narrated.

---

## User Stories

### The person asking the question

1. As a user with a chosen minimum balance, I want a recommendation that never takes me below it, so that I keep my safety cushion intact.
2. As a user with rent due next week, I want upcoming committed bills subtracted before I'm told what's safe to spend, so that the number I see is actually spendable.
3. As a user with a pending card charge that hasn't cleared, I want it treated as already gone, so that I don't spend money twice.
4. As a user expecting a refund that hasn't arrived, I want it excluded from what I can spend, so that I'm not relying on money I don't have.
5. As a user whose salary is confirmed for the 15th, I want it counted on the 15th and not before, so that my forecast matches reality.
6. As a user whose employer has confirmed a pay rise, I want the increase reflected from its effective date, so that my capacity isn't understated.
7. As a user whose employer has confirmed a pay cut, I want the reduction reflected immediately, so that I'm not told I can afford something I can't.
8. As a user starting a new job with no salary history, I want my confirmed first salary counted, so that I'm not treated as having no income at all.
9. As a user whose pay is temporarily reduced for one cycle, I want that treated as temporary, so that my whole quarter isn't forecast at the lower figure.
10. As a user receiving a one-off arrears payment, I want it counted once, so that it isn't mistaken for a permanent rise.
11. As a user whose seasonal contract has ended, I want future income stopped, so that I'm not told to spend against income that won't arrive.
12. As a user with an unconfirmed bonus, I want it excluded until it settles, so that a maybe isn't treated as a certainty.
13. As a user who received a "you've won a prize, pay a release fee" message, I want it ignored entirely, so that a scam can never influence my financial advice.
14. As a user whose lease renewal raises rent 12%, I want the higher rent projected from its effective date, so that my forecast isn't optimistic.
15. As a user who moved money between my own two accounts, I want that not counted as income or spending, so that my cash position isn't distorted.
16. As a user disputing a duplicate card charge that hasn't been reversed, I want the money treated as still gone, so that my advice is safe while the dispute is open.
17. As a user whose bill payment failed and will be retried, I want the retry reserved, so that the money is still there when it's attempted.
18. As a user with a bill whose amount only exists on a receipt, I want that amount read from the receipt, so that it isn't silently treated as zero.
19. As a user paid in a foreign currency, I want conversion at the correct dated rate, so that my home-currency position is right.
20. As a user holding investments that rose on paper, I want unrealised gains excluded from spendable cash, so that I'm not told to spend money I'd have to sell to get.
21. As a user who actually sold an investment and received the proceeds, I want that cash counted, so that my real position is reflected.
22. As a user who will only ever pay in full, I want never to be offered installments, so that advice respects how I choose to pay.
23. As a user who will consider installments up to 6 months, I want plans longer than that excluded, so that I'm not shown something I'd refuse.
24. As a user who accepts partial payment, I want to be offered paying part today and the rest on a date I can actually make, so that I can start now without risk.
25. As a user with a hard deadline, I want any recommended plan to finish on or before it, so that the recommendation is actually usable.
26. As a user choosing between safe plans, I want the cheapest one, so that I don't pay avoidable financing fees.
27. As a user for whom waiting two weeks avoids all fees, I want to be told to wait, so that I keep the money the fees would have cost.
28. As a user for whom nothing is safe, I want to be told not to proceed rather than given an unsafe plan, so that I can trust the advice.
29. As a user willing to pause a streaming subscription, I want that offered when it's what unlocks my request, so that I get a route to yes.
30. As a user who has protected groceries and rent, I want those never proposed for cutting, so that advice respects what I refuse to compromise.
31. As a user willing to reduce dining but not stop it, I want a reduction proposed rather than a cancellation, so that the suggestion is one I'd accept.
32. As a user asked to cut spending, I want the smallest change that works, so that I'm not over-asked.
33. As a user asked to reduce a commitment, I want it never reduced below its stated minimum, so that the suggestion is realistic.
34. As a user reading the recommendation, I want an explanation naming the actual amounts and dates behind it, so that I can check the reasoning.
35. As a user receiving advice, I want the same inputs to always produce the same advice, so that I can trust it isn't arbitrary.

### The evaluator

36. As an evaluator, I want exactly one output row per `request_id` in `requests.csv`, so that scoring can join cleanly.
37. As an evaluator, I want the eight required columns in the exact required order, so that parsing doesn't break.
38. As an evaluator, I want `0 <= amount_safe_to_pay <= requested_amount` on every row, so that the core invariant holds.
39. As an evaluator, I want only the permitted enum values in the status and method columns, so that no row is unscoreable.
40. As an evaluator, I want every installment plan to match a supplied payment option exactly, so that plans aren't invented.
41. As an evaluator, I want every partial-payment plan's two amounts to sum to `requested_amount`, so that the arithmetic is sound.
42. As an evaluator, I want `earliest_date_for_full_payment` to equal `request_date` whenever the status is `affordable_now`, so that the columns are mutually consistent.
43. As an evaluator, I want spending changes to reference only non-protected, permitted, correctly-flexible events, so that suggestions are valid.
44. As an evaluator, I want `evaluation/usage_report.md` to describe the run that actually produced `output.csv`, so that the reported usage is truthful.
45. As an evaluator, I want the solution runnable from a terminal against `dataset/`, so that I can reproduce the result.
46. As an evaluator, I want no organizer-only files or hardcoded labels used, so that the result reflects genuine inference.

### The developer and the two agents

47. As the developer, I want the engine fully developable and testable with no API key, so that the prototype is never blocked on credentials.
48. As the developer, I want a per-column scorecard against the 25 solved samples, so that I can see exactly which behaviour regressed.
49. As the developer, I want a day-by-day ledger for any request, so that I can see precisely where the balance breached the floor.
50. As the developer, I want calibration parameters in one injected config object, so that I can sweep them without touching engine code.
51. As the developer, I want model calls recorded to an append-only log as they happen, so that the usage report is measured rather than reconstructed.
52. As the developer, I want a logging failure never to fail a run, so that instrumentation can't cost me the submission.
53. As the developer, I want extraction cached by content hash, so that a second run costs nothing and can't drift.
54. As the developer, I want a changed prompt to invalidate its cache entry, so that I never silently replay a stale fact.
55. As the developer, I want a malformed request to yield a safe row plus a logged violation rather than aborting, so that one bad row can't cost me 249 good ones.
56. As opencode, I want the extraction contract frozen before I start, so that my work integrates without rework.
57. As Claude Code, I want file-level ownership boundaries, so that two terminals never collide.

---

## Implementation Decisions

### Seams and shape

- **The deterministic core seam** — genuinely pure, because it receives already-loaded values and
  performs no I/O:

  ```text
  run_pipeline(dataset, extraction_facts, config) -> tuple[OutputRow, ...]
  ```

  `dataset` is the parsed CSV data, `extraction_facts` the already-validated facts. No filesystem, no
  network, no clock. Every one of the 25 samples is exercised through it.

- **`ExtractionPort` is the single external/LLM I/O boundary**, frozen in the contract. Adapters:
  `FixtureExtractor` (default, offline), `GroqExtractor` (messages), `VisionExtractor` (images,
  development-time only).

- **The shell owns all I/O**, in this order:

  ```text
  load dataset → extraction / fixtures → run_pipeline(dataset, facts, config) → validate → output.csv
  ```

  `code/main.py` is the only place this sequence lives. *Acceptance test for this decision: if
  `main.py` contains an `if` statement about finance, the logic is in the wrong place.*

- Calibration parameters live in an injected `config` — **not** a seam, not module state, not globals.

- **No additional architectural seams** are to be introduced unless implementation demonstrates a
  concrete need. Two is the budget.

### Modules

| Module | Responsibility |
|---|---|
| `dataset` | Load the seven CSVs into frozen dataclasses; no logic |
| `fx` | Convert a foreign amount at the dated rate for its settlement date |
| `state` | Reconstruct cash position: classify events by status/direction, apply the seven `linked_event_id` lifecycle patterns, net internal transfers, reserve pending debits, exclude pending credits and unrealized valuations |
| `evidence` | Apply the authority matrix to validated facts; resolve conflicts by the spec's four-level precedence |
| `recurrence` | Detect streams and project them forward — **parameters unfrozen** |
| `simulate` | The 90-day ledger and the floor test |
| `plans` | Generate candidates: full, partial, each eligible installment option, wait, plus change-variants when required |
| `rank` | One `rank_key(plan) -> tuple`; lexicographic |
| `validate` | Enforce every output invariant; returns violations, never raises |
| `explain` | Render `decision_explanation` from reason codes |
| `report` | Aggregate the usage log into `evaluation/usage_report.md` |

### Financial semantics

Fully specified in `CONTEXT.md` §3–§11 and not restated here. The decisions that bind implementation:

- **Forecast window**: fixed 90 days anchored at `request_date`. The maximum deadline distance in the
  dataset is 86 days, so the window always covers the deadline.
- **`amount_safe_to_pay`** = `min over window(projected_balance) − minimum_balance_to_keep`, clamped
  to `[0, requested_amount]`, computed **before** any spending changes.
- **`earliest_date_for_full_payment`** = the first date in the window at which a single full payment
  passes the floor test **without** spending changes; empty if never. Independent of the user's
  method preferences.
- **Same-day ordering**: dataset debits → dataset credits → **plan payment last**.
  *Inferred, not specified* — implemented as a single named sort key so all three conventions can be
  swapped and scored. **Unfrozen.**
- **Arithmetic**: `Decimal` from strings, exact throughout, floor comparisons on exact values,
  quantized to 2dp **only at output**.
- **Status mapping**: `affordable_now` requires both `amount_safe_to_pay == requested_amount` **and**
  `full_payment` among accepted methods.

### Ranking and the proven pruning rule

`rank_key` returns a 7-tuple — the spec's six criteria plus `request_id` as a final tie-break for a
guaranteed total order. Total paid uses the **fee-inclusive** `total_payable_amount`. Ineligible
options are pruned **before** ranking.

**Spending-change variants are expanded if and only if no safe no-change plan completes the full
request by `desired_completion_date`.** Proof: a safe no-change plan completing by the deadline has
key `(0, 0, …)`; any change-requiring plan has `needs_changes = 1`, so it loses at level 1 or level 2.
The converse matters — this is why the rule is a biconditional and not a shortcut: samples `06`, `11`
and `21` all have `earliest_date_for_full_payment` **after** the deadline, so no no-change plan
completes, change-variants must be explored, and they win at level 1. Naive pruning loses all three.

`wait` is expected to beat installments whenever both are eligible, because it pays exactly
`requested_amount` while **all 515** installment options carry a non-zero financing fee. This is an
emergent property of the ranking, **never hardcoded**.

### Spending-change selection

Eligible only when `flexibility != fixed`, the operation matches the flexibility (`stop` requires
`stoppable`/`reducible_or_stoppable`; `reduce_to` requires `reducible`/`reducible_or_stoppable`), the
category is in the user's corresponding willing list, the category is **not** protected, and the event
belongs to a detected recurring stream. Cite the **most recent settled occurrence before
`request_date`**. `reduce_to` targets `minimum_allowed_amount` exactly. Select the **smallest
sufficient** set: fewest changes, then smallest total saving that still passes, then lowest
`event_id`. `stop:` entries precede `reduce_to:` in the output.

### Extraction and execution model

- The engine reads evidence **only** through `ExtractionPort`. It never touches `messages.csv`,
  `images.csv`, or a PNG directly.
- Extraction is a **checkpointed pipeline stage**, cached by content hash with the contract version in
  the key.
- **The final full-dataset run may be warm** and is the run that produces both `output.csv` and
  `usage_report.md`. `usage_report.md` reports only the calls actually made in that run, and documents
  cache hits separately.
- **No vision key is required at submission runtime.** Development-time vision → verified fixtures is
  the shipped default. A runtime vision provider, if one appears, plugs in behind the same port and
  never becomes a dependency.
- Blank amounts resolve via image extraction, then deterministic imputation (median of the user's
  settled same-category events, reason code `IMPUTED_BLANK_AMOUNT`). **Never zero.**

### Output mechanics

`csv` writer with `newline=""` and `lineterminator="\n"`; values written as `str(Decimal)`; `sorted()`
around every set iteration; `PYTHONHASHSEED=0` documented. `payment_plan` and `reduce_to` amounts take
two decimals when fractional and a bare integer otherwise; `amount_safe_to_pay` takes its natural
shortest representation. **The validator is the only writer to `output.csv`.**

### Build order — deliberately front-loads a working prototype and touches the API exactly once

| Phase | Output | API involvement |
|---|---|---|
| **A. Tracer bullet** | A valid 250-row `output.csv` end-to-end on `FixtureExtractor` with stubbed facts | **None** |
| **B. Populate fixtures** | Real committed fixtures: 215 messages via Groq, 16 images via development-time vision | **Once, offline, ~15h before the deadline** |
| **C. Calibration** | Tuned recurrence and variable-spend parameters, then **frozen** (3h timebox) | None — warm cache |
| **D. Final run** | `output.csv` + `evaluation/usage_report.md` together | None — warm cache |
| **E. Packaging** | `code.zip`, README, `log.txt` transcript | None |

Phase A yields a submittable artifact before any credential is needed. If Phase B fails entirely, the
deterministic imputation path still ships. Everything after Phase B is offline, so there is no
API exposure near the deadline.

### Ownership

| | Claude Code | opencode |
|---|---|---|
| Owns | `code/engine/**`, `code/main.py`, integration, calibration, review | `code/extraction/**`, `code/eval/**`, `tests/**`, `fixtures/**` |
| Also | — | The 16 development-time vision extractions |

Neither modifies the other's directories. opencode starts now that the contract is committed.
Findings are persisted to `docs/investigation/*.md`, never relayed through chat.

---

## Testing Decisions

**What makes a good test here:** it asserts on externally observable behaviour — an output row, a
projected balance, a chosen plan — never on whether a function was called or how a module is
structured. The fixture adapter is a **fake**, not a mock: assert on the engine's output, never on the
prompt it would have sent.

**Primary test — the golden harness.** `code/eval/harness.py` runs all 25 solved samples through the
**complete pipeline** and prints a **per-column scorecard** (matched/mismatched for each of the eight columns)
plus a per-request `request_id | field | expected | actual` table. `--update` regenerates the
self-golden file for the unlabelled 250, so accepting a deviation is a visible diff in a commit.
`decision_explanation` is compared structurally and **reported separately** — it never gates the build.
This is the primary accuracy metric and the calibration objective.

**The 25 sample cases must exercise the complete pipeline** — load → extraction → `run_pipeline` →
validate → CSV — not just the core. An end-to-end pass is the acceptance bar.

**Focused unit tests** (stdlib `unittest`; `pytest` is unavailable in this environment) are added
**only for high-risk mathematical and domain primitives that are hard to diagnose through an
end-to-end test** — these seven areas, and not as a general habit:

1. **Recurrence detection** — monthly, semi-monthly, irregular, and two-occurrence streams
2. **Lifecycle resolution** — each of the seven `linked_event_id` patterns, including the
   disputed-duplicate reservation and internal-transfer netting
3. **Same-day ordering** — under all three candidate conventions (the convention is unfrozen)
4. **90-day simulation** — window boundaries, month-end clamping (Jan 31 → Feb 28/29), leap years
5. **Spending-change pruning** — the biconditional, including the three samples where variants must
   be expanded, and the `reducible_or_stoppable` case where reducing beats stopping
6. **Ranking** — lexicographic ordering, fee-inclusive totals, pre-rank pruning, total-order tie-break
7. **Decimal / rounding behaviour** — exact comparison, output-only quantisation, both number formats

A failure in any of these is otherwise near-impossible to localise from a wrong CSV cell.

**Property tests**: `0 <= amount_safe_to_pay <= requested_amount`; partial plans always sum to
`requested_amount`; any plan certified safe never breaches the floor when re-simulated.

**Adversarial tests** (drawn from real rows, not invented): the `windfall_solicitation` message must
change no output; a `financial_service` message claiming income must be downgraded by the authority
matrix; a numeric fact whose digits are absent from its verbatim quote must be rejected; an
installment option exceeding `max_installment_months` must be pruned rather than ranked.

**Contract test**: a full run produces exactly 250 rows, the exact columns in order, valid enums
throughout, and a non-empty `evaluation/usage_report.md`.

---

## Out of Scope

Visualization of any kind; a retrieval layer; a database; MCP servers; spotlighting delimiters,
prompt-guard classifiers, and injection self-report fields; a standalone counterfactual engine
(`spending_changes_needed` already is the counterfactual); any third-party runtime dependency; asset
price prediction or securities recommendation; live banking, market-data, or exchange-rate calls;
voice input; a chat interface.

Deliberately **not frozen** by this spec, because evidence does not yet justify freezing them:
recurrence parameters, the variable-spend estimator, and same-day event/payment ordering. Each is
implemented behind the injected `config` and settled in Phase C against the 25 samples.

## Further Notes

The riskiest remaining work is Phase C calibration: the variable-spend estimator
(`max(median(last 3 monthly), mean(last 6 monthly))`) is research-derived, **not** validated against
samples, and an early crude approximation was off by up to an order of magnitude on some rows. It is
timeboxed to three hours and then frozen, because the five discrete columns are more tractable and
carry more of the score than the one continuous column.

The most load-bearing single discovery is that `problem_statement.md` names caching as an encouraged
optimisation, which is what makes a warm final run both compliant and truthful — and therefore what
removes API risk from the critical path entirely.

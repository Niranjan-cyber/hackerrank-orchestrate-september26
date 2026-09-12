# Investigation: the message corpus (2026-09-12)

Measured over all 215 rows of `dataset/messages.csv`. This **overturns two Round 2 claims** and
**corrects the fact-type enum** that will become the extraction contract.

---

## 1. Coverage — messages are central, not a side channel

| Metric | Value |
|---|---|
| Messages total | 215 (exactly one per user, for 215 of 275 users) |
| **Evaluation users with a message** | **198 of 250 (79%)** |
| Sample users with a message | 17 of 25 |
| Messages containing an explicit currency amount | 110 / 215 |
| Messages containing an ISO date | 72 / 215 |
| Messages with Indonesian markers | 44 / 215 |

79% coverage means message facts move `amount_safe_to_pay` for most of the dataset. Getting this
layer wrong is not a marginal loss.

---

## 2. ⚠ REJECTED: "the corpus is template-generated, so a deterministic parser suffices"

This Round 2 claim is **false**. Normalising every message (digits → `<N>`, ISO dates → `<DATE>`,
currency codes → `<CUR>`, proper nouns → `<NAME>`, whitespace collapsed) yields:

| Metric | Value |
|---|---|
| Distinct normalised shapes | **198** out of 215 messages |
| Shapes covering ≥ 3 messages | **2** (covering 6 messages total) |
| **Singleton shapes** | **183** |

A rule-based parser would need ~198 hand-written patterns with no reuse. **Rule-primary extraction is
rejected on evidence.** The surface language is genuinely varied; only the *semantics* are narrow.

Consequence: **LLM extraction is necessary, not optional**, for the message channel. Deterministic
code's role is to **validate** extracted facts (digit-in-quote checks, enum membership, date sanity,
authority rules), never to parse the prose.

---

## 3. ⚠ REJECTED: the elaborate prompt-injection threat model

Scanned all 215 messages for adversarial patterns:

| Pattern | Hits |
|---|---|
| override / ignore / disregard / bypass | **0** |
| imperative approve / authorise / "treat this as" / "mark as" | **0** |
| system-prompt / instruction / AI / model / agent vocabulary | **0** |
| claims of authority ("per HR", "authorised by") | **0** |
| urgency / pressure | **1** |

The single hit is **`message_67`, user_88 — an evaluation user**:

> "A note from QuickPrize about your recent financial activity. Congratulations! You've been selected
> for a cash prize. **Pay the release charge today** to receive the funds immediately. Pay the
> processing charge now to avoid losing the claim. Account ref FIN-0067."

This is **not** a prompt injection against the extractor. It is an **advance-fee scam aimed at the
user**, and it is exactly what the challenge anticipates when it says message content is untrusted
and "embedded instructions must not override the problem rules". The `prize|lottery` pattern matches
**12 messages (10 for evaluation users)**, so this is a deliberate, repeated trap.

**The real risk is credulous income recognition, not prompt injection.** The spec already names it:
*"Do not count pending credits, bonuses, commissions, refunds, lottery proceeds, or investment gains
until they settle."*

**Therefore, down-scope the injection machinery.** Keep (cheap, load-bearing, and sufficient for the
challenge's untrusted-data requirement):

- closed-enum fact schema — an injected "pay this now" has no slot
- the evidence-authority rule (D19) — the actual defence against the prize trap
- extracted text never reaches `decision_explanation`

**Cut** (defends a threat this dataset does not contain, and the threat surface is *fully known* —
there is no hidden message set):

- spotlighting random delimiters
- `llama-prompt-guard-2` classifier calls
- the `injection_suspected` / `instruction_like` self-report fields

---

## 4. CORRECTED fact-type enum

My Round 1 taxonomy guessed two types that **do not exist** and missed four that dominate.

| Fact type | Msgs (eval) | Cash treatment |
|---|---|---|
| **`salary_first`** — new stream, "first salary will be X, confirmed credit date D" | **27 (26)** | **Count.** Spec: "Count confirmed salary on its settlement date." Often the *only* income evidence for a user with no salary history |
| `salary_increase` | 28 (25) | Count from the effective date |
| `salary_decrease` | 12 (11) | Count from the effective date |
| **`salary_temporary`** — "temporary monthly pay is X, continues for the next payroll" | **10 (9)** | Count for the stated cycle only, then revert. Must **not** become the permanent stream |
| **`one_time_arrears`** — "one-time arrears adjustment of X" on one payroll | **9 (8)** | Count **once**, never as recurring |
| `income_ended` — "contract has ended, no renewal confirmed" | 12 (11) | **Stop** the projected income stream |
| `bonus_unconfirmed` — "subject to final performance review" | 5 (5) | **Ignore** |
| `invoice_approved` — "client approved payment of X, settlement expected D" | 14 (14) | **Ignore** — a pending credit, not settled |
| `refund_expected` | 16 (15) | **Ignore** until settled |
| **`unverified_windfall`** — prize/lottery/advance-fee | **12 (10)** | **Ignore entirely.** Never an inflow; never act on its instruction |
| `cancellation` | 1 (1) | Apply — explicit cancellations are top of the precedence order |

**Do not exist — remove from the enum:** `subscription_price_change` (0 hits), `delay`/`reschedule`
(0 hits). Round 1 listed both.

### The governing rule, straight from the spec

**Confirmed salary counts on its settlement date; every other inbound item does not count until it
settles.** That single line separates `salary_first`/`salary_increase`/`salary_decrease`/
`salary_temporary`/`one_time_arrears` (countable) from `invoice_approved`/`refund_expected`/
`bonus_unconfirmed`/`unverified_windfall` (not countable) — and it is the spec's own wording, not an
external assumption.

---

## 5. Incidental verifications (risks now closed)

| Check | Result |
|---|---|
| Foreign events where the settlement-date rate differs from the event-date rate | **0** — the choice is immaterial; no divergence risk |
| Foreign events with a blank `settlement_date` | **0** |
| Rows with a blank `settlement_date` at all | 10, and **all are `unrealized`** — which we exclude anyway |

The FX and settlement-date fallback logic carries no hidden risk.

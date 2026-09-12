# Competitive and regulatory insights

How real products and regulators actually do affordability assessment and cash-flow forecasting,
and what of it we borrow. This is the file that answers "why did you choose *that* number?" —
which is exactly what the hackathon interview will ask.

Verdict key: **ADOPT** · **CONSIDER** · **IGNORE**

---

## 1. Regulated methodology (FCA CONC 5.2A) — the defensible spine

The UK FCA's consumer-credit sourcebook is the single most useful reference found, because it
specifies an affordability test in almost exactly our shape — and it is a citable primary source.
Source for all of the below: <https://www.handbook.fca.org.uk/handbook/CONC/5/2A.html>

### The affordability predicate is a three-part test, not "balance stays positive"

- **CONC 5.2A.12R(5)** defines a *sustainable* repayment as one made **without further borrowing,
  while meeting other reasonable commitments and essential living expenses, and without significant
  adverse impact on the customer's financial situation.**
- **Maps to**: the 90-day floor predicate and the `decision_explanation` template.
- **Verdict: ADOPT.** We restate our pass/fail condition as *"after all essential commitments, the
  projected balance never falls below `minimum_balance_to_keep`"*. This is a one-line template
  change that makes the explanations methodologically grounded rather than arbitrary — and it maps
  cleanly onto the challenge's own definition of safety.

### Non-discretionary expenditure has a definition we can reuse

- **CONC 5.2A.18G(1)**: non-discretionary expenditure is payments for **priority debts,
  contractual or statutory obligations, and essential living expenses that are hard to reduce
  without affecting basic quality of life.**
- **Maps to**: the essential-vs-flexible classifier gating `spending_changes_needed`.
- **Verdict: ADOPT as the default for ambiguous categories.** Contractual or recurring obligations →
  never propose `stop:`. Only spend that is genuinely *reducible without affecting basic quality of
  life* is flexible. This costs zero new code because the dataset already carries
  `flexibility` and the protected/reducible/stoppable category lists — it simply gives us a
  principled default when those fields leave a case ambiguous, and a citable reason for it.

### Future income counts only on independent evidence — directly relevant to our message channel

- **CONC 5.2A.15R(5)**: a future income increase may be counted **only** where the firm
  "reasonably believes on the basis of appropriate evidence that the increase is likely to happen".
- **CONC 5.2A.16G(3)**: a customer's own statement of income, without independent evidence, is
  **"not generally sufficient"**.
- **Maps to**: income projection, and the trust tiering of LLM-extracted message facts.
- **Verdict: ADOPT** — and note an important nuance for our dataset. Every message in
  `messages.csv` comes from a **third party**: `employer` 126, `service_provider` 31, `bank` 18,
  `merchant` 17, `financial_service` 23. There is **no self-reported category at all.** So an
  employer payroll notice *is* independent evidence of the kind 5.2A.15R(5) contemplates and may
  amend the projected income stream; whereas an **unconfirmed** bonus or an invoice still "subject to
  review" remains excluded regardless of who sent it. This resolves the apparent tension between
  "use messages as evidence" and "do not invent unsupported income": **confirmed third-party
  amendment = evidence; anything conditional = not evidence.**

### Statistical estimates are explicitly permitted for essential spend

- **CONC 5.2A.19G(1)**: statistical estimates may be used for non-discretionary expenditure
  **unless there is reason to believe actual spend is significantly higher.**
- **CONC 5.2A.20R**: assessment depth should be proportionate to the case.
- **Maps to**: the variable-spend forecaster (groceries/transport/dining).
- **Verdict: ADOPT.** This licenses a statistical category estimate with a
  *"use the higher of estimate vs. observed"* rule — and it is the regulatory basis for the
  conservative-direction choice in §4 below.

### Installments must be tested payment-by-payment

- FCA **CP25/23** extends existing CONC 5.2A to BNPL rather than adding installment-specific tests.
- **Verdict: ADOPT the consequence**: because CONC 5.2A applies unchanged, an installment plan is
  affordable only if **every** scheduled debit passes the floor test individually — never just the
  first one, and never the plan "on average".

### UK Standard Financial Statement — the two-tier spend split

- The SFS splits spending into **fixed costs** (rent/mortgage — deliberately given *no* trigger
  figure because they vary too much) versus three **flexible** guideline categories with
  annually-updated trigger figures derived from ONS living-costs data.
- Source: <https://standard-financial-statement.maps.org.uk/en/use-the-sfs/spending-guidelines>
  (page returns 403 to automated fetch; taxonomy corroborated via
  <https://aib.gov.uk/systems/common-financial-tool/common-financial-statement-cfs>)
- **Verdict: ADOPT the two-tier split** — fixed bills are projected as an *exact amount on an exact
  date*; flexible categories are projected as a *statistical monthly total spread across the month*.
  **IGNORE the trigger figures themselves**: they are UK household values in GBP, wrong for a
  dataset denominated in INR/EUR/IDR/ZAR/USD.

---

## 2. Product practice — "safe to spend" is a solved product problem

### Monzo "Left to Spend" — the closest production analogue to `amount_safe_to_pay`

- **Left to Spend = current balance − upcoming scheduled payments − set budgets.** Committed
  spending is predicted by **carrying forward last month's amount** for each detected stream
  (£30 last month ⟹ £30 forecast).
- Sources: <https://monzo.com/help/budgeting-overdrafts-savings/what-is-left-to-spend>,
  <https://monzo.com/help/budgeting-overdrafts-savings/summary-left-over>
- **Verdict: ADOPT.** Our formula becomes the direct analogue:
  `safe = balance − floor − reserved_pending_debits − projected_commitments`.
  Crucially, **last-observed-amount** for a fixed recurring stream is both simpler and more
  defensible than averaging it — a subscription that changed price last month should be projected at
  the new price, not at a blend of old and new.

### Simple bank's original Safe-to-Spend — the ordering principle

- **Safe-to-Spend = balance − expenses − goals**: commitments are subtracted **before** the number
  is shown, not flagged afterwards.
- Source: <https://weeklybudgeting.com/knowledge-base/core-concepts/safe-to-spend/>
- **Verdict: ADOPT the ordering.** Never surface a safe amount that a known future bill would
  invalidate. This is precisely what the challenge's 90-day safety check demands.

### Forecast horizon — 90 days is production-normal

- NatWest's cash-flow forecaster projects **90 days** ahead. Monarch Core forecasts "a few months"
  from recurring bills; multi-year projection is a separate premium product.
- Sources: <https://www.natwest.com/business/enterprise/cashflow.html>,
  <https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch>
- **Verdict: no change needed.** The challenge's 90-day horizon sits squarely in production range.
  Settled — stop treating it as a risk.

### YNAB deliberately does not forecast

- YNAB allocates only money **already in accounts**; it refuses to project future income.
- Source: <https://www.ynab.com/guide/irregular-income>
- **Verdict: CONSIDER.** This is a genuinely strong argument that **`affordable_now` should rest on
  settled cash alone**, with projections confined to `earliest_date_for_full_payment`. It is a
  respectable alternative philosophy and worth raising in the next grilling round — though note our
  sample evidence shows `amount_safe_to_pay` is clearly *net of projected commitments*, so the
  projection cannot be removed from that column.

### BNPL provider practice — thin

- No provider publishes usable affordability criteria. The only concrete primary datapoint: CFPB
  reports at least one lender verifies checking-account funds via open banking, and at least one
  requires a **>25% down payment** from riskier applicants.
- Source: <https://files.consumerfinance.gov/f/documents/cfpb_BNPL_Report_2025_01.pdf>
- **Verdict: IGNORE** for build purposes. The regulatory read-through in §1 is the useful part.

---

## 3. Recurrence threshold — corroborated independently

Plaid's requirement of **~3 occurrences** before a stream is considered mature was confirmed a
second time in this pass (<https://plaid.com/blog/recurring-transactions/>).

**Verdict: ADOPT, with a conservative refinement.** Use 3 occurrences as the confirmation threshold;
a 2-occurrence candidate is included **only if including it worsens the forecast** (i.e. only if it
is an outflow). That asymmetry means low-confidence detection can never manufacture affordability —
it can only withhold it.

---

## 4. The variable-spend statistic — our biggest calibration question, answered honestly

**Finding: no primary source publishes a percentile-based variable-spend forecast.** Stated plainly
rather than padded. The concrete production numbers that do exist:

| Product | Practice | Source |
|---|---|---|
| Monarch | Seeds budgets from a **6-month monthly average** | <https://help.monarch.com/hc/en-us/articles/360048883631-Creating-Your-Budget-in-Monarch> |
| Monzo | **Carries forward last month's amount** per stream | Monzo help, above |
| General forecasting practice | **3-month moving average** | — |

**Adopted approach** — forecast each variable category as:

```text
max( median of the last 3 monthly totals ,  mean of the last 6 monthly totals )
```

Why this specific rule, in interview-ready form:

1. **It is not invented.** Mean-over-6-months and short-window-average are both documented
   production practice; we are combining two attested estimators, not inventing a statistic.
2. **Taking the `max` leans conservative in exactly the direction the regulator cares about.**
   CONC 5.2A.19G(1) permits statistical estimates *unless actual spend may be significantly higher* —
   so the burden is on never **under**-estimating essential spend. `max` discharges that burden.
3. **It is ~10 lines of code** and has no tunable magic number beyond the two window lengths.

**Explicitly rejected: p90 or any high percentile.** No source supports it, and it would push too
many genuinely affordable requests into `not_affordable` — the opposite of the scoring objective.

Note the deliberate asymmetry in the overall model, which is worth stating as a design principle:
**fixed streams are projected at their last observed amount** (Monzo), while **variable categories
are projected at a conservative statistical estimate** (SFS + CONC). Different estimators for
different kinds of spend, each justified by a source.

---

## 5. Net effect on the build

| Question | Answer from this pass | Confidence |
|---|---|---|
| Forecast horizon | 90 days — production-normal, settled | High |
| Fixed recurring amount | Last observed amount, not an average | High (Monzo) |
| Variable category amount | `max(median of last 3 monthly, mean of last 6 monthly)` | Medium — best available; no primary source for percentiles |
| Recurrence threshold | 3 occurrences; 2 only if it worsens the forecast | High (Plaid, corroborated twice) |
| Fixed vs variable handling | Two-tier: exact date/amount vs. monthly statistical spread | High (SFS) |
| Unconfirmed income | Excluded — conditional means not evidence | High (CONC 5.2A.15R/16G) |
| Third-party message amendments | Admissible as evidence; all our messages are third-party | High (CONC + dataset `source_type`) |
| Installment testing | Every scheduled debit tested individually against the floor | High (CONC via CP25/23) |
| `amount_safe_to_pay` shape | `balance − floor − reserved pending debits − projected commitments` | High (Monzo/Simple, matches sample arithmetic) |

**One open philosophical question carried to the next grilling round**: YNAB's refusal to forecast
raises whether `affordable_now` should depend on settled cash only. Our sample evidence constrains
`amount_safe_to_pay` to be net of projections, but the *status* assignment is a separate choice.

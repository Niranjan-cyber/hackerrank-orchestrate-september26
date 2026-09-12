# Reference patterns

Established software patterns adopted for this engine, each with its canonical source and the
concrete component it applies to. The purpose is twofold: make the system testable without an API
key, and make it reproducible byte-for-byte.

Verdict key: **ADOPT** · **CONSIDER** · **IGNORE**

---

## 1. Structural patterns

### Ports & Adapters (Hexagonal) — Alistair Cockburn

- **Prescription**: *"Allow an application to equally be driven by users, programs, automated test or
  batch scripts, and to be developed and tested in isolation from its eventual run-time devices and
  databases."* A **port** is a *purposeful conversation* — a semantic interface, not a technology —
  and each port has N adapters. All I/O lives in adapters at the periphery. Cockburn explicitly
  prescribes **test adapters** (in-memory fakes, harnesses replacing the human).
- Source: <https://alistair.cockburn.us/hexagonal-architecture/>
- **Applies to**: `ExtractionPort` with two adapters — `AnthropicAdapter` and `FixtureExtractor`.
  The same pattern justifies a `DatasetPort` for CSV loading, so the simulator never touches the
  filesystem.
- **Verdict: ADOPT.** This *is* the "develop without the API key" requirement (decision Q2b),
  expressed as an established pattern rather than an improvisation.

### Functional Core, Imperative Shell — Gary Bernhardt

- **Prescription**: the core manipulates **values only** and is pure; a thin imperative shell does
  stdin/stdout/DB/network *"based on values produced by the functional core."* Stated payoff:
  *"testing the functional pieces is very easy and often naturally allows isolated testing with no
  test doubles"*, and the shell ends up with **few conditionals**.
- Sources: <https://www.destroyallsoftware.com/screencasts/catalog/functional-core-imperative-shell>,
  <https://www.destroyallsoftware.com/talks/boundaries>
- **Applies to**: the whole chain — state reconstruction → recurrence detection → 90-day simulator →
  plan generation → ranker → validator — as **pure functions over frozen dataclasses**. The shell is
  `code/main.py` only: read CSVs, call the extraction adapter, call the core, write `output.csv`.
- **The test for whether we got it right**: if `main.py` contains an `if` statement *about finance*,
  the logic is in the wrong place.
- **Verdict: ADOPT.** Zero cost, and it is what makes the 25-sample harness runnable with no network.

### Fake Object, not Mock — Meszaros, via Fowler's *Mocks Aren't Stubs*

- **Prescription**: *"Fake objects actually have working implementations, but usually take some
  shortcut which makes them not suitable for production."* Only **mocks** mandate behaviour
  verification; everything else uses **state verification**.
- Source: <https://martinfowler.com/articles/mocksArentStubs.html>
- **Applies to**: name it `FixtureExtractor`, and assert on the **engine's output**, never on
  "was the LLM called with prompt X".
- **Verdict: ADOPT** (naming and testing discipline; no implementation cost).

---

## 2. Recorded-fixture / replay testing

### Content-addressed fixture directory — **chosen approach**

- **Shape**:
  `fixtures/extraction/<sha256(prompt_template_version + model_id + canonical_input_json)[:16]>.json`
  with the input canonicalised as `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
- `FixtureExtractor`: hash → read file; **miss → hard fail printing the missing key** (the equivalent
  of vcrpy's `record_mode="none"`). `AnthropicAdapter` computes the *same* key and writes the file on
  a real call, so **recording is a byproduct of the first real run**.
- **The determinism trap it closes**: because the **prompt template version is inside the hash**, a
  changed prompt cannot silently reuse a stale fixture — it becomes a miss instead.
- **Verdict: ADOPT.** ~20 lines, stdlib only, and one code path serves both the fixture world today
  and the live world later. Hand-authored fixtures for adversarial test messages live in the same
  directory under a pinned key.

### vcrpy cassettes — rejected

- Record modes `once` / `none` / `all` / `new_episodes`; default
  `match_on = ['method','scheme','host','port','path','query']` — **`body` is not matched by
  default**, which is exactly the stale-replay trap (same endpoint, changed prompt, silent replay).
  Fixable with `match_on=['method','path','body']` plus `filter_headers` to scrub the key.
- Sources: <https://vcrpy.readthedocs.io/en/latest/usage.html>,
  <https://vcrpy.readthedocs.io/en/latest/configuration.html>
- **Verdict: IGNORE.** It adds a dependency, stores YAML cassettes, and intercepts at the HTTP layer
  **below** the `anthropic` SDK — so we would be recording SSE/JSON envelopes instead of our own
  parsed extraction result. Not decisive for our shape.

---

## 3. Golden master / approval testing

### Characterization testing — Michael Feathers

- **Prescription**: write the test with a dummy expectation, run it, read the actual value from the
  failure, paste it in as expected, rename the test. *"The purpose of characterization testing is to
  document your system's actual behavior, not check for the behavior you wish your system had."*
- Source: <https://michaelfeathers.silvrback.com/characterization-testing>

### Golden files with an `--update` flag — Go `testdata` convention

- **Prescription**: expected output lives in a checked-in file; the test diffs actual against
  golden; an explicit `--update` flag regenerates it — so **accepting a deviation shows up as a
  visible diff in the commit**. This is the standard "accepted deviation" mechanism.
- Source: <https://eli.thegreenplace.net/2022/file-driven-testing-in-go/>
- **Applies to**: `evaluation/harness.py`.
  - Default run: prints a **per-column scorecard** (rows matched/mismatched for each of the 8 output
    fields) plus a per-request table of `request_id | field | expected | actual`.
  - `--update`: regenerates `evaluation/golden_output.csv`.
  - `sample_requests.csv` is the **real** golden file (25 labelled rows). A separate self-generated
    golden over the full `requests.csv` catches regressions where no labels exist.
  - `decision_explanation` is free text: compare it **structurally** or exclude it from the pass/fail
    gate and report it separately. It must never fail the build on wording.
- **Verdict: ADOPT** the golden-file + `--update` + per-column diff.
  **IGNORE the `approvaltests` library** — a dependency plus diff-tool/reporter machinery we do not
  need; stdlib `difflib` + `csv` suffices.

---

## 4. Determinism engineering in Python

All four items below are cheap and load-bearing. Sources are the Python docs.

### Money

- The docs state `decimal` *"is preferred in accounting applications which have strict equality
  invariants"*, and `quantize()` *"is useful for monetary applications that often round results to a
  fixed number of places."* Default context is `prec=28, rounding=ROUND_HALF_EVEN`.
- **Rules adopted**: set the context **once, in the shell**; **construct from `Decimal(str(x))`,
  never `Decimal(float)`**.
- ⚠ **SUPERSEDED — see `CONTEXT.md` D5.** This section previously prescribed *directional* rounding
  (`ROUND_DOWN` for `amount_safe_to_pay`, `ROUND_UP` for projected expenses). **That was wrong**, and
  the cross-verification pass reversed it: directional rounding buys **no** safety once floor
  comparisons run on exact values, while it can miss a graded ground-truth value by a cent.
- **The rule now in force**: exact `Decimal` arithmetic with **no intermediate rounding**; floor
  comparisons on exact values; quantize to 2dp **only at output**, `ROUND_HALF_UP`. Rounding is a
  presentation concern, not a safety one.
- Source: <https://docs.python.org/3/library/decimal.html>

### Stable sorting and total order

- Docs: *"Sorts are guaranteed to be stable… when multiple records have the same key, their original
  order is preserved"*, and tuples compare lexicographically.
- **Stability is not sufficient here**, because input order derives from CSV/dict order. Therefore
  **append `request_id` / `payment_option_id` as a final tie-break element**, turning the documented
  6-criteria rank tuple into a **7-tuple with a guaranteed total order**.
- Source: <https://docs.python.org/3/howto/sorting.html>

### Set and dict iteration

- dicts preserve insertion order; **sets do not**, and set ordering is hash-seed dependent.
- **Rule**: never iterate a set to build output — wrap it in `sorted()`. Document
  `PYTHONHASHSEED=0` in the README as belt-and-braces.
- Source: <https://docs.python.org/3/using/cmdline.html>

### Byte-stable CSV output — the single highest-value line, because we are on Windows

- Open with `newline=""` — otherwise, per the docs, *"on platforms that use `\r\n` line endings on
  write an extra `\r` will be added."* The default dialect `lineterminator` is `"\r\n"`, so pass
  **`lineterminator="\n"`** explicitly for identical bytes on Windows and Linux.
- Write `str(decimal)` — **never a float** — so there is no float-repr variance between platforms.
- Source: <https://docs.python.org/3/library/csv.html>

### Randomness

- No `random` anywhere in the engine. If the live adapter is used, pin `temperature=0` and treat the
  response as **untrusted input**, never as a source of numbers.

---

## 5. Decision tables and ranking

### Decision Table — Martin Fowler (*DSLs*, ch. 48)

- **Prescription**: *"Represents a combination of conditional statements in a tabular form… improves
  understandability by representing the group of conditions as a table"*, each column giving the
  outcome for one combination. Use it where code *"composes several conditional statements"* that are
  hard to follow.
- Source: <https://martinfowler.com/dslCatalog/decisionTable.html>
- **Applies to**: the payment-option **eligibility matrix**, as a module-level tuple of rows —
  `(option_type, predicate, rejection_reason)` — evaluated in order, where each predicate is a named
  pure function: `respects_max_installment_months`, `matches_payment_preference`,
  `allows_partial_payment`, `completes_by_deadline`. **The rejection reason then feeds
  `decision_explanation` for free.**
- The **ranker** stays plain tuple-key sorting: a single `rank_key(plan) -> tuple` whose returned
  tuple *is* the documented ordering, so the six criteria are readable in one place.
- **Verdict: ADOPT** (predicate table + single `rank_key`). **IGNORE** any rules engine.

---

## 6. Audit trail and provenance

### Notification — Martin Fowler

- **Prescription**: *"If a failure is expected behavior, then you shouldn't be using exceptions."*
  A Notification accumulates a list of errors (message + optional cause) so you can report *"all
  errors with the incoming data, not just the first"*, instead of the user *"playing a game of
  whack-a-mole."*
- Source: <https://martinfowler.com/articles/replaceThrowWithNotification.html>
- **Applies to** two things:
  1. **The validator returns a violations list rather than raising.** A malformed request then
     yields a safe conservative row plus a logged violation, instead of killing the batch on
     request 300 of 500. This is a submission-integrity property, not a nicety.
  2. **Reason codes as the provenance carrier.** Every core function that derives a number appends a
     `Reason(code, event_id, amount)` to the plan's `reasons` tuple — e.g.
     `RESERVED_PENDING_DEBIT/event_412`, `EXCLUDED_UNREALIZED_GAIN/event_88`,
     `FLOOR_BREACH_ON/2026-10-03`, `PRUNED_OVER_MAX_INSTALLMENTS/payment_option_49`.
- **This closes the loop on the templated-explanation decision**: `decision_explanation` is
  *rendered from reason codes*, not hand-written per case — which is exactly how it stays consistent
  across 250 rows (a graded property) while remaining grounded in engine facts. It also gives us
  `evaluation/trace.jsonl` (one JSON object per request) for debugging at no extra cost.
- **Verdict: ADOPT.** **IGNORE** W3C PROV and decision-provenance graph literature — enterprise
  scale, nothing usable inside a hackathon.

---

## 7. The 30-minute action list

Concrete, ordered, and cheap. These are the items to apply the moment implementation starts:

1. CSV writer: `newline=""` + `lineterminator="\n"` + `str(Decimal)`.
2. Append `request_id` as the final tie-break element of the rank tuple (6 → 7-tuple).
3. `sorted()` around every set iteration; document `PYTHONHASHSEED=0`.
4. `ExtractionPort` + `FixtureExtractor` keyed by
   `sha256(template_version | model_id | canonical_input)`, hard-failing on a miss.
5. `evaluation/harness.py --update` writing `evaluation/golden_output.csv`; default run prints the
   per-column scorecard.
6. Validator returns a violations list instead of raising; plans carry a `reasons` tuple.
7. Decimal context set once in the shell; exact arithmetic throughout; quantize **only at output**
   with `ROUND_HALF_UP` (see the superseding note in §4 — directional rounding was reversed).

# 13: Focused unit tests for the seven high-risk primitives

**Status: COMPLETE (2026-09-13).** Implemented as an independent, OpenCode-owned suite in
`tests/**`, importing the engine across the port boundary (allowed; the engine files are untouched).
75 tests across seven files plus `tests/primitive_fixtures.py`:

| File | Covers |
|---|---|
| `test_engine_recurrence_primitives.py` | monthly, semi-monthly, within-tolerance wobble, irregular, two-occurrence |
| `test_engine_lifecycle_primitives.py` | all seven linked patterns, disputed duplicate, transfer netting |
| `test_engine_simulation_primitives.py` | all three same-day conventions, window boundaries, month-end/leap |
| `test_engine_spending_primitives.py` | the biconditional, samples 06/11/21, reduce-beats-stop |
| `test_engine_ranking_primitives.py` | all six levels, fee-inclusive total, pre-rank pruning, total order |
| `test_engine_money_primitives.py` | exact comparison, output-only quantisation, both formats |
| `test_engine_adversarial_primitives.py` | windfall inert, non-employer downgrade, digit mismatch, over-length option |

The adversarial tests assert on observable output (published row, returned fact, violation,
pruned option); none asserts on whether a call was made.

**What to build:** The seven areas that are hard to diagnose from a wrong CSV cell get direct tests,
so a regression points at its cause instead of a symptom. Deliberately narrow: the 25 samples already
cover behaviour end to end, and no further tests are added as a habit.

stdlib `unittest` - `pytest` is unavailable in this environment and the submission stays
zero-dependency.

**Blocked by:** 08.

**Owner:** OpenCode - `tests/**`

**Status:** done

- [x] Recurrence detection: monthly, semi-monthly, irregular, and two-occurrence streams
- [x] Lifecycle resolution: all seven linked-event patterns, the disputed-duplicate reservation, and internal-transfer netting
- [x] Same-day ordering: all three candidate conventions
- [x] 90-day simulation: window boundaries, month-end clamping, leap years
- [x] Spending-change pruning: the biconditional, the three samples needing expansion, and the reduce-beats-stop case
- [x] Ranking: lexicographic order, fee-inclusive totals, pre-rank pruning, total-order tie-break
- [x] Decimal and rounding: exact comparison, output-only quantisation, both number formats
- [x] Adversarial: a windfall solicitation changes no output; a non-employer income claim is downgraded; a digit mismatch is rejected; an over-length installment option is pruned
- [x] Tests assert on observable output, never on whether a call was made

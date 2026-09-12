# 13: Focused unit tests for the seven high-risk primitives

**What to build:** The seven areas that are hard to diagnose from a wrong CSV cell get direct tests,
so a regression points at its cause instead of a symptom. Deliberately narrow: the 25 samples already
cover behaviour end to end, and no further tests are added as a habit.

stdlib `unittest` - `pytest` is unavailable in this environment and the submission stays
zero-dependency.

**Blocked by:** 08.

**Owner:** OpenCode - `tests/**`

**Status:** ready-for-agent

- [ ] Recurrence detection: monthly, semi-monthly, irregular, and two-occurrence streams
- [ ] Lifecycle resolution: all seven linked-event patterns, the disputed-duplicate reservation, and internal-transfer netting
- [ ] Same-day ordering: all three candidate conventions
- [ ] 90-day simulation: window boundaries, month-end clamping, leap years
- [ ] Spending-change pruning: the biconditional, the three samples needing expansion, and the reduce-beats-stop case
- [ ] Ranking: lexicographic order, fee-inclusive totals, pre-rank pruning, total-order tie-break
- [ ] Decimal and rounding: exact comparison, output-only quantisation, both number formats
- [ ] Adversarial: a windfall solicitation changes no output; a non-employer income claim is downgraded; a digit mismatch is rejected; an over-length installment option is pruned
- [ ] Tests assert on observable output, never on whether a call was made

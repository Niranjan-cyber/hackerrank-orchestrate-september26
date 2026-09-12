# 02: Sample scorecard harness

**What to build:** A developer can run one command and see, per output column, how many of the 25
solved sample requests match and which ones differ - so every later change is measurable rather than
guessed at. This is the measurement instrument the rest of the build depends on, which is why it
lands immediately after the tracer bullet.

The 25 samples must be driven through the **complete pipeline** (load -> extraction -> core ->
validate -> CSV), not just the core.

**Blocked by:** 01.

**Owner:** OpenCode - `code/eval/**`

**Status:** ready-for-agent

- [ ] Default run prints a per-column scorecard: matched and mismatched counts for each of the 8 columns
- [ ] Default run prints a per-request table of request_id, field, expected, actual for every mismatch
- [ ] `--update` regenerates the self-golden file for the unlabelled 250 so an accepted deviation appears as a commit diff
- [ ] `decision_explanation` is compared structurally and reported separately; it never gates pass or fail
- [ ] Exit status is non-zero when any non-explanation column regresses
- [ ] Uses `sample_requests.csv` as the golden source and never writes to `dataset/`

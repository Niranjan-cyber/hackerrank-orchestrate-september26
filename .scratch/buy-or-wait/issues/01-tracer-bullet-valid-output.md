# 01: Tracer bullet - a valid 250-row output.csv end to end

**What to build:** Running the solution from the terminal produces a root `output.csv` with one row
for every `request_id` in `dataset/requests.csv`, in the exact required column order, that passes
every output invariant. The decision logic is deliberately naive at this stage (today's headroom
only, no projection, no evidence) - the point is that the whole path exists and the contract holds.

This establishes the two approved seams: the pure core
`run_pipeline(dataset, extraction_facts, config) -> tuple[OutputRow, ...]`, and a shell that owns all
I/O in the order `load dataset -> extraction/fixtures -> run_pipeline -> validate -> output.csv`.
The shell passes an **empty** fact tuple, so this ticket needs no extraction code and no API.

The `Fact` and `Dataset` value types live with the core that consumes them; the `ExtractionPort`
protocol and its adapters belong to ticket 03. This is what removes the bootstrap deadlock between
the two agents.

**Blocked by:** None (can start immediately).

**Owner:** Claude - `code/engine/**`, `code/main.py`

**Status:** ready-for-agent

- [ ] `python code/main.py` writes root `output.csv` with 250 data rows plus the header
- [ ] Columns exactly: request_id, amount_safe_to_pay, affordability_status, recommended_payment_method, payment_plan, earliest_date_for_full_payment, spending_changes_needed, decision_explanation
- [ ] `0 <= amount_safe_to_pay <= requested_amount` on every row
- [ ] Only permitted enum values appear in the status and method columns
- [ ] `affordable_now` rows have `earliest_date_for_full_payment == request_date`
- [ ] `not_recommended` rows have plan `none`, changes `none`, and an empty earliest date
- [ ] Writer uses `newline=""`, `lineterminator="\n"`, and `str(Decimal)`; two consecutive runs are byte-identical
- [ ] Money is `Decimal` built from strings throughout; no floats in the ledger
- [ ] The validator is the only writer to `output.csv`, and it returns a violations list rather than raising
- [ ] `run_pipeline` performs no filesystem, network, or clock access
- [ ] `code/main.py` contains no `if` statement about finance

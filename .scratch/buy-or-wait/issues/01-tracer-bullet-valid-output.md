# 01: Tracer bullet - a valid 250-row output.csv end to end

> **DONE 2026-09-12.** `python code/main.py` -> 250 rows, validation clean.
> Independently verified outside the validator: exact columns in dataset order, bounds hold on
> every row, all enums valid, affordable_now rows carry earliest == request_date,
> not_recommended rows carry none/none/empty, plan amounts sum to requested_amount,
> zero CR bytes in the file, and two consecutive runs are byte-identical.
> Outcome split under the deliberately naive rule: 87 affordable_now, 163 not_affordable.
> Built: `code/engine/{types,money,loaders,pipeline,validate}.py`, `code/main.py`.

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

- [x] `python code/main.py` writes root `output.csv` with 250 data rows plus the header
- [x] Columns exactly: request_id, amount_safe_to_pay, affordability_status, recommended_payment_method, payment_plan, earliest_date_for_full_payment, spending_changes_needed, decision_explanation
- [x] `0 <= amount_safe_to_pay <= requested_amount` on every row
- [x] Only permitted enum values appear in the status and method columns
- [x] `affordable_now` rows have `earliest_date_for_full_payment == request_date`
- [x] `not_recommended` rows have plan `none`, changes `none`, and an empty earliest date
- [x] Writer uses `newline=""`, `lineterminator="\n"`, and `str(Decimal)`; two consecutive runs are byte-identical
- [x] Money is `Decimal` built from strings throughout; no floats in the ledger
- [x] The validator is the only writer to `output.csv`, and it returns a violations list rather than raising
- [x] `run_pipeline` performs no filesystem, network, or clock access
- [x] `code/main.py` contains no `if` statement about finance

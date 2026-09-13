# 15: Final full-dataset run, usage report, and submission package

**What to build:** The submission: a final full-dataset run producing `output.csv` together with
`evaluation/usage_report.md`, packaged with everything a grader needs to reproduce it.

The final run **may be warm and cache-backed** - no cold run is required. The usage report truthfully
states only the model calls actually made during that run, and documents fixture and cache hits
separately.

**Blocked by:** 09, 13, 14.

**Owner:** Claude - integration, packaging, final review

**Status: COMPLETE (2026-09-13).** Final run is fixture-backed: `code/main.py` makes **zero live
model calls** (all 231 extraction facts - 215 message, 16 image - are served from `fixtures/`),
which the ticket explicitly permits. `code/evaluation/report.py` is new: it generates
`evaluation/usage_report.md` from `evaluation/usage_raw.jsonl` rather than hand-writing it, so the
numbers stay measured. The report states the 0-live-call fact for this run up front, then documents
the historical cost of building the message-extraction cache (215 calls, $0.1047, per-model
breakdown) and the image-extraction provenance (opencode/deepseek-v4.1-flash, offline, tokens not
tracked - honestly reported as such rather than estimated) as a separate, clearly-labelled section.
`code.zip` built via a one-off packaging script (not committed - reproducible from tracked sources)
containing `code/` (incl. `evaluation/usage_report.md`), the two fixture directories plus
`image_readings.json`, and `README.md`; gitignored like `log.txt` since it is a derived artifact.
README gained a "Running This Submission" section with the run command, the `PYTHONHASHSEED=0` note,
and the offline/cache-backed statement.

- [x] `output.csv` has 250 data rows plus header, exact columns in order, all enums valid, all bounds satisfied - `validation : clean` on the full run
- [x] Every installment plan matches a supplied option; every partial plan sums to the requested amount - enforced by the ticket 07/08 engine and the ticket-02/14 golden-sample suite, unchanged here
- [x] Every spending change targets a permitted, non-protected, correctly-flexible recurring event - same, unchanged by this ticket
- [x] `evaluation/usage_report.md` reports providers, models, call counts, input and output tokens, totals and per-request averages, and estimated total and per-request cost - section 3.1
- [x] The report distinguishes live calls from cache hits and corresponds to the run that produced `output.csv` - sections 1 and 2
- [x] No API key, credential, or prompt content appears anywhere in the package - `code.zip` scanned for key-shaped strings, none found; `log.txt` scanned, none found
- [x] `code.zip` contains the runnable solution, README with setup and run instructions, and the `evaluation/` folder - verified via zip listing (287 files)
- [x] README documents `python3 code/main.py` for graders, notes `PYTHONHASHSEED=0`, and states that the shipped cache lets the run reproduce offline
- [x] `log.txt` is ready as the chat transcript and contains no secrets - scanned, clean
- [x] A second run reproduces `output.csv` byte-for-byte - verified twice, with and without `PYTHONHASHSEED=0`

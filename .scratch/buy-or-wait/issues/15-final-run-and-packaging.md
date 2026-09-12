# 15: Final full-dataset run, usage report, and submission package

**What to build:** The submission: a final full-dataset run producing `output.csv` together with
`evaluation/usage_report.md`, packaged with everything a grader needs to reproduce it.

The final run **may be warm and cache-backed** - no cold run is required. The usage report truthfully
states only the model calls actually made during that run, and documents fixture and cache hits
separately.

**Blocked by:** 09, 13, 14.

**Owner:** Claude - integration, packaging, final review

**Status:** ready-for-agent

- [ ] `output.csv` has 250 data rows plus header, exact columns in order, all enums valid, all bounds satisfied
- [ ] Every installment plan matches a supplied option; every partial plan sums to the requested amount
- [ ] Every spending change targets a permitted, non-protected, correctly-flexible recurring event
- [ ] `evaluation/usage_report.md` reports providers, models, call counts, input and output tokens, totals and per-request averages, and estimated total and per-request cost
- [ ] The report distinguishes live calls from cache hits and corresponds to the run that produced `output.csv`
- [ ] No API key, credential, or prompt content appears anywhere in the package
- [ ] `code.zip` contains the runnable solution, README with setup and run instructions, and the `evaluation/` folder
- [ ] README documents `python3 code/main.py` for graders, notes `PYTHONHASHSEED=0`, and states that the shipped cache lets the run reproduce offline
- [ ] `log.txt` is ready as the chat transcript and contains no secrets
- [ ] A second run reproduces `output.csv` byte-for-byte

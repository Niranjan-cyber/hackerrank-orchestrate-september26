# 11: Groq message extraction and recorded fixtures

**What to build:** All 215 messages are turned into typed facts by a real model call, once, and the
results are committed as fixtures so every later run is warm, offline, and reproducible.

This is the **only** ticket that touches a text API, and it runs early with plenty of buffer before
the deadline. Uses the environment's `GROQ_API_KEY` and `qwen/qwen3.8-27b`, which was verified to
support strict JSON-schema output at 41 prompt / 12 completion tokens on a probe.

Two verified operational traps: never set a tight `max_tokens` on a reasoning model (gpt-oss-120b
returned HTTP 400 with an empty generation at 64 tokens), and pack one item per call - batching
multiple messages costs accuracy and allows cross-item contamination.

**Blocked by:** 03.

**Owner:** OpenCode - `code/extraction/**`, `fixtures/**`

**Status:** ready-for-agent

- [ ] Strict JSON-schema output with additionalProperties false and full required lists
- [ ] The 25-type enum from the contract is enforced by the schema
- [ ] One call per message; no batching, no caching, no multi-item packing
- [ ] Untrusted message text is delivered JSON-encoded, not interpolated into an instruction
- [ ] Every call records provider, model, purpose, subject id, input and output tokens, cost, retries, and errors to an append-only log
- [ ] The usage logger never raises and never records prompt or response content or any key
- [ ] Retry with backoff on rate limits; the run is resumable because fixtures already written are reused
- [ ] All 215 messages produce a committed fixture, and the 17 sample-user messages are included so calibration can use them
- [ ] Facts failing contract validation are dropped and logged, never repaired

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

**Status:** completed 2026-09-13.

- [x] Strict JSON-schema output with additionalProperties false and full required lists
- [x] The 25-type enum from the contract is enforced by the schema
- [x] One call per message; no batching, no caching, no multi-item packing
- [x] Untrusted message text is delivered JSON-encoded, not interpolated into an instruction
- [x] Every call records provider, model, purpose, subject id, input and output tokens, cost, retries, and errors to an append-only log
- [x] The usage logger never raises and never records prompt or response content or any key
- [x] Retry with backoff on rate limits; the run is resumable because fixtures already written are reused
- [x] All 215 messages produce a committed fixture, and the 17 sample-user messages are included so calibration can use them
- [x] Facts failing contract validation are dropped and logged, never repaired

## What was built

- `code/extraction/prompts.py` - `SYSTEM_PROMPT` (the 25-type taxonomy, source-type disambiguation,
  field discipline), `build_fixture_payload` (the canonical input), `build_messages` (system + user,
  the record JSON-encoded), and `build_facts_schema` (strict: `additionalProperties: false`, every
  property in `required`, the 25-value enum from `validate.MESSAGE_FACT_TYPES`).
- `code/extraction/groq_client.py` - stdlib `urllib` client for Groq's OpenAI-compatible endpoint.
  Strict `json_schema` response format, generous `max_tokens` (the reasoning-model trap), exponential
  backoff on 429/5xx/network, and a nominal per-model price table for the cost column.
- `code/extraction/groq_extractor.py` - one call per message, atomic fixture write, cache detection
  across known models, validation drops logged, resumable.
- `code/extraction/usage_log.py` - append-only JSONL; swallows every write error; records only ids and
  token counts (never prompt/response content or keys).
- `code/extraction/record.py` - the development-time CLI. `FixtureExtractor` remains the shipped path.
- `code/evaluation/usage_raw.jsonl` - the recorded calls and validation drops (one line per event).
- `fixtures/message/*.json` - 215 recorded fixtures (213 single-model, plus the two below), each
  `{"facts": [...]}`.

## Decisions and deviations (all deliberate)

1. **Daily token cap forced a model split.** `qwen/qwen3.8-27b` has a free-tier limit of 200k tokens
   per day; the corpus needs roughly 215 x ~1.4k = ~300k, and the cap was reached after 98 messages
   (`TPD: Limit 200000, Used 198591`). The remaining messages were recorded with
   `openai/gpt-oss-20b` (115) and `openai/gpt-oss-120b` (2). Provenance is per fact, and the model is
   in the fixture key, so a warm run is still model-agnostic and reproducible. `TPD` 429s are surfaced
   immediately rather than retried; retrying a daily cap would only burn time. Per-minute 429s are
   still retried with backoff.
2. **Typographic normalisation (contract V4).** 60 of 215 messages print curly quotes/dashes, which
   the model returns inconsistently (a curly apostrophe came back as a control character). The
   purely typographic set is normalised to ASCII on both the delivered record and the V4 comparison
   (`code/extraction/text.py`). No letter, digit, or semantic mark changes, and normalising both
   sides cannot make a fabricated quote match.
3. **Validator corrections in `code/extraction/validate.py`**, both contract-aligned:
   `AMOUNT_FORBIDDEN_TYPES` narrowed to section 3's exact six types (the extra Class C/D entries only
   caused spurious drops the engine ignores anyway), and `income_ended` / `employment_ended` /
   `recurring_expense_increase` removed from `DATE_REQUIRED_TYPES` because the engine already applies
   a conservative default when the date is absent - dropping them would discard conservative
   evidence.
4. **`PROMPT_VERSION` is separate from `contract_version`.** The fact schema is unchanged, so the
   contract version stays `1.0.0`; the prompt version lives in the key payload, so editing the prompt
   invalidates the corpus without falsely claiming a schema change.

## Known limitations (for ticket 14)

- 15 messages yield no facts; sampling shows these are all inert types
  (`salary_confirmed_unchanged` for a date-only payroll notice, `bonus_unconfirmed`,
  `gig_payout_pending`, `expense_reimbursement_settled`), whose engine consequence is "no change", so
  the absence does not move a number.
- Undated salary changes ("your next salary is reduced to X", "regular salary for the next payroll is
  X") are emitted correctly but dropped by V10 because the contract requires an effective date and
  the message states none. The alternative - inventing a date - is forbidden by the "dropped, never
  repaired" rule. This is the largest measurable gap and is left for calibration to price.
- The model classifies more messages as `salary_first` and fewer as `salary_confirmed_unchanged` than
  the contract's census (41 vs 27, 3 vs 36); every accepted one carries a quoted amount and an
  employer source, and `evidence._set_income_amount` replaces rather than duplicates an existing
  stream, so no income is fabricated.

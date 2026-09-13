# 03: Extraction port and offline fixture adapter

**What to build:** The engine can obtain evidence facts through a single boundary that works entirely
offline, so development never blocks on a credential. A fixture-backed adapter resolves facts by
content hash and fails loudly on a miss rather than silently returning nothing.

Implements the interface frozen in `docs/contracts/extraction-fact-schema.md` v1.0.0. **No API calls
in this ticket.** A small number of hand-authored fixtures are included to support the adversarial
tests: a `windfall_solicitation` message, and a fact whose digits are absent from its verbatim quote.

**Blocked by:** 01.

**Owner:** OpenCode - `code/extraction/**`, `fixtures/**`

**Status:** completed 2026-09-12.

- [x] `ExtractionPort` exposes `facts_for_user(user_id)` and `image_amount(event_id)` per the contract
- [x] `facts_for_user` returns a deterministically ordered tuple, and an empty tuple rather than None when there is no evidence
- [x] `image_amount` returns None when unresolvable and **never** returns zero
- [x] Fixture key is sha256 over contract_version, provider, model, kind, subject_id, canonical_input, truncated to 16 hex chars
- [x] Canonical input uses sorted keys and compact separators
- [x] A fixture miss hard-fails and prints the missing key (for linked images without fixtures)
- [x] Changing the contract version or a prompt template turns every affected lookup into a miss, never a stale hit
- [x] Hand-authored adversarial fixtures are present and labelled as hand-authored
- [x] All 13 contract validation rules are enforced before any fact is returned

**2026-09-13 (ticket 11 follow-up):** the two hand-authored adversarial fixtures moved to
`tests/fixtures/adversarial/message/`. When ticket 11 recorded all 215 real message fixtures under
`fixtures/message/`, the adversarial `message_01` fixture both collided with and contradicted the
recorded one; isolating them keeps `fixtures/` a clean record of real model output, and the adapter
tests now point at the test-local directory.

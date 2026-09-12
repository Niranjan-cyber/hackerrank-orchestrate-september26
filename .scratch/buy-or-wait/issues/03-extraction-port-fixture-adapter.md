# 03: Extraction port and offline fixture adapter

**What to build:** The engine can obtain evidence facts through a single boundary that works entirely
offline, so development never blocks on a credential. A fixture-backed adapter resolves facts by
content hash and fails loudly on a miss rather than silently returning nothing.

Implements the interface frozen in `docs/contracts/extraction-fact-schema.md` v1.0.0. **No API calls
in this ticket.** A small number of hand-authored fixtures are included to support the adversarial
tests: a `windfall_solicitation` message, and a fact whose digits are absent from its verbatim quote.

**Blocked by:** 01.

**Owner:** OpenCode - `code/extraction/**`, `fixtures/**`

**Status:** ready-for-agent

- [ ] `ExtractionPort` exposes `facts_for_user(user_id)` and `image_amount(event_id)` per the contract
- [ ] `facts_for_user` returns a deterministically ordered tuple, and an empty tuple rather than None when there is no evidence
- [ ] `image_amount` returns None when unresolvable and **never** returns zero
- [ ] Fixture key is sha256 over contract_version, provider, model, kind, subject_id, canonical_input, truncated to 16 hex chars
- [ ] Canonical input uses sorted keys and compact separators
- [ ] A fixture miss hard-fails and prints the missing key
- [ ] Changing the contract version or a prompt template turns every affected lookup into a miss, never a stale hit
- [ ] Hand-authored adversarial fixtures are present and labelled as hand-authored
- [ ] All 13 contract validation rules are enforced before any fact is returned

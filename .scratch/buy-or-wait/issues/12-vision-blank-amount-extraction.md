# 12: Vision extraction for the 16 blank amounts

**What to build:** The 16 financial events with a blank `amount` get a real amount read from their
linked receipt image, committed as verified fixtures. A blank amount must never become zero.

Development-time only, using the vision credentials available to OpenCode. **No vision key is
required at submission runtime** - the committed fixtures are the shipped path.

5 of the 16 images belong to sample users, so extraction accuracy is directly checkable against known
good outputs. Measured cost for all 16 is about 24,155 image tokens.

**Blocked by:** 03.

**Owner:** OpenCode - `code/extraction/**`, `fixtures/**`

**Status:** ready-for-agent

- [ ] All 16 images resolve to a fixture carrying value, currency, verbatim amount string, and provenance
- [ ] Digit-in-quote verification: every digit of the parsed amount appears in the verbatim string after separator normalisation
- [ ] Thousand-separator and decimal-comma handling is parsed per the row's currency, never by locale guess - Rp 12.500.000 is twelve and a half million
- [ ] Images are pre-resized to the provider tier cap and never re-encoded as JPEG
- [ ] Images are placed before text in the request
- [ ] Two-call self-consistency; disagreement discards the result rather than guessing
- [ ] A failed extraction yields no fixture rather than a fabricated amount
- [ ] The 5 sample-user images are cross-checked against the sample outputs
- [ ] No fixture for a blank amount is ever zero

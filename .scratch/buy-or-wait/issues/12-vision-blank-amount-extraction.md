# 12: Vision extraction for the 16 blank amounts

**What to build:** The 16 financial events with a blank `amount` get a real amount read from their
linked receipt image, committed as verified fixtures. A blank amount must never become zero.

Development-time only, using the vision credentials available to OpenCode. **No vision key is
required at submission runtime** - the committed fixtures are the shipped path.

5 of the 16 images belong to sample users, so extraction accuracy is directly checkable against known
good outputs. Measured cost for all 16 is about 24,155 image tokens.

**Blocked by:** 03.

**Owner:** OpenCode - `code/extraction/**`, `fixtures/**`

**Status:** completed 2026-09-13.

- [x] All 16 images resolve to a fixture carrying value, currency, verbatim amount string, and provenance
- [x] Digit-in-quote verification: every digit of the parsed amount appears in the verbatim string after separator normalisation
- [x] Thousand-separator and decimal-comma handling is parsed per the row's currency, never by locale guess - Rp 12.500.000 is twelve and a half million
- [x] Images are pre-resized to the provider tier cap and never re-encoded as JPEG
- [x] Images are placed before text in the request
- [x] Two-call self-consistency; disagreement discards the result rather than guessing
- [x] A failed extraction yields no fixture rather than a fabricated amount
- [x] The 5 sample-user images are cross-checked against the sample outputs
- [x] No fixture for a blank amount is ever zero

## What was built

- `code/extraction/amounts.py` - currency-aware parsing of a printed money string, and the shared
  digit-in-quote check. Parsing reads separator *structure* first (multiple separators, both kinds,
  or a lone separator before one/two digits are unambiguous) and consults the row's currency only for
  the one ambiguous shape, a single separator before exactly three digits.
- `code/extraction/vision.py` - `prepare_image` (PNG only, long-edge cap 1568, in-cap images passed
  through byte-for-byte, Pillow imported lazily for the one oversized image), `build_vision_messages`
  (image data URI before the text), `RawReading` + `reconcile` (two-call consistency), `reading_to_fact`
  (parse + model-amount agreement + V5, so a hallucinated figure is rejected), the offline
  `ScriptedVisionClient`, a lean `OpenAiCompatibleVisionClient`, and `VisionExtractor` with atomic
  fixture writes and per-image status.
- `code/extraction/prompts.py` - the vision system prompt, the strict image schema (no decision slot),
  and `build_image_fixture_payload`, whose canonical input carries the prompt version and the source
  PNG's `sha256`.
- `code/extraction/record_images.py` - the development-time CLI. `--readings` replays two captured
  readings per image through the same extractor path; without it, a live provider is used.
- `fixtures/image_readings.json` - the two verified passes per image (the recording's canonical input).
- `fixtures/image/*.json` - the 16 committed fixtures, each `{"facts": [image_amount]}`.
- `tests/test_amounts.py`, `tests/test_vision.py`, `tests/test_vision_fixtures.py` - 45 new tests.
- `Validate.py` V5 now calls `amounts.digit_in_quote`; `fixture_adapter.image_amount`'s expected-key
  payload matches the recorder's.

## Decisions and deviations (all deliberate)

1. **Offline recording, not a live call.** No vision key is exportable from this environment
   (`docs/investigation/provider-capability.md` section 1), so the shipped recording is option 2 from
   section 3: opencode's own vision reads each PNG twice, and `record_images.py` replays the two
   readings through the identical two-call path. The live client exists and is unit-tested so a runtime
   provider can plug in later (D29).
2. **Structural separator parsing beats strict locale.** `image_01` prints an IDR payslip as
   `IDR 4,365,000` (comma grouping). A strict "IDR uses '.' as the thousands separator" rule would
   misread it by 10**6, so structure decides and the currency breaks only the lone-three-digit tie.
   `Rp 12.500.000` still parses to 12,500,000.
3. **The digit check ignores presentation-only trailing zeros.** `2298.00` is 2298; demanding two
   zeros the receipt never printed failed `image_13` on the first recording run. The fix was verified
   not to change any message-channel output (old vs new semantics produced byte-identical
   `golden_requests.csv`), so the message corpus is untouched.
4. **The model returns both `amount` and `verbatim_amount_string`.** The number the engine uses is
   parsed here from the printed string; the model's rendering is only a cross-check (equality + V5), so
   a transposition is an auditable rejection rather than a plausible wrong amount.
5. **In-cap images are never re-encoded.** Only `image_01` (1628px) exceeds the 1568px cap; every other
   image is sent byte-for-byte. Pillow is a lazy, development-time-only import, so the runtime stays
   stdlib-only (D10).

## Cross-checks

- `request_16` is the only sample request whose blank event sits inside the forecast window. It still
  matches the published sample exactly (`122500`, `affordable_now`, `full_payment`, `2023-08-12`), and
  that is asserted end to end in `TestSampleCrossCheck`.
- Independent corroboration: deterministic imputation for `user_03`'s salary stream yields
  **4,365,000**, exactly `image_01`'s net pay.
- All 16 fixtures pass V1-V13 through the offline adapter with **zero** image violations.
- `image_02` (balance due 1,00,000) and `image_05` (amount due 704.05) are the two sample images whose
  events are future reserves; both are read off the receipt's own balance-due line, matching the event
  date to the receipt date.

## Blast radius

- Compared with the same pipeline minus the image facts, exactly **2 of 250** evaluation rows change:
  - `request_64`: an installment plan (safe 41,880.33) -> `0 / not_affordable`, because the previously
    unpriced 79,679.26 pending grocery invoice is now reserved.
  - `request_73`: `0 / not_affordable` -> `affordable_now` with a 71,400 full payment, because pricing
    the hospital bill (3,650) lets the forecast resolve instead of degrading on an unpriced outflow.
- `fixtures/golden_requests.csv` and `output.csv` were regenerated. Their diff against `HEAD` is much
  larger than two rows because the golden file had not been rewritten since ticket 02 and had absorbed
  no characterization update since tickets 04-11; the image change contributes exactly the two rows
  above on top of that pre-existing drift.

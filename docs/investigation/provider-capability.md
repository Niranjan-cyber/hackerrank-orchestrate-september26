# Investigation: model provider capability (2026-09-12)

Empirically verified, not assumed. This **supersedes the framing of D13** ("no API key available"),
which was true only for Anthropic.

---

## 1. What credentials actually exist

| Credential | Where it lives | Reachable by our submitted Python app? |
|---|---|---|
| `GROQ_API_KEY` | **Environment variable** (len 56) | **YES** — satisfies "read secrets from environment variables only" |
| OpenRouter | opencode's `~/.local/share/opencode/auth.json` | No — not exported |
| Google (Gemini) | opencode's `auth.json` | No — not exported |
| Nvidia | opencode's `auth.json` | No — not exported |
| OpenCode Go | opencode's `auth.json` | No — not exported |
| `ANTHROPIC_API_KEY` | absent | No |

**Consequence**: opencode can drive Gemini/OpenRouter/Nvidia models interactively, but the
*submitted application* can only use **Groq** today. Scraping opencode's `auth.json` from the app is
rejected — it is a credential store, and it would make the submission non-reproducible for graders.

---

## 2. Groq capability — verified by live calls

`GET /openai/v1/models` → **HTTP 200**. 14 models:

```text
allam-2-7b                          groq/compound
canopylabs/orpheus-arabic-saudi     groq/compound-mini
canopylabs/orpheus-v1-english       meta-llama/llama-prompt-guard-2-22m
openai/gpt-oss-120b                 meta-llama/llama-prompt-guard-2-86m
openai/gpt-oss-20b                  qwen/qwen3.6-27b
openai/gpt-oss-safeguard-20b        qwen/qwen3.8-27b
whisper-large-v3                    whisper-large-v3-turbo
```

### Strict JSON schema works

Tested `response_format: {type: "json_schema", json_schema: {strict: true, schema: {...}}}` with
`additionalProperties: false` and full `required`:

| Model | Result | prompt / completion tokens |
|---|---|---|
| `openai/gpt-oss-120b` | `{"ok":true,"n":7}` | 192 / 75 |
| `qwen/qwen3.8-27b` | `{"n": 7, "ok": true}` | **41 / 12** |

**Operational trap found**: `gpt-oss-120b` is a **reasoning** model. At `max_tokens: 64` it returned
**HTTP 400 `json_validate_failed` with an empty `failed_generation`** — reasoning consumed the whole
budget before emitting JSON. It only succeeded at `max_tokens: 1200`. A tight `max_tokens` on a
reasoning model is a silent failure mode; budget generously or avoid reasoning models for extraction.

**Chosen for message extraction: `qwen/qwen3.8-27b`** — 16× cheaper in completion tokens on the same
task, non-reasoning, and schema-compliant.

### ⚠ Groq has NO vision model

Zero multimodal models in the catalogue (no Llama-4 Scout/Maverick, no VL, no LLaVA). **The 16 image
amounts cannot be read through Groq.** This is the one genuine capability gap.

### Bonus: a purpose-built injection classifier

`meta-llama/llama-prompt-guard-2-86m` and `-22m` are dedicated prompt-injection/jailbreak
classifiers. This is materially better than asking the extractor to self-report
`injection_suspected` (a model grading its own input), and at 22–86M parameters it is nearly free.
Recommended as the implementation of injection control #2.

---

## 3. Vision options for the 16 blank amounts

Ranked by preference:

1. **Export a key the user already holds** — `OPENROUTER_API_KEY` or `GEMINI_API_KEY` (both present
   in opencode's store; Gemini 2.5/3.x flash and many OpenRouter models are multimodal, with free
   tiers). Cleanest: the submitted app reads it from the environment like any other secret, and the
   pipeline is genuinely end-to-end reproducible.
2. **One-time offline extraction into committed fixtures** — opencode (which *can* reach
   `google/gemini-*` and `opencode-go/deepseek-v4-flash-vision-exp`) reads the 16 PNGs once and
   writes fixtures carrying `{value, currency, verbatim_quote, image_id, model, timestamp}`. The app
   replays them. Honest and reproducible **provided the fixtures ship and the provenance is
   documented**, but the grader's re-run exercises the cache rather than the vision call.
3. **Deterministic imputation, degraded path** — impute a blank amount from the same user's own
   history for that category (e.g. median of settled same-category events within the lookback
   window), tagged with an `IMPUTED_BLANK_AMOUNT` reason code. Never zero, which the challenge
   forbids. Affects 16 of 275 users — **11 of the 250 evaluation requests**.

Option 3 must exist regardless, as the failure path when no vision provider is reachable at run time.

---

## 4. Rate limits and cost

Groq's free tier covers this workload comfortably. Estimated for 215 message extractions at
~600 prompt / ~150 completion tokens each: **~129k prompt + ~32k completion tokens** for the whole
dataset — a single-digit-cents job on paid pricing, and plausibly $0 on the free tier.

**Conclusion: cost is not a constraint on this project.** Rate limiting (requests/minute) is the only
real operational concern, so the extraction loop needs a modest retry-with-backoff and must be
resumable — which the content-addressed fixture cache already provides for free.

---

## 5. What this changes

| Previously recorded | Now |
|---|---|
| D13: "no API key is assumed or required" | Still the right *architecture*, but it is no longer the *situation*. Real text extraction is available today via Groq; only vision is blocked. |
| D17: content-addressed fixtures | Circularity resolved — fixtures can now be **recorded from real Groq calls**. But the fallback tier must still be a deterministic extractor, not an empty fixture directory. |
| D21: cost levers (Anthropic pricing) | Anthropic pricing is moot unless a key appears. Groq free tier; no caching, no batching, one call per item — unchanged conclusion, different reason. |
| Injection control #2 (`injection_suspected` self-report) | Replace with `llama-prompt-guard-2-86m`, a purpose-built classifier. |
| Extraction model choice | `qwen/qwen3.8-27b` for messages. Vision model TBD on the decision above. |

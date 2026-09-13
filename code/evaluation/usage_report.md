# Usage Report - Buy or Wait?

Covers the final full-dataset run of `code/main.py` that produced the submitted `output.csv` (250 requests).

## 1. Live model calls made by this run

**0 live model calls.** The challenge permits a warm, cache-backed final run (no cold run required). Every extraction fact this run needed was already present on disk as a committed fixture, so `code/main.py` never imports a network client and never spends a token or a dollar.

| Provider | Model | Live calls | Cost |
|---|---|---|---|
| - | - | 0 | $0.00 |

## 2. Fixture / cache hits used by this run

| Fact type | Cache hits used by this run |
|---|---|
| message_fact (from `fixtures/message/`) | 215 |
| image_amount (from `fixtures/image/`) | 16 |
| **Total** | **231** |

231 facts served from cache across 250 evaluation requests.

## 3. Fixture-cache provenance (historical, not this run's cost)

The figures below describe **when the fixture cache was built** (`code/extraction/record.py`, tickets 11-12) - a one-time, development-time cost. They are **not** incurred by this run and are reported separately, as the challenge's cost-analysis ask (`problem_statement.md`) still expects a true accounting of every model call the system ever made to produce its answers.

### 3.1 Message extraction (Groq)

228 call attempts: 215 succeeded (one fixture each), 13 failed and were retried or escalated to a different model. 0 messages were already cached during recording. 67 extracted facts were dropped after failing contract validation (never written as a fixture, never a model call).

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Cost |
|---|---|---|---|---|---|---|
| groq | openai/gpt-oss-120b | 2 | 3427 | 1278 | 4705 | $0.001473 |
| groq | openai/gpt-oss-20b | 115 | 195425 | 80473 | 275898 | $0.059779 |
| groq | qwen/qwen3.8-27b | 98 | 132186 | 8614 | 140800 | $0.043416 |
| **(all)** | **(all)** | **215** | **331038** | **90365** | **421403** | **$0.104668** |

Average per successful call: 1539.71 input tokens, 420.30 output tokens, 1960.01 total tokens, $0.000487 cost.
Averaged over all 250 evaluation requests instead (AGENTS.md 6.5's "per request" framing): 1685.61 total tokens/request, $0.000419/request - this is the historical cache-build cost amortized over the requests it serves, not a cost this run itself paid (see section 4).

Failed-call error classes: `http_400` x2, `rate_limit_exceeded` x11.

Dropped-fact validation rules: `V10` x64, `V9` x3.

### 3.2 Image amount extraction (vision)

Groq has no multimodal model (verified; see `docs/investigation/provider-capability.md`), so the 16 blank amounts were read offline through **opencode** using **deepseek-v4.1-flash** (opencode vision, two independent passes per image; the extractor reconciles them and discards on disagreement), and committed as fixtures under `fixtures/image/`. This was a one-time, human-driven session outside the submitted application's own credentials, so no token count or dollar cost was captured for it - reported here as **not tracked** rather than estimated, per the same honesty standard applied to every other number in this file.

## 4. Grand total

- This run (`output.csv`, 250 requests): **0 calls, $0.00** (0 tokens/request, $0.00/request).
- Lifetime cost to build the cache this run replays: **215 calls, $0.104668** (1685.61 tokens/request, $0.000419/request; message extraction only, image extraction cost not tracked, see 3.2).


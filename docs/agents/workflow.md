# Implementation workflow: models, context hygiene, and the per-ticket loop

Operational guide for the build phase. Both agents read this before starting a ticket.
Companions: `.scratch/buy-or-wait/README.md` (the board), `docs/agents/tooling.md` (environment facts).

---

## 1. There are THREE model decisions, not two

This is the distinction most easily conflated, and getting it wrong would put a model call in the
wrong place:

| # | Decision | What it chooses | Where it lives |
|---|---|---|---|
| 1 | **Claude Code's model** | The agent that writes `code/engine/**` and `code/main.py` | `/model` in this terminal |
| 2 | **opencode's model** | The agent that writes `code/extraction/**`, `code/eval/**`, `tests/**` | `opencode -m <provider/model>` |
| 3 | **The application's runtime model** | What the *submitted program* calls when it extracts facts | Code + `GROQ_API_KEY` |

Decisions 1 and 2 are **development tooling** — they never appear in `usage_report.md`.
Decision 3 **is** the graded AI usage and the only one the report describes.

### Decision 3 is already settled — do not revisit it per ticket

| Job | Model | Why |
|---|---|---|
| 215 message facts | **`qwen/qwen3.8-27b`** via Groq REST, `GROQ_API_KEY` from env | Verified by live call: strict JSON schema works, 41 prompt / 12 completion tokens on a probe. Non-reasoning, so no silent empty-generation failure. |
| 16 blank amounts | A vision model at **development time only**, results committed as fixtures | Groq has **no** multimodal model. No vision key is required at submission runtime. |

⚠ **Verified trap**: never set a tight `max_tokens` on a reasoning model. `openai/gpt-oss-120b`
returned HTTP 400 `json_validate_failed` with an **empty** `failed_generation` at `max_tokens: 64`,
and only worked at 1200.

---

## 2. Claude Code model routing, per ticket

Opus 5 is the default for anything where a subtle invariant can be silently wrong. Drop to Sonnet 5
only for mechanical work. Haiku 4.5 is not recommended for any ticket here — every one of them turns
on an invariant.

| Ticket | Model | Reason |
|---|---|---|
| **01** tracer bullet | **Opus 5** | Defines the value types, the validator and the writer that everything else depends on. Highest leverage in the build. |
| **04** FX + lifecycle | **Opus 5** | Seven lifecycle patterns plus the duplicate-reservation reversal — exactly where a weaker model regresses to the "obvious" wrong rule. |
| **05** recurrence | **Opus 5** | Semi-monthly phase drift and month-end clamping are subtle and silently wrong when wrong. |
| **06** simulator | **Opus 5** | Window boundaries, per-event floor test, swappable ordering key. |
| **07** ranking | **Opus 5** | Lexicographic 7-tuple, pre-rank pruning, the `affordable_now` subtlety. |
| **08** spending changes | **Opus 5** | The pruning biconditional. A model that "simplifies" it loses 3 of 25 samples. |
| **09** reason codes + explanation | **Sonnet 5** | Templating from existing state — mechanical once the codes exist. |
| **10** evidence authority | **Opus 5** | The authority matrix and the scam traps. |
| **14** calibration | **Opus 5** | Judgment-heavy; this is where the score is actually won or lost. |
| **15** packaging | **Sonnet 5** | Checklist-driven against explicit acceptance criteria. |

`/fast` (Opus with faster output, not a smaller model) is worth it on 09 and 15 where you want Opus
quality without the wait. This session's settings already pin `effortLevel: high` for Opus 5.

---

## 3. opencode model routing, per ticket

Five providers are authenticated (OpenRouter, Google, Groq, Nvidia, OpenCode Go), so the catalogue is
large. Start with a code-specialised model, and switch if it underperforms — these are starting
points chosen by specialisation and tier, not benchmark claims.

| Ticket | Starting model | Reason |
|---|---|---|
| **02** scorecard harness | `opencode-go/kimi-k2.7-code` | Code-specialised; the harness is straightforward CSV diffing and reporting. |
| **03** extraction port + fixtures | `opencode-go/qwen3.8-max` or `opencode-go/glm-5.3` | Top-tier general reasoning — this ticket implements 13 validation rules and a hash-keyed cache, where precision matters more than speed. |
| **11** Groq extraction driver | `opencode-go/kimi-k2.7-code` | Mechanical API-client work against a frozen schema. |
| **12** vision blank amounts | **`google/gemini-3.1-pro-preview`** or `google/gemini-2.5-pro` | **Must be vision-capable**, and Pro tier over Flash because 16 numeric reads are accuracy-critical, not throughput-critical. |
| **13** focused unit tests | `opencode-go/kimi-k2.7-code` | Test authoring against seven named primitives. |

Invoke with `opencode -m <provider/model>`; `opencode models` lists everything.

### Ticket 12 has a credential subtlety — read before starting it

Google/OpenRouter/Nvidia credentials live in **opencode's own credential store**
(`~/.local/share/opencode/auth.json`), **not** in the environment. So a standalone Python script
cannot reach them, and scraping that file is rejected (it is a credential store, and it would make the
submission non-reproducible).

Therefore ticket 12 is done **by opencode itself**: run opencode with a vision model, have it read the
16 PNGs directly, and write the fixture JSONs with full provenance. No script, no exported key, works
today.

**Free upgrade available**: the contract asks for two-call self-consistency. Doing those two calls
with **two different models** (e.g. `google/gemini-3.1-pro-preview` and
`opencode-go/deepseek-v4-flash-vision-exp`) is strictly stronger than the same model twice —
cross-model agreement on a digit is far better evidence than one model agreeing with itself.

**Only if** you later export `GEMINI_API_KEY` or `OPENROUTER_API_KEY` does the scripted path become
available. It is an upgrade, never a dependency.

---

## 4. `/clear` vs `/compact` — the actual tree

From the skill's own `PHASE-BOUNDARIES.md`. Work the questions **top to bottom at a phase boundary**;
the first **yes** wins. Never decide this mid-phase — mid-phase you either continue or split the
remainder into subagents.

**1. Can you continue in this session?**
Yes if either the next phase needs this one as a **primary source**, or enough smart zone (~150k
tokens) remains. Continue costs nothing and loses nothing, so rule it out first.

**2. Is the context irrelevant to what comes next?** → **`/clear`**
The cheapest move on the board; hands back the whole window and stays resumable.
⚠ **The cost of getting this wrong is one-way**: clear a *relevant* context and you lose the **why**
behind what you built, and reading the diff back never returns it.

**3. Do you need to hand off?** → **`/handoff`**
Narrow, and this list is the whole clause: new **harness**, new **directory/repo**, a **colleague**,
or forking a side task found **mid-phase**. What it buys is portability. If nothing is travelling, you
don't need it.

**4. Can the task run AFK, no steering?** → **subagent**
Leaves this session untouched. Automated review is the standard case.

**5. Otherwise → `/compact`**, with an instruction (`/compact we're going to QA this area`).
**It is the default, not the first reach.** It sits last because the four questions above are cheaper
or more precise. The failure mode of starting here is a fresh session that is confidently wrong about
a decision the summary flattened.

### Applied to this build

| Boundary | Call | Why |
|---|---|---|
| Planning → ticket 01 | **`/clear`** | Q1 is no (the window is long and ticket 01 needs none of it); Q2 is yes — every decision is already in `CONTEXT.md`, the contract and the ticket. Nothing in the planning window is a primary source for writing a CSV writer. |
| Between engine tickets (01→04, 04→05, …) | **`/clear`** | Each ticket is self-contained by construction. This is the flow's explicit prescription. |
| 05 → 06 | **Continue** *if* 05 ended with live calibration insight you haven't written down yet | Q1 applies: the next phase wants the reasoning verbatim. **Better: write it to `docs/investigation/` and `/clear` anyway.** |
| Mid-ticket, hit something gnarly | **Continue or subagent** | Never compact mid-phase; it loses the thread. |
| Inside ticket 14 (calibration) | **Continue** for the whole 3h block | Each sweep needs the previous sweep's numbers as a primary source. Write results to `docs/investigation/calibration.md` as you go so the window is disposable at the end. |
| 14 → 15 | **`/clear`** | Packaging needs the frozen parameter values (on disk), not the search that found them. |
| Ticket review | **subagent** | Q4: a diff review runs AFK and reports back. |

**Net: `/clear` is the right call at almost every boundary in this build**, precisely because the
ticket files and `CONTEXT.md` already externalise the *why*. `/compact` should be rare here — reach
for it only if a ticket ends mid-thought with something important not yet written down.

---

## 5. The per-ticket loop

Run this identically for every ticket, in both terminals:

1. **`/clear`**
2. Point the agent at the ticket file, e.g. `.scratch/buy-or-wait/issues/01-tracer-bullet-valid-output.md`
3. Set the model for that ticket (§2 / §3)
4. **`/implement`** — it drives `/tdd` internally one red-green slice at a time, then closes out with
   `/code-review` on the diff before committing. Do not invoke those two separately.
5. Tick the ticket's acceptance boxes and note completion at its top
6. Append the `log.txt` entry required by `AGENTS.md` §5.2
7. Commit on a short-lived branch, merge, and move to the next unblocked ticket

### Parallel choreography

- **Ownership is absolute.** Claude touches only `code/engine/**` and `code/main.py`; opencode only
  `code/extraction/**`, `code/eval/**`, `tests/**`, `fixtures/**`. Neither edits the other's files.
  Importing across the boundary is fine; **editing** is not.
- **No shared paths means no merge conflicts.** Merge often anyway.
- **Findings go to `docs/investigation/*.md`**, never relayed through chat — the other agent reads
  the file, not your terminal scrollback.
- **Only ticket 01 is unblocked at t=0.** Until it lands, opencode can productively author 03's
  hand-authored adversarial fixture JSON (contract §3 and §8 fully specify it) and 02's CSV
  diff/report core (testable against a fabricated "actual" file).

### The one standing rule during implementation

If implementation exposes a concrete contradiction with the challenge requirements, **stop and
surface it**. Do not silently redesign. The architecture, contract, spec and ticket set are validated;
a surprise means either a real challenge-requirement conflict worth raising, or a misreading worth
correcting — both are conversations, not unilateral edits.

---

## 6. Quick reference

```text
Claude Code   : Opus 5 for 01, 04-08, 10, 14   |  Sonnet 5 for 09, 15
opencode      : kimi-k2.7-code for 02, 11, 13  |  qwen3.8-max / glm-5.3 for 03
                google/gemini-3.1-pro-preview for 12 (vision, Pro tier)
application   : groq qwen/qwen3.8-27b (messages) + committed vision fixtures (images)

boundary      : try Continue -> /clear -> /handoff -> subagent -> /compact  (first yes wins)
this build    : /clear at nearly every boundary; Continue through calibration; subagent for review
```

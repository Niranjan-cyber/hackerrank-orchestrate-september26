# Buy or Wait? - ticket board (filesystem-coordinated)

GitHub Issues are disabled on this fork, and coordination is filesystem-only by decision. These files
are the tracker. One ticket per file, numbered in dependency order (blockers first).

**Working rule:** take any ticket whose blockers are all complete. Mark a ticket done by checking its
boxes and noting completion at the top. Do not edit a ticket owned by the other agent.

## Ownership

| Agent | Directories |
|---|---|
| Claude Code | `code/engine/**`, `code/main.py`, integration, calibration, final review |
| OpenCode | `code/extraction/**`, `code/eval/**`, `tests/**`, `fixtures/**` |

## Dependency graph

```text
01 tracer bullet (no blockers)                    <- THE ONLY TICKET UNBLOCKED AT t=0
 |-- 02 scorecard harness            [OpenCode]
 |-- 03 extraction port + fixtures   [OpenCode]
 |    |-- 11 Groq message extraction [OpenCode]  <- only text API ticket
 |    |-- 12 vision blank amounts    [OpenCode]  <- only vision ticket
 |-- 04 FX + lifecycle + cash state  [Claude]
      |-- 05 recurrence + projection [Claude]
           |-- 06 simulator + safe amount + earliest date [Claude]
           |    |-- 07 plans + ranking      [Claude]
           |         |-- 08 spending changes + pruning [Claude]
           |         |    |-- 13 focused unit tests    [OpenCode]
           |         |-- 09 reason codes + explanation + trace [Claude]
           |-- 10 evidence authority + conflicts  [Claude]  (also needs 03)  DONE

14 calibration   <- needs 02, 10, 11, 12
15 final run + packaging <- needs 09, 13, 14
```

**Dependency audit (verified against ticket contents, not assumed):**

| Edge | Verdict | Evidence |
|---|---|---|
| `01 -> 02` | **GENUINE, kept** | 02 requires driving the 25 samples through the *complete* pipeline, and its criteria print an `actual` column and exit non-zero on regression. There is no pipeline and no actual output without 01. |
| `01 -> 03` | **GENUINE, kept (but thin)** | 03 must return `Fact` objects and enforce the 13 contract rules on them. `Fact` is a core value type created in 01; defining it in `code/extraction/` would invert the hexagonal dependency direction (core importing from adapter). Blocking surface is exactly one dataclass, already field-specified in contract §3, so 03 unblocks minutes into 01. |
| `06 -> 10` | **REMOVED** | No criterion in 10 consumes `amount_safe_to_pay`, `earliest_date_for_full_payment`, the ledger, or the same-day sort key — 06's only outputs. 10 amends *projected streams*, which 05 produces. Replaced by `05 -> 10`. Also the correct ordering: evidence amends streams *before* simulation, so 06 and 10 are siblings on 05. |

## Critical path

`01 -> 04 -> 05 -> 06 -> 07 -> 08 -> 13 -> 15`, with `03 -> 11/12` and `05 -> 10 -> 14` running in
parallel.

**Ticket 01 alone produces a submittable artifact**, with no extraction code and no API involvement -
the shell passes an empty fact tuple. Every API call in the whole build lives in tickets 11 and 12,
both early and both one-time.

**Startup note:** only ticket 01 is unblocked at t=0. OpenCode's tickets 02 and 03 both genuinely
gate on it, so at the very start OpenCode can productively author only the parts that need no engine
types: 03's hand-authored adversarial fixture JSON (fully specified by contract §3 and §8) and 02's
CSV diff/report core (testable against a fabricated actual file). Both complete once 01 lands.

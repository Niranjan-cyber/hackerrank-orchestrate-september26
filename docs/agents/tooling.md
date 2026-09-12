# Tooling: skills, MCPs, and how they enter the workflow

Verified on this machine on 2026-09-12. Facts here were checked by running the commands, not assumed.
Companion to `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/domain.md`.

---

## 1. Environment facts that constrain tooling choices

| Fact | Verified how | Consequence |
|---|---|---|
| Python **3.13.9** available as **`python`** (anaconda) | `python --version` | Use `python` locally. |
| **`python3` is a broken WindowsApps stub** | `python3 --version` → "Python was not found" | Never script against `python3` locally. The README must still document `python3 code/main.py` for Linux graders. |
| **`pytest` is NOT installed** (`iniconfig` missing) | `python -c "import pytest"` | Test with stdlib **`unittest`**. Keeps the zero-dependency promise and works for graders. |
| **IPython / jupyter_client absent** | `python -c "import IPython"` | The IDE MCP's `executeCode` tool is **non-functional** here. Do not plan around it. |
| **Pillow 12.2.0 installed** | `python -c "import PIL"` | Usable for local image inspection, but must **not** become a submission dependency. |
| Node 24.11.1, npm 11.6.2, uv 0.12.10 present | `--version` | Available but unnecessary; the solution is Python. |
| **`gh` 2.100.0, authenticated** as `Niranjan-cyber`, scopes `gist, read:org, repo, workflow` | `gh auth status` | Issue-tracker operations are possible. |
| **Issues are DISABLED on the fork** (`has_issues: false`) | `gh api repos/.../...` | ⚠ `gh issue create` fails today. Either `gh repo edit --enable-issues` or switch to a local tracker. **Open decision.** |
| GitHub REST reachable, GraphQL timed out once | `curl`, `gh api` | Transient. Prefer REST (`gh api`) over GraphQL-backed commands if flakiness recurs. |
| opencode **1.4.6** reads skills from `.claude/skills`, `.opencode/skills`, `.agents/skills` (project + user level) | `opencode debug skill` | The 25 Matt Pocock skills are installed at `~/.agents/skills` and visible to opencode. |

---

## 2. Skills

### Installed and load-bearing for this project

| Skill | Where it enters the workflow | Used by |
|---|---|---|
| `grill-with-docs` / `grilling` | Phase 1 (done) and the pre-spec grilling round | Claude Code |
| `domain-modeling` | Maintaining `CONTEXT.md` vocabulary as terms sharpen | Claude Code |
| `to-spec` | Turning the brief into the build spec | Claude Code |
| `to-tickets` | Splitting the spec into tracer-bullet tickets with blocking edges | Claude Code |
| `tdd` | The simulator, ranker, and validator — behaviours with crisp assertions | Both |
| `diagnosing-bugs` | Sample-harness mismatches; the feedback loop is the per-column diff | Claude Code |
| `code-review` | Two-axis review per ticket before merge | Claude Code |
| `claude-api` (Anthropic built-in) | Extraction layer: model ids, pricing, structured outputs, token counting — directly serves the `usage_report.md` requirement | Claude Code |
| `writing-for-agents` | Editing `AGENTS.md` / `CONTEXT.md` / these docs | Claude Code |

### Recommended install (one)

**`obra/superpowers@verification-before-completion`** (208K installs) — forces evidence-based
verification before declaring work done. Enters at the **packaging stage**, where the failure modes
are existential: 250 rows present, exact column order, all enums valid, partial-payment amounts
summing to `requested_amount`, `evaluation/usage_report.md` present and non-empty. One short file,
loads once, near-zero token cost.

### Rejected, with reasons

| Rejected | Why |
|---|---|
| `obra/superpowers@systematic-debugging` | Duplicates installed `diagnosing-bugs`; two debug loops conflict |
| `obra/superpowers@test-driven-development`, `writing-plans`, `executing-plans`, `subagent-driven-development` | Overlap installed `tdd` / `to-spec`; the multi-agent ones spend tokens on ceremony |
| `pandas-pro`, `data-science-python-stack`, `data-processing` | 25k rows is trivial for stdlib `csv`; pandas dtype coercion actively threatens determinism |
| pytest-boilerplate skills | Sub-500 installs, and we are using stdlib `unittest` anyway |
| prompt-injection / LLM-security skills | Our defence is **architectural** (closed-enum schema, deterministic consumer). A skill adds nothing to a one-paragraph rule |
| PDF/OCR extraction skills | The vision model reads the PNGs directly; no OCR layer exists |
| LLM-benchmark harness skills (`evaluating-llms-harness`, `nemo-evaluator-sdk`) | Our harness is a short diff against `sample_requests.csv` |
| documentation / dataviz / cost-tracking skills | `README` + `usage_report.md` are two short files; no visualisation is a deliverable |

---

## 3. MCP servers

**Conclusion: no MCP server is load-bearing for this project. Install none; wire none.**
This is a deliberate finding, not an omission — time spent wiring MCPs here returns nothing.

Configuration state: there is **no project `.mcp.json`** and no project-level Claude settings.
All MCP servers present are user-level.

| Available MCP | Relevance | Verdict |
|---|---|---|
| `ide` — `getDiagnostics` | Mildly useful: surfaces Python errors from the IDE's language server without running a separate linter | **Opportunistic use only** |
| `ide` — `executeCode` | **Non-functional**: requires IPython/jupyter_client, both absent | Unusable |
| Canva, Gmail, Google Drive, Slack, Vercel | No deliverable involves design, email, cloud docs, team chat, or deployment | Ignore |
| `playwright` | No web UI exists in this project | Ignore |

Considered and rejected as additions:

- **filesystem MCP** — redundant; the harness has native file tools.
- **sqlite MCP** — could query the 25k events, but Python `csv` + dicts is faster to write, and
  introducing a database would undermine the determinism and zero-dependency stance.
- **fetch/web MCP** — `WebSearch` / `WebFetch` are already native.

opencode can share MCP servers via `opencode mcp add` if that ever changes; today there is nothing
worth adding.

---

## 4. Non-MCP tools that are actually load-bearing

| Tool | Role |
|---|---|
| `python` (3.13.9) | The solution runtime and all throwaway analysis |
| stdlib `unittest` | The test runner (pytest unavailable) |
| Bash + `awk`/`grep`/`curl` | Fast dataset reconnaissance and connectivity checks |
| `gh` CLI (REST via `gh api`) | Issue tracker, once Issues are enabled or the tracker decision changes |
| `git` with short-lived branches | Integration boundary between the two terminals |
| `opencode` (1.4.6) | The second terminal; `opencode debug skill` verifies skill visibility |

---

## 5. How the two agents use this tooling

Ownership is by **file path**, so the terminals never contend:

| | Claude Code | opencode |
|---|---|---|
| Owns | `code/engine/**` — state reconstruction, recurrence, simulator, ranker, validator | `code/extraction/**`, `code/eval/**`, `tests/**` |
| Skills it drives | `to-spec`, `to-tickets`, `diagnosing-bugs`, `code-review`, `claude-api`, `tdd` | `tdd`, `implement` |
| Never touches | `code/extraction/**`, `tests/**` | `code/engine/**`, root `output.csv` |

**Gate (decided):** opencode does not start until the extraction fact-schema contract is written and
committed. Parallelising before the interface exists is how the two terminals collide.

**Shared state:** this repo, `CONTEXT.md`, and the issue tracker. Both agents read `AGENTS.md`
automatically (opencode treats it as project instructions), so both comply with the `log.txt`
transcript requirement without extra prompting — already observed working for a research sub-agent.

---

## 6. Open tooling decisions for the next grilling round

1. **Issue tracker viability** — Issues are disabled on the fork. Enable them (`gh repo edit
   --enable-issues`, reversible) or switch to a local markdown tracker under `.scratch/`? A solo 24h
   build may not need the round-trip latency of a remote tracker at all.
2. **`verification-before-completion`** — install now, or rely on the validator plus a manual
   packaging checklist?

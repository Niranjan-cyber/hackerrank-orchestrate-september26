# 08: Spending-change generation and the proven pruning rule

**What to build:** When nothing safe completes the request by its deadline, the engine finds the
smallest set of permitted flexible-spending changes that unlocks it, and reports them.

The pruning rule is a **biconditional**, not a shortcut: expand change-set variants if and only if no
safe no-change plan completes the full request by `desired_completion_date`. Proof: a safe no-change
plan that completes has rank key starting (0, 0, ...), while any change-requiring plan has
needs_changes = 1 and therefore loses at level 1 or level 2. The converse is load-bearing - samples
06, 11 and 21 all have an earliest date **after** the deadline, so no no-change plan completes,
variants must be explored, and they win at level 1. Naive pruning loses all three.

**Blocked by:** 07.

**Owner:** Claude - `code/engine/**`

**Status:** ready-for-agent

- [ ] Variants are expanded only when no safe no-change plan completes by the deadline
- [ ] Samples 06, 11 and 21 are reproduced, including their exact cited event ids
- [ ] Only non-fixed events qualify; stop requires stoppable or reducible_or_stoppable, reduce_to requires reducible or reducible_or_stoppable
- [ ] The category must appear in the user's matching willing list and must not be protected
- [ ] Only events belonging to a detected recurring stream qualify
- [ ] The cited event id is the most recent settled occurrence before request_date
- [ ] `reduce_to` targets minimum_allowed_amount exactly and never goes below it
- [ ] Selection picks the smallest sufficient set: fewest changes, then smallest total saving that still passes, then lowest event id
- [ ] At most three changes; stop and reduce never target the same event; stop entries precede reduce_to entries
- [ ] Sample 21 reproduces the case where reducing is chosen over stopping for an event eligible for both

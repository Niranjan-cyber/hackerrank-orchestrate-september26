"""Permitted spending changes and how a set of them is chosen (Ticket 08).

When nothing safe completes the request by `desired_completion_date`, the engine is
allowed to ask the user to change some of their own flexible recurring spending. This
module answers the two questions that involves, and nothing else:

    eligible_changes(...)          what is the user actually willing to change?
    sufficient_change_sets(...)    which *set* of those closes the shortfall, first?

`apply_changes` then rewrites the projected occurrences so the ledger - still the only
safety predicate in the engine - can certify the resulting plan. The decision to look
for changes at all belongs to `plans.candidate_plans`, which owns the pruning rule.

WHAT MAY BE CHANGED (problem_statement.md:170, CONTEXT.md section 10)
All of these must hold, and each is tested alone in `tests/test_spending.py`:

  * the event's `flexibility` is not `fixed`
  * `stop` needs `stoppable` or `reducible_or_stoppable`;
    `reduce_to` needs `reducible` or `reducible_or_stoppable`
  * the category is in the user's matching willing-to-stop / willing-to-reduce list
  * the category is not in `expense_categories_to_protect`
  * the event belongs to a detected recurring stream, and is that stream's most
    recent settled occurrence before `request_date` - the id the samples cite

WHICH SET IS CHOSEN - THE SAMPLES DECIDE THIS, NOT INTUITION
Order by **smallest total saving that still passes**, then fewest changes, then lowest
event ids. "Fewest changes first" is the intuitive reading and it is *wrong*: in sample
21, `stop:event_1816` closes the 31.05 shortfall with one change (saving 47), and
`reduce_to:event_1817` closes it with one change too (saving 76.78), yet the published
answer is the two-change `stop:event_1815|reduce_to:event_1816:23.50` - total 34.50,
the smallest sufficient total there is. The rule is "ask the user to give up as little
as possible", and the preference for reducing over stopping an event eligible for both
falls out of it rather than being a rule of its own.

HOW BIG IS A SAVING - THE CONSERVATIVE READING
One occurrence's worth, measured on the cited event itself:

    stop        the cited event's amount
    reduce_to   the cited event's amount minus its `minimum_allowed_amount`

and a set is *offered* for a full payment when its total covers the whole shortfall
`requested_amount - amount_safe_to_pay` in **one** occurrence. Two consequences, both
deliberate. First, the *selection* never spends the later occurrences inside the 90-day
window: a set that only works because the saving repeats for three months is not
offered, even though `apply_changes` does lower every occurrence and the ledger does
see all of them. Second, for a *variable* stream - where one projected occurrence is a
whole month's forecast category total, not one event - the saving is the fall in that
total, never a claim that the whole month's dining collapses to one meal's minimum.
Both keep the engine from asking for a change on the strength of an effect it cannot
prove. `request_21`'s published 34.50-against-31.05 is exactly this one-occurrence
arithmetic - see `sufficient_change_sets` for why the gate is one-sided on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import combinations
from typing import Iterator, Mapping, Sequence

from .cash import PROJECTED_DEBIT, CashPosition, convert
from .money import ZERO, format_plan_amount
from .recurrence import Stream
from .types import Event, Profile

STOP = "stop"
REDUCE_TO = "reduce_to"

# Which `flexibility` values permit which action. `fixed` appears in neither, which is
# what makes "a fixed event is never changeable" a property of this table rather than
# of a branch somewhere. `validate.py` states the same table a second time on purpose:
# it re-checks the finished row against the dataset rather than against the engine
# value that produced it, so sharing this constant would defeat the point of the check.
FLEXIBILITY_ALLOWS = {
    STOP: frozenset({"stoppable", "reducible_or_stoppable"}),
    REDUCE_TO: frozenset({"reducible", "reducible_or_stoppable"}),
}


@dataclass(frozen=True, slots=True)
class SpendingChange:
    """One permitted change to one recurring commitment.

    `saving` is per projected occurrence, in home currency. `new_amount` is the
    `reduce_to` target in the *event's own* currency, because that is the number the
    output cites and the dataset supplied - so `currency` travels with it, and any
    renderer that prints the target must print this code rather than the home one.
    """

    action: str
    event_id: str
    category: str
    description: str
    saving: Decimal
    new_amount: Decimal | None = None
    currency: str = ""

    def render(self) -> str:
        """The output form: `stop:<event_id>` or `reduce_to:<event_id>:<amount>`."""
        if self.action == STOP:
            return f"{STOP}:{self.event_id}"
        return f"{REDUCE_TO}:{self.event_id}:{format_plan_amount(self.new_amount)}"

    @property
    def sort_key(self) -> tuple[int, str]:
        """Stop before reduce_to, then by event id - the published output order."""
        return (0 if self.action == STOP else 1, self.event_id)


def eligible_changes(
    streams: Sequence[Stream],
    events_by_id: Mapping[str, Event],
    profile: Profile,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
) -> tuple[SpendingChange, ...]:
    """Every (event, action) pair this user has permitted, in a stable order.

    Derived from the detected streams rather than from the raw event rows, because
    "part of a detected recurring stream" is itself one of the eligibility rules and
    the cited id must be the stream's most recent settled occurrence.
    """
    rates = rates or {}
    protected = frozenset(profile.protected_categories)
    willing = {
        STOP: frozenset(profile.stoppable_categories),
        REDUCE_TO: frozenset(profile.reducible_categories),
    }

    found: list[SpendingChange] = []
    for stream in streams:
        # Only spending can be cut. An income stream is not the user's to stop, and
        # stopping it would *lower* the forecast balance anyway.
        if stream.direction != "debit":
            continue
        if stream.category in protected:
            continue
        event = events_by_id.get(stream.latest_event_id)
        if event is None or event.amount is None:
            continue

        amount_home = _home(event.amount, event, profile, rates)
        if amount_home <= ZERO:
            continue

        for action in (STOP, REDUCE_TO):
            if event.flexibility not in FLEXIBILITY_ALLOWS[action]:
                continue
            if stream.category not in willing[action]:
                continue
            if action == STOP:
                saving, target = amount_home, None
            else:
                if event.minimum_allowed_amount is None:
                    # No target, and inventing one would invent a financial fact.
                    continue
                target = event.minimum_allowed_amount
                saving = amount_home - _home(target, event, profile, rates)
                if saving <= ZERO:
                    # The "minimum" is at or above what the user already spends, so
                    # this is not a reduction.
                    continue
            found.append(
                SpendingChange(
                    action=action,
                    event_id=event.event_id,
                    category=stream.category,
                    description=event.description,
                    saving=saving,
                    new_amount=target,
                    currency=event.currency,
                )
            )

    return tuple(sorted(found, key=lambda change: (change.saving, change.sort_key)))


def total_saving(changes: Sequence[SpendingChange]) -> Decimal:
    """One occurrence's worth of saving for a whole set, in home currency."""
    return sum((change.saving for change in changes), ZERO)


def change_sets(
    changes: Sequence[SpendingChange],
    max_changes: int,
) -> Iterator[tuple[SpendingChange, ...]]:
    """Every permitted set, best candidate first.

    Yields rather than returns a winner: the ledger still has to certify the resulting
    plan, so the caller walks this order and takes the first set that survives. Sets
    that stop and reduce the same event are never produced - one commitment cannot be
    changed two ways at once.
    """
    if not changes:
        return

    permitted: list[tuple[SpendingChange, ...]] = []
    for size in range(1, max_changes + 1):
        for combination in combinations(changes, size):
            if len({change.event_id for change in combination}) != size:
                continue
            permitted.append(tuple(sorted(combination, key=lambda c: c.sort_key)))

    permitted.sort(
        key=lambda chosen: (
            total_saving(chosen),  # give up as little...
            len(chosen),  # ...in as few changes...
            [change.sort_key for change in chosen],  # ...deterministically.
        )
    )
    yield from permitted


def sufficient_change_sets(
    changes: Sequence[SpendingChange],
    shortfall: Decimal,
    max_changes: int,
) -> Iterator[tuple[SpendingChange, ...]]:
    """`change_sets`, narrowed to those covering `shortfall` in one occurrence.

    This is the gate for a *full payment on `request_date`*, where "shortfall" is the
    well-defined `requested_amount - amount_safe_to_pay`, and it is a **selection**
    rule rather than a safety one - it decides which set the user is asked for, never
    whether a plan is safe, which stays the ledger's job alone.

    It is deliberately blind to *when* the saving lands, and one-sided on purpose. It
    can refuse a set the ledger would certify - three months of a small subscription
    can lift the window minimum further than one month of it - and refusing means
    asking for a larger change or falling back to a late `wait`, both safe. Sample 21
    is why it exists: its published 34.50-against-31.05 is exactly this single-
    occurrence arithmetic, and without the gate a smaller set certifies on the strength
    of three months of savings and the published answer is not reproduced.
    """
    if shortfall <= ZERO:
        return
    for chosen in change_sets(changes, max_changes):
        if total_saving(chosen) >= shortfall:
            yield chosen


def render_changes(changes: Sequence[SpendingChange]) -> tuple[str, ...]:
    """The `spending_changes_needed` column: stop entries first, then reduce_to."""
    return tuple(
        change.render() for change in sorted(changes, key=lambda c: c.sort_key)
    )


def apply_changes(
    position: CashPosition,
    changes: Sequence[SpendingChange],
) -> CashPosition:
    """The same position with each change applied to its stream's projections.

    Only *projected* occurrences move. An explicit dataset row is a commitment already
    made - a scheduled direct debit is not cancelled by the user deciding to spend
    less next month - and the opening balance is history. So a change can only ever
    lower future inferred spending, which is the only thing it is evidence for.

    A saving larger than the occurrence zeroes it; it never becomes a credit. That
    matters for a variable stream, where the saving is measured on one event and the
    occurrence is a whole month's forecast.
    """
    if not changes:
        return position

    savings: dict[str, Decimal] = {}
    for change in changes:
        savings[change.event_id] = savings.get(change.event_id, ZERO) + change.saving

    kept = []
    for effect in position.effects:
        saving = savings.get(effect.source_event_id or "")
        if saving is None or effect.state != PROJECTED_DEBIT:
            kept.append(effect)
            continue
        reduced = (effect.amount_home or ZERO) - saving
        if reduced <= ZERO:
            continue
        kept.append(replace(effect, amount_home=reduced))

    return replace(position, effects=tuple(kept))


def _home(
    amount: Decimal,
    event: Event,
    profile: Profile,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> Decimal:
    """The event's own currency converted at its cash date, as ticket 04 does it."""
    return convert(
        amount, event.currency, profile.home_currency, event.cash_date, rates
    )

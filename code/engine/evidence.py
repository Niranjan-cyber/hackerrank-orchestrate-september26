"""Evidence application: the authority matrix and conflict precedence (Ticket 10).

Validated facts reach the engine as values. This module is the only place that decides
*how far* one of them may move the forecast, and it is deliberately the narrowest
module in the engine: it reads facts and a `CashPosition`, and returns a `CashPosition`
plus the reason codes naming what it did.

THE GOVERNING RULE IS DIRECTIONAL, NOT PER-TYPE
`docs/contracts/extraction-fact-schema.md` section 4 (D31): evidence may **always**
move the forecast conservatively (less cash available), and may move it optimistically
**only** for a confirmed salary fact passing all five authority conditions. That rule
is implemented once, as a measurement, rather than as a permitted direction hung off
each of the 25 fact types: every amendment is costed against the position it would
replace, and an amendment that leaves *more* cash available is accepted only from an
authorised inflow fact. A scam message promising prize money is therefore inert by
construction rather than by a branch that names it, and so are the cases no branch
would have anticipated - a `salary_temporary` quoted above the permanent stream, a
`recurring_expense_increase` that would actually lower an expense, an
`internal_transfer` whose netting would only remove the debit leg.

WHY READING UNTRUSTED TEXT HERE IS SAFE
`authority_check` looks at `verbatim_quote` for conditional phrasing and for the
digits of `amount`. Both tests can only ever *withhold* authority: a message cannot
talk its way into moving the forecast, only out of it. No other function here reads a
fact's text, and nothing read here reaches an output column - `Reason` carries a code,
an event id and an amount, never prose (see `types.Reason` and ticket 09's trace).

WHAT THIS MODULE DOES NOT DO
It does not validate facts (V1-V13 are the extractor's, `code/extraction/validate.py`),
it does not simulate (ticket 06), and it does not choose a plan (ticket 07). It runs
once per request, after `recurrence.with_projections` and before the simulator, which
is the point the README's dependency audit fixes: evidence amends projected streams,
so ticket 06 is its sibling on ticket 05 rather than its blocker.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from .cash import (
    BLANK_AMOUNT_UNRESOLVED,
    EXPECTED_CREDIT,
    PROJECTED_CREDIT,
    PROJECTED_DEBIT,
    RESERVED_DEBIT,
    UNKNOWN_AMOUNT,
    CashEffect,
    CashPosition,
)
from .cash import convert
from .money import ZERO, money_scale
from .recurrence import monthly_occurrences
from .types import Config, Event, Fact, Profile, Reason, Request

# --- the inflow-increasing set (contract section 4) --------------------------------
# The only fact types that may ever leave more cash available than the dataset alone
# implies. Everything else is conservative-only, whatever it claims.

INFLOW_INCREASE_TYPES = frozenset(
    {"salary_first", "salary_increase", "one_time_arrears"}
)

# Phrases that make a statement conditional, so it cannot license counting income.
# Matched against the fact's own `verbatim_quote`, which V4 has already proved is a
# contiguous span of the source text.
CONDITIONAL_PHRASES = (
    "subject to",
    "pending review",
    "pending final",
    "may change",
    "expected",
    "estimated",
    "projected",
    "if approved",
    "tentative",
    "provisional",
    "not yet confirmed",
    "unconfirmed",
)

# The five condition names, in contract order. `authority_check` returns the subset a
# fact fails, so a reason code can name the cause rather than merely assert a refusal.
CONDITION_FACT_TYPE = "fact_type"
CONDITION_EMPLOYER_SOURCE = "employer_source"
CONDITION_UNCONDITIONAL = "unconditional"
CONDITION_VERIFIED_AMOUNT = "verified_amount"
CONDITION_DATE_IN_WINDOW = "effective_date_in_window"


def authority_check(fact: Fact, request: Request, config: Config) -> tuple[str, ...]:
    """The names of the section-4 conditions `fact` fails. Empty means authorised.

    Applied by the core rather than trusted from the extractor: contract section 9
    assigns the authority matrix to the engine, so a future live adapter that forgets
    its own V9 check cannot widen what the forecast will accept.
    """
    failed: list[str] = []

    if fact.fact_type not in INFLOW_INCREASE_TYPES:
        failed.append(CONDITION_FACT_TYPE)

    # Payroll is the authoritative source for salary. This single condition is what
    # makes all 12 windfall and prize messages financially inert: every one of them is
    # from a financial service, and none from an employer.
    if fact.source_type != "employer":
        failed.append(CONDITION_EMPLOYER_SOURCE)

    if not _is_unconditional(fact.verbatim_quote):
        failed.append(CONDITION_UNCONDITIONAL)

    if (
        fact.amount is None
        or fact.amount <= ZERO
        or not _digits_appear_in(fact.amount, fact.verbatim_quote)
    ):
        failed.append(CONDITION_VERIFIED_AMOUNT)

    if not _date_in_window(fact, request, config):
        failed.append(CONDITION_DATE_IN_WINDOW)

    return tuple(failed)


def _is_unconditional(quote: str) -> bool:
    lowered = quote.lower()
    return not any(phrase in lowered for phrase in CONDITIONAL_PHRASES)


_NON_DIGITS = re.compile(r"[^0-9]")


def _digits_appear_in(amount: Decimal, quote: str) -> bool:
    """Every digit of `amount` appears in `quote`, in order, after separators go.

    The contract's V5 in the extractor tests digit *membership*; this tests the digit
    *sequence*, which is the check that actually catches the documented transposition
    failure mode ($47.3M read as $37.4M - same digits, different number). Grouping
    separators are stripped from the quote rather than interpreted, so `IDR 42.750.000`
    verifies 42750000 without this module ever guessing a locale. A trailing `.00` on
    the parsed amount is not required to appear: `INR 1661` is a faithful quote of
    1661.00.
    """
    digits = _NON_DIGITS.sub("", str(amount)).lstrip("0")
    stripped = _NON_DIGITS.sub("", quote)
    if digits and digits in stripped:
        return True
    # A whole amount carried as `1661.00` quotes as `1661`.
    whole = _NON_DIGITS.sub("", str(amount.to_integral_value())).lstrip("0")
    if amount == amount.to_integral_value() and whole:
        return whole in stripped
    return False


def _date_in_window(fact: Fact, request: Request, config: Config) -> bool:
    """The effective date is present and inside the forecast window.

    An inflow dated before `request_date` is history the opening balance already
    reflects, and one dated past the horizon is outside everything the engine
    forecasts; neither may raise the projected balance. Contract section 5: a
    `salary_first` whose date falls outside the window is *recorded* and projects
    nothing.
    """
    if fact.effective_date is None:
        return False
    horizon = request.request_date + timedelta(days=config.horizon_days)
    return request.request_date <= fact.effective_date <= horizon


# --- reason codes -----------------------------------------------------------------
# One per outcome, so ticket 09's explanation and trace can name what evidence did
# without re-deriving it. An accepted amendment and a refused one are different codes
# on purpose: "the forecast was amended" and "the forecast was left alone because the
# fact had no authority to amend it" are different facts about a request.

EVIDENCE_INCOME_STREAM_CREATED = "EVIDENCE_INCOME_STREAM_CREATED"
EVIDENCE_INCOME_AMOUNT_REPLACED = "EVIDENCE_INCOME_AMOUNT_REPLACED"
EVIDENCE_INCOME_AMOUNT_TEMPORARY = "EVIDENCE_INCOME_AMOUNT_TEMPORARY"
EVIDENCE_ONE_TIME_ARREARS = "EVIDENCE_ONE_TIME_ARREARS"
EVIDENCE_EXPENSE_INCREASED = "EVIDENCE_EXPENSE_INCREASED"
EVIDENCE_OUTFLOW_RESERVED = "EVIDENCE_OUTFLOW_RESERVED"
EVIDENCE_OUTFLOW_ALREADY_RESERVED = "EVIDENCE_OUTFLOW_ALREADY_RESERVED"
EVIDENCE_INTERNAL_TRANSFER_NETTED = "EVIDENCE_INTERNAL_TRANSFER_NETTED"
EVIDENCE_INCOME_STOPPED = "EVIDENCE_INCOME_STOPPED"
EVIDENCE_AUTHORITY_DOWNGRADE = "EVIDENCE_AUTHORITY_DOWNGRADE"
EVIDENCE_TARGET_UNRESOLVED = "EVIDENCE_TARGET_UNRESOLVED"
EVIDENCE_AMOUNT_UNCONVERTIBLE = "EVIDENCE_AMOUNT_UNCONVERTIBLE"
EVIDENCE_FACT_INCOMPLETE = "EVIDENCE_FACT_INCOMPLETE"
EVIDENCE_SUPERSEDED = "EVIDENCE_SUPERSEDED"

# Blank-amount resolution. `BLANK_AMOUNT_UNRESOLVED` is `cash.py`'s own code, reused
# here so the "we still do not know this amount" finding reads the same whether it
# comes from classification or from a failed resolution attempt.
IMAGE_AMOUNT_RESOLVED = "IMAGE_AMOUNT_RESOLVED"
IMPUTED_BLANK_AMOUNT = "IMPUTED_BLANK_AMOUNT"

# The four precedence rules of `problem_statement.md:202-205`, one code each, plus the
# deterministic tiebreak for facts the four rules cannot separate. The code records
# *which rule fired*, which is what makes a conflict auditable rather than merely
# resolved.
CONFLICT_EXPLICIT_AMENDMENT = "CONFLICT_EXPLICIT_AMENDMENT"
CONFLICT_NEWER_SAME_SOURCE = "CONFLICT_NEWER_SAME_SOURCE"
CONFLICT_SETTLED_OVER_ESTIMATE = "CONFLICT_SETTLED_OVER_ESTIMATE"
CONFLICT_SAFER_INTERPRETATION = "CONFLICT_SAFER_INTERPRETATION"
CONFLICT_DETERMINISTIC_TIEBREAK = "CONFLICT_DETERMINISTIC_TIEBREAK"

CONFLICT_CODE_BY_LEVEL = {
    1: CONFLICT_EXPLICIT_AMENDMENT,
    2: CONFLICT_NEWER_SAME_SOURCE,
    3: CONFLICT_SETTLED_OVER_ESTIMATE,
    4: CONFLICT_SAFER_INTERPRETATION,
}
EVIDENCE_CONFIRMED_NO_CHANGE = "EVIDENCE_CONFIRMED_NO_CHANGE"
EVIDENCE_IGNORED_NOT_SETTLED = "EVIDENCE_IGNORED_NOT_SETTLED"
EVIDENCE_IGNORED_SOLICITATION = "EVIDENCE_IGNORED_SOLICITATION"
EVIDENCE_IGNORED_NON_CASH = "EVIDENCE_IGNORED_NON_CASH"
EVIDENCE_ALREADY_IN_BALANCE = "EVIDENCE_ALREADY_IN_BALANCE"


# --- the inert classes ------------------------------------------------------------
# Contract section 1, classes C, D, E and the confirm-only row of the section 4 matrix.
# A table rather than a chain of branches: the engine's whole response to these 14
# types is to say which one it saw and move on, and a table cannot accidentally grow a
# consequence. An unrecognised `fact_type` is not listed here and is not proposed
# either, so a future enum value is inert until someone gives it a consequence.

INERT_REASON_BY_TYPE = {
    # Class C - promised but not arrived. Never a credit until it settles.
    "invoice_approved_pending": EVIDENCE_IGNORED_NOT_SETTLED,
    "refund_pending": EVIDENCE_IGNORED_NOT_SETTLED,
    "gig_payout_pending": EVIDENCE_IGNORED_NOT_SETTLED,
    "prize_claim_processing": EVIDENCE_IGNORED_NOT_SETTLED,
    "bonus_unconfirmed": EVIDENCE_IGNORED_NOT_SETTLED,
    # The advance-fee scam. Inert for the same reason as the rest of class C, which is
    # the point: it needs no rule of its own, and gets none.
    "windfall_solicitation": EVIDENCE_IGNORED_SOLICITATION,
    # Class E - a mark-to-market notice moves no cash in either direction.
    "unrealized_valuation_notice": EVIDENCE_IGNORED_NON_CASH,
    # Class D - already inside `current_available_balance`, so counting it again would
    # double it.
    "windfall_settled": EVIDENCE_ALREADY_IN_BALANCE,
    "investment_sale_settled": EVIDENCE_ALREADY_IN_BALANCE,
    "expense_reimbursement_settled": EVIDENCE_ALREADY_IN_BALANCE,
    # Confirm-only - the fact corroborates a number the engine already has.
    "salary_confirmed_unchanged": EVIDENCE_CONFIRMED_NO_CHANGE,
    "distinct_obligations": EVIDENCE_CONFIRMED_NO_CHANGE,
    # The amount is in the linked receipt image, resolved by `resolve_blank_amounts`
    # before the position is even built; the pointer itself moves nothing.
    "receipt_amount_pointer": EVIDENCE_CONFIRMED_NO_CHANGE,
    # The home-currency amount finalises at settlement, and `cash.convert` already
    # uses the dated rate the dataset supplies. Nothing to amend.
    "foreign_currency_amount_pending": EVIDENCE_CONFIRMED_NO_CHANGE,
}


@dataclass(frozen=True, slots=True)
class AppliedEvidence:
    """The amended position and the reason codes naming every fact's consequence."""

    position: CashPosition
    reasons: tuple[Reason, ...]


# --- conflict precedence ----------------------------------------------------------
# Two facts conflict when they amend the same thing. Everything else is independent,
# including two arrears adjustments (each is a separate payment) and any number of
# inert facts (none of them changes a number, so none of them can contradict another).

# Level 1: an explicit cancellation, settlement or amendment outranks a confirmation,
# an estimate or a pending notice. Membership, not a branch, so adding a fact type
# means deciding once which side of the line it falls on.
EXPLICIT_RECORD_TYPES = frozenset(
    {
        "salary_first",
        "salary_increase",
        "salary_decrease",
        "salary_temporary",
        "income_ended",
        "employment_ended",
        "one_time_arrears",
        "recurring_expense_increase",
        "internal_transfer",
        "windfall_settled",
        "investment_sale_settled",
        "expense_reimbursement_settled",
    }
)

# Level 3: a settled event outranks an estimate or forecast.
SETTLED_RECORD_TYPES = frozenset(
    {"windfall_settled", "investment_sale_settled", "expense_reimbursement_settled"}
)
ESTIMATE_RECORD_TYPES = frozenset(
    {
        "invoice_approved_pending",
        "refund_pending",
        "gig_payout_pending",
        "prize_claim_processing",
        "bonus_unconfirmed",
        "windfall_solicitation",
        "payment_retry_pending",
        "foreign_currency_amount_pending",
        "unrealized_valuation_notice",
    }
)

# Level 4: the financially safer interpretation. Lower is safer.
SAFER_RANK_BY_TYPE = {
    "income_ended": 0,
    "employment_ended": 0,
    "salary_decrease": 0,
    "salary_temporary": 0,
    "recurring_expense_increase": 0,
    "payment_retry_pending": 0,
    "disputed_duplicate_charge": 0,
    "salary_first": 2,
    "salary_increase": 2,
    "one_time_arrears": 2,
    "internal_transfer": 2,
}

CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

# Every fact that speaks about the income stream, amendments and confirmations alike.
# A confirmation belongs here even though it changes nothing: rule 1 ranks an explicit
# amendment *above a confirmation*, which it can only do if the two ever meet.
INCOME_STREAM_FACT_TYPES = frozenset(
    {
        "salary_first",
        "salary_increase",
        "salary_decrease",
        "salary_temporary",
        "salary_confirmed_unchanged",
        "income_ended",
        "employment_ended",
    }
)


@dataclass(frozen=True, slots=True)
class Resolution:
    """Which of a set of conflicting facts applies, and which rule decided it.

    `level` is 1-4 for the four precedence rules, and 0 when the facts were
    indistinguishable under all four and the subject id broke the tie.
    """

    winner: Fact
    superseded: tuple[Fact, ...]
    level: int
    code: str


def conflict_group(fact: Fact) -> tuple[str, ...]:
    """What `fact` amends, as a grouping key. `()` means it conflicts with nothing.

    Only facts that move the same number can contradict each other. Everything inert
    groups to `()`, and so does `one_time_arrears`: a second arrears adjustment is a
    second payment rather than a disagreement about the first.
    """
    if fact.fact_type in INCOME_STREAM_FACT_TYPES:
        return ("income",)
    if fact.fact_type == "recurring_expense_increase":
        return ("expense", fact.related_event_id or "")
    if fact.fact_type in (
        "payment_retry_pending",
        "disputed_duplicate_charge",
        "internal_transfer",
    ):
        return ("outflow", fact.related_event_id or "")
    return ()


def precedence_components(fact: Fact, *, same_source: bool = True) -> tuple[tuple, ...]:
    """The four precedence levels as comparable components, best first.

    `same_source` is the group's property, not the fact's: rule 2 is "a newer record
    from **the same source**", so where a group spans two source types the date
    component is neutralised and the rule is not allowed to decide. Without that, a
    later message from a merchant would outrank an earlier one from payroll on a rule
    that never mentioned two sources.
    """
    level_1 = (0 if fact.fact_type in EXPLICIT_RECORD_TYPES else 1,)
    level_2 = (
        (-fact.effective_date.toordinal(),)
        if same_source and fact.effective_date is not None
        else (0,)
    )
    settled_rank = (
        0
        if fact.fact_type in SETTLED_RECORD_TYPES
        else (2 if fact.fact_type in ESTIMATE_RECORD_TYPES else 1)
    )
    # Rule 3 is a statement of *event state* - a settled event over an estimate or a
    # forecast - so it compares `settled_rank` alone. Extractor `confidence` is not one
    # of the four named rules, so it must not manufacture a "settled over estimate"
    # finding when neither fact is settled; it breaks ties only, after all four rules.
    level_3 = (settled_rank,)
    level_4 = (SAFER_RANK_BY_TYPE.get(fact.fact_type, 1),)
    return (level_1, level_2, level_3, level_4)


def _rank_key(fact: Fact, *, same_source: bool) -> tuple:
    """The four precedence rules, then the deterministic tiebreaks.

    `confidence` sits *below* rule 4 rather than inside it: it is a tiebreak the
    contract never names, so it may order two facts only once the four rules have
    failed to separate them. `subject` is the final tiebreak, so a set of truly
    indistinguishable facts still resolves the same way on every run.
    """
    return precedence_components(fact, same_source=same_source) + (
        (CONFIDENCE_RANK.get(fact.confidence, 3),),
        (fact.subject,),
    )


def resolve_conflict(
    facts: Sequence[Fact], group: tuple[str, ...] = ("income",)
) -> Resolution:
    """The fact that applies, the ones it supersedes, and the rule that decided.

    An ordered comparator rather than a score: the four rules are a priority order, and
    a weighted sum of them could let two weak rules outvote rule 1.
    """
    same_source = len({fact.source_type for fact in facts}) == 1
    ranked = sorted(facts, key=lambda fact: _rank_key(fact, same_source=same_source))
    winner, losers = ranked[0], tuple(ranked[1:])
    if not losers:
        return Resolution(winner=winner, superseded=(), level=0, code="")

    best = precedence_components(winner, same_source=same_source)
    runner_up = precedence_components(losers[0], same_source=same_source)
    for index, (mine, theirs) in enumerate(zip(best, runner_up)):
        if mine != theirs:
            level = index + 1
            return Resolution(
                winner=winner,
                superseded=losers,
                level=level,
                code=CONFLICT_CODE_BY_LEVEL[level],
            )
    return Resolution(
        winner=winner,
        superseded=losers,
        level=0,
        code=CONFLICT_DETERMINISTIC_TIEBREAK,
    )


# --- amendment operations ---------------------------------------------------------
# An amendment is a value, not a closure: the op plus its parameters. That keeps the
# whole of what a fact proposes printable in a trace, and lets the directional guard
# below cost a proposal before anything is committed to.

OP_SET_INCOME_AMOUNT = "set_income_amount"
OP_TEMPORARY_INCOME_AMOUNT = "temporary_income_amount"
OP_STOP_INCOME = "stop_income"
OP_ADD_ONE_OFF_CREDIT = "add_one_off_credit"
OP_RAISE_EXPENSE = "raise_expense"
OP_RESERVE_NAMED_OUTFLOW = "reserve_named_outflow"
OP_NET_OUT_TRANSFER = "net_out_transfer"


@dataclass(frozen=True, slots=True)
class _Amendment:
    op: str
    accepted_code: str
    from_date: date | None = None
    amount: Decimal | None = None
    day_of_month: int | None = None
    cycles: int | None = None
    percent: Decimal | None = None
    target_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Amended:
    """What an op produced: the effects, and the code that names what it did.

    The code is on the result rather than only on the amendment because one op can
    legitimately do two different things - `set_income_amount` replaces a stream the
    user already has and creates one they do not - and the trace has to say which.
    """

    effects: tuple[CashEffect, ...]
    accepted_code: str


@dataclass(frozen=True, slots=True)
class _NoAmendment:
    """The op changed nothing, and the code says why.

    Every refusal is named rather than silent. "The fact names no row I can amend" and
    "the fact had no authority to raise the forecast" are different findings about a
    request, and a developer reading a trace needs to be able to tell them apart.
    """

    code: str


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything an amendment needs that is not on the fact itself."""

    request: Request
    profile: Profile
    config: Config
    horizon_end: date
    events_by_id: Mapping[str, Event]
    rates: Mapping[tuple[str, str, str], Decimal]


def apply_evidence(
    position: CashPosition,
    facts: Sequence[Fact],
    request: Request,
    profile: Profile,
    config: Config,
    events: Sequence[Event] = (),
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
) -> AppliedEvidence:
    """Amend `position` by every fact that has the authority to amend it.

    Called once per request, between `recurrence.with_projections` and the simulator.
    `position.projected_until` bounds every occurrence this creates, so an amended
    position still has inferred recurring spend across its whole window and `simulate`
    has nothing new to refuse.
    """
    context = _Context(
        request=request,
        profile=profile,
        config=config,
        horizon_end=position.projected_until
        or request.request_date + timedelta(days=config.horizon_days),
        events_by_id={event.event_id: event for event in events},
        rates=rates or {},
    )

    reasons: list[Reason] = []
    # Deterministic regardless of how the port happened to order its output.
    ordered = sorted(facts, key=lambda f: (f.fact_type, f.subject))
    applicable, conflict_reasons = _winners(ordered)
    reasons.extend(conflict_reasons)

    effects = position.effects
    for fact in applicable:
        inert = INERT_REASON_BY_TYPE.get(fact.fact_type)
        if inert is not None:
            reasons.append(
                Reason(code=inert, detail=f"{fact.fact_type} {fact.subject}")
            )
            continue
        amendment = _propose(fact, context)
        if amendment is None:
            continue
        effects, applied = _apply(amendment, fact, effects, context)
        reasons.extend(applied)

    return AppliedEvidence(
        position=replace(
            position,
            effects=tuple(sorted(effects, key=lambda e: (e.cash_date, e.event_id))),
        ),
        reasons=tuple(reasons),
    )


def _winners(facts: Sequence[Fact]) -> tuple[tuple[Fact, ...], tuple[Reason, ...]]:
    """Drop every fact a conflicting one supersedes, recording the rule that fired."""
    grouped: dict[tuple[str, ...], list[Fact]] = {}
    independent: list[Fact] = []
    for fact in facts:
        group = conflict_group(fact)
        if group == ():
            independent.append(fact)
        else:
            grouped.setdefault(group, []).append(fact)

    winners = list(independent)
    reasons: list[Reason] = []
    for group in sorted(grouped):
        members = grouped[group]
        resolution = resolve_conflict(members, group)
        winners.append(resolution.winner)
        if not resolution.superseded:
            continue
        reasons.append(
            Reason(
                code=resolution.code,
                detail=(
                    f"{'/'.join(group)}: {resolution.winner.fact_type} "
                    f"{resolution.winner.subject} applies"
                ),
            )
        )
        reasons.extend(
            Reason(
                code=EVIDENCE_SUPERSEDED,
                detail=(
                    f"{loser.fact_type} {loser.subject} superseded by "
                    f"{resolution.winner.subject}"
                ),
            )
            for loser in resolution.superseded
        )
    return tuple(sorted(winners, key=lambda f: (f.fact_type, f.subject))), tuple(
        reasons
    )


def _propose(fact: Fact, context: _Context) -> _Amendment | None:
    """What `fact` asks the forecast to do, or None when it asks for nothing."""
    if fact.fact_type in INERT_REASON_BY_TYPE:
        return None
    if fact.fact_type in ("salary_first", "salary_increase", "salary_decrease"):
        # One op for all three. They differ only in which direction the new amount
        # happens to move the stream, and the guard already measures that - so naming
        # a separate "increase" path would be a second place for the same decision.
        return _Amendment(
            op=OP_SET_INCOME_AMOUNT,
            accepted_code=EVIDENCE_INCOME_AMOUNT_REPLACED,
            from_date=fact.effective_date,
            amount=fact.amount,
            day_of_month=(
                fact.effective_date.day if fact.effective_date is not None else None
            ),
        )
    if fact.fact_type == "salary_temporary":
        return _Amendment(
            op=OP_TEMPORARY_INCOME_AMOUNT,
            accepted_code=EVIDENCE_INCOME_AMOUNT_TEMPORARY,
            from_date=fact.effective_date,
            amount=fact.amount,
            cycles=fact.applies_to_cycles,
        )
    if fact.fact_type == "one_time_arrears":
        return _Amendment(
            op=OP_ADD_ONE_OFF_CREDIT,
            accepted_code=EVIDENCE_ONE_TIME_ARREARS,
            from_date=fact.effective_date,
            amount=fact.amount,
        )
    if fact.fact_type == "recurring_expense_increase":
        return _Amendment(
            op=OP_RAISE_EXPENSE,
            accepted_code=EVIDENCE_EXPENSE_INCREASED,
            # V10 requires an effective date, but an expense that starts sooner is the
            # conservative reading, so a missing one starts at `request_date` rather
            # than refusing an amendment that can only lower the forecast.
            from_date=fact.effective_date or context.request.request_date,
            amount=fact.amount,
            percent=fact.percent_change,
            target_event_id=fact.related_event_id,
        )
    if fact.fact_type in ("payment_retry_pending", "disputed_duplicate_charge"):
        return _Amendment(
            op=OP_RESERVE_NAMED_OUTFLOW,
            accepted_code=EVIDENCE_OUTFLOW_RESERVED,
            target_event_id=fact.related_event_id,
        )
    if fact.fact_type == "internal_transfer":
        return _Amendment(
            op=OP_NET_OUT_TRANSFER,
            accepted_code=EVIDENCE_INTERNAL_TRANSFER_NETTED,
            target_event_id=fact.related_event_id,
        )
    if fact.fact_type in ("income_ended", "employment_ended"):
        return _Amendment(
            op=OP_STOP_INCOME,
            accepted_code=EVIDENCE_INCOME_STOPPED,
            from_date=fact.effective_date,
        )
    return None


def _apply(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> tuple[tuple[CashEffect, ...], tuple[Reason, ...]]:
    """Cost the amendment and accept it only if it is conservative or authorised.

    This is the single enforcement point for D31. A refusal is never an exception: the
    forecast stays as the dataset left it, and the reason code records which authority
    conditions the fact failed.
    """
    amended = _amend(amendment, fact, effects, context)
    if isinstance(amended, _NoAmendment):
        if amended.code == EVIDENCE_AUTHORITY_DOWNGRADE:
            return effects, (_downgrade_reason(fact, context),)
        return effects, (
            Reason(
                code=amended.code,
                amount=fact.amount,
                detail=f"{fact.fact_type} {fact.subject}",
            ),
        )

    if not _is_conservative(effects, amended.effects, context):
        failed = authority_check(fact, context.request, context.config)
        if failed:
            return effects, (_downgrade_reason(fact, context, failed),)

    return amended.effects, (
        Reason(
            code=amended.accepted_code,
            amount=amendment.amount,
            detail=f"{fact.fact_type} {fact.subject}",
        ),
    )


def _downgrade_reason(
    fact: Fact, context: _Context, failed: tuple[str, ...] | None = None
) -> Reason:
    """The fact stands as evidence but changes no number - and why."""
    if failed is None:
        failed = authority_check(fact, context.request, context.config)
    detail = f"{fact.fact_type} {fact.subject}"
    if failed:
        detail = f"{detail}: failed {', '.join(failed)}"
    return Reason(code=EVIDENCE_AUTHORITY_DOWNGRADE, amount=fact.amount, detail=detail)


def _amend(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """What this amendment would produce, or a named refusal when it can do nothing."""
    if amendment.op == OP_SET_INCOME_AMOUNT:
        return _set_income_amount(amendment, fact, effects, context)
    if amendment.op == OP_TEMPORARY_INCOME_AMOUNT:
        return _temporary_income_amount(amendment, fact, effects, context)
    if amendment.op == OP_STOP_INCOME:
        return _stop_income(amendment, effects, context)
    if amendment.op == OP_ADD_ONE_OFF_CREDIT:
        return _add_one_off_credit(amendment, fact, effects, context)
    if amendment.op == OP_RAISE_EXPENSE:
        return _raise_expense(amendment, fact, effects, context)
    if amendment.op == OP_RESERVE_NAMED_OUTFLOW:
        return _reserve_named_outflow(amendment, fact, effects, context)
    if amendment.op == OP_NET_OUT_TRANSFER:
        return _net_out_transfer(amendment, effects, context)
    raise ValueError(f"unknown evidence amendment op {amendment.op!r}")


# Reason codes marking an effect that evidence added as a *one-off*. A one-off is
# income, but it is not part of the recurring stream, so an amendment to the stream
# must leave it alone - otherwise a salary figure arriving in the same message as an
# arrears adjustment overwrites the adjustment with the salary.
ONE_OFF_REASON_CODES = frozenset({EVIDENCE_ONE_TIME_ARREARS})


def _is_stream_income(effect: CashEffect, context: _Context) -> bool:
    """Whether this effect is one occurrence of the user's recurring income stream.

    Both an inferred occurrence and an explicit scheduled payroll row count. A
    `Next confirmed salary` row is the dataset's *forecast* of a payroll that has not
    happened, so a confirmed amendment amends it for the same reason it amends an
    inferred one - precedence rule 1, an explicit amendment over a forecast. Settled
    history is excluded by state: it is already inside the opening balance. A one-off
    credit evidence itself added is excluded by reason code: arrears is owed for work
    already done, so neither a new salary figure nor a stopped contract changes it.
    """
    return (
        effect.state in (PROJECTED_CREDIT, EXPECTED_CREDIT)
        and effect.category in context.config.income_categories
        and effect.reason_code not in ONE_OFF_REASON_CODES
    )


def _set_income_amount(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Put the stream at `amount` from `from_date` onward.

    Replaces the occurrences already forecast where there are any, and creates the
    stream where there are none. Creating is the `salary_first` case contract section 5
    describes - the user has no salary history, so omitting the fact under-forecasts
    income and wrongly returns `not_affordable`. Replacing is what stops the same fact
    from counting a payroll twice for a user whose history *was* detected.
    """
    if amendment.from_date is None or amendment.amount is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    amount_home = _home_amount(fact, amendment.from_date, context)
    if amount_home is None:
        return _NoAmendment(EVIDENCE_AMOUNT_UNCONVERTIBLE)

    targets = tuple(
        effect
        for effect in effects
        if _is_stream_income(effect, context)
        and effect.cash_date >= amendment.from_date
    )
    if targets:
        target_ids = {effect.event_id for effect in targets}
        return _Amended(
            effects=tuple(
                replace(effect, amount_home=amount_home)
                if effect.event_id in target_ids
                else effect
                for effect in effects
            ),
            accepted_code=EVIDENCE_INCOME_AMOUNT_REPLACED,
        )

    if amendment.day_of_month is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    # The first credit lands on the date the employer stated, unrolled: "the confirmed
    # credit date is 2026-01-15" is a fact about that date, not an inference about a
    # day of the month, and rolling it back off a weekend would both contradict the
    # source and count the money a day or two early. Every later occurrence *is*
    # inferred, so those go through the same placement rule ticket 05 projects with.
    stated = amendment.from_date
    occurrences = ()
    if context.request.request_date <= stated <= context.horizon_end:
        occurrences = (stated,)
    occurrences += monthly_occurrences(
        amendment.day_of_month,
        max(stated + timedelta(days=1), context.request.request_date),
        context.horizon_end,
        "credit",
    )
    occurrences = tuple(sorted(set(occurrences)))
    if not occurrences:
        # Nothing lands inside the window, which is condition 5 of the authority check
        # failing: a date outside the forecast is recorded and projects nothing.
        return _NoAmendment(EVIDENCE_AUTHORITY_DOWNGRADE)
    return _Amended(
        effects=effects
        + tuple(
            CashEffect(
                event_id=f"evidence:{fact.subject}:{when.isoformat()}",
                state=PROJECTED_CREDIT,
                cash_date=when,
                amount_home=amount_home,
                reason_code=EVIDENCE_INCOME_STREAM_CREATED,
                category=context.config.income_categories[0],
                # Evidence-created income is not the user's to stop, and ticket 08
                # only ever looks at projected *debits*, so `fixed` here states that
                # this occurrence is nobody's spending change rather than guessing.
                flexibility="fixed",
            )
            for when in occurrences
        ),
        accepted_code=EVIDENCE_INCOME_STREAM_CREATED,
    )


def _temporary_income_amount(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Put the next `cycles` income occurrences at `amount`, then leave the stream alone.

    Only occurrences the stream already forecasts are touched, so a temporary figure can
    never become the permanent amount and can never establish a stream that was not
    there: both would be the fact claiming an authority the matrix gives only to
    `salary_first`, `salary_increase` and `one_time_arrears`. Where no stream was
    detected there is nothing to reduce, and the fact is recorded having changed nothing.
    """
    if amendment.from_date is None or amendment.amount is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    amount_home = _home_amount(fact, amendment.from_date, context)
    if amount_home is None:
        return _NoAmendment(EVIDENCE_AMOUNT_UNCONVERTIBLE)
    cycles = amendment.cycles if amendment.cycles is not None else 1
    if cycles <= 0:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)

    affected = sorted(
        (
            effect
            for effect in effects
            if _is_stream_income(effect, context)
            and effect.cash_date >= amendment.from_date
        ),
        key=lambda effect: (effect.cash_date, effect.event_id),
    )[:cycles]
    if not affected:
        # No stream to reduce, and creating one is an authority this type lacks.
        return _NoAmendment(EVIDENCE_AUTHORITY_DOWNGRADE)
    affected_ids = {effect.event_id for effect in affected}
    return _Amended(
        effects=tuple(
            replace(effect, amount_home=amount_home)
            if effect.event_id in affected_ids
            else effect
            for effect in effects
        ),
        accepted_code=amendment.accepted_code,
    )


def _add_one_off_credit(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Add exactly one credit on the stated date, outside any stream.

    Deliberately *not* placed by `monthly_occurrences`: the date is stated by the
    employer rather than inferred, and leaving a weekend date alone keeps the credit no
    earlier than stated. It carries no `source_event_id`, so it belongs to no stream and
    nothing downstream can repeat it.
    """
    if amendment.from_date is None or amendment.amount is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    if not (context.request.request_date <= amendment.from_date <= context.horizon_end):
        return _NoAmendment(EVIDENCE_AUTHORITY_DOWNGRADE)
    amount_home = _home_amount(fact, amendment.from_date, context)
    if amount_home is None:
        return _NoAmendment(EVIDENCE_AMOUNT_UNCONVERTIBLE)
    return _Amended(
        effects=effects
        + (
            CashEffect(
                event_id=f"evidence:{fact.subject}:{amendment.from_date.isoformat()}",
                state=PROJECTED_CREDIT,
                cash_date=amendment.from_date,
                amount_home=amount_home,
                reason_code=amendment.accepted_code,
                category=context.config.income_categories[0],
                flexibility="fixed",
            ),
        ),
        accepted_code=amendment.accepted_code,
    )


def _raise_expense(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Raise the named recurring outflow from `from_date` onward.

    Two stated forms, because the corpus uses both: an absolute new amount, and a
    percentage. `percent_change` is in **percentage points** - the seven rent messages
    say "increases monthly rent by 12%", so 12 means 12%, and the new occurrence is the
    old one times 1.12, held to the currency's scale like every other modelled amount.

    The target is whatever the fact names in `related_event_id`: every projected
    occurrence of the stream that event belongs to, plus the event itself where it is
    an open reserved debit. A fact that names no row amends nothing - the `Fact` schema
    has no category field, and picking the stream out of the message text would let
    untrusted prose choose what gets changed.
    """
    if amendment.from_date is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    if amendment.amount is None and amendment.percent is None:
        return _NoAmendment(EVIDENCE_FACT_INCOMPLETE)
    if amendment.target_event_id is None:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)

    replacement = (
        None
        if amendment.amount is None
        else _home_amount(fact, amendment.from_date, context)
    )
    if amendment.amount is not None and replacement is None:
        return _NoAmendment(EVIDENCE_AMOUNT_UNCONVERTIBLE)

    # A stated absolute amount is what the *month* costs. A percentage needs no such
    # care - scaling every slot scales the month by the same factor - but replacing
    # every slot with the stated figure would forecast it once per slot, and under the
    # frozen `individual_events` shape a variable category has several slots a month
    # all sharing the cited `source_event_id`.
    stated_shares = (
        {}
        if replacement is None
        else _monthly_shares(
            [e for e in effects if _is_named_outflow(e, amendment, context)],
            replacement,
        )
    )

    amended: list[CashEffect] = []
    hit = False
    for effect in effects:
        if not _is_named_outflow(effect, amendment, context):
            amended.append(effect)
            continue
        hit = True
        current = effect.amount_home or ZERO
        raised = (
            stated_shares[effect.event_id]
            if replacement is not None
            else money_scale(
                current * (Decimal(100) + amendment.percent) / Decimal(100)
            )
        )
        amended.append(replace(effect, amount_home=raised))

    if not hit:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)
    return _Amended(effects=tuple(amended), accepted_code=amendment.accepted_code)


def _monthly_shares(
    matched: list[CashEffect], month_total: Decimal
) -> dict[str, Decimal]:
    """Spread a stated monthly amount over each month's slots, keyed by effect id.

    Proportional to what each slot already carries, so the shape of the month is kept
    and only its total is restated. The rounding remainder goes on the month's last
    slot. A month whose slots price to nothing is split evenly - there is no shape to
    preserve, and dropping the amount would lose the evidence entirely.
    """
    by_month: dict[tuple[int, int], list[CashEffect]] = {}
    for effect in matched:
        key = (effect.cash_date.year, effect.cash_date.month)
        by_month.setdefault(key, []).append(effect)

    shares: dict[str, Decimal] = {}
    for slots in by_month.values():
        ordered = sorted(slots, key=lambda e: (e.cash_date, e.event_id))
        current_total = sum((e.amount_home or ZERO for e in ordered), ZERO)
        allocated = ZERO
        for effect in ordered[:-1]:
            part = (
                money_scale(month_total * (effect.amount_home or ZERO) / current_total)
                if current_total > ZERO
                else money_scale(month_total / Decimal(len(ordered)))
            )
            shares[effect.event_id] = part
            allocated += part
        shares[ordered[-1].event_id] = max(month_total - allocated, ZERO)
    return shares


def _is_named_outflow(
    effect: CashEffect, amendment: _Amendment, context: _Context
) -> bool:
    """Whether this outflow is the one the fact named, on or after its effective date."""
    if effect.state not in (PROJECTED_DEBIT, RESERVED_DEBIT):
        return False
    if amendment.from_date is not None and effect.cash_date < amendment.from_date:
        return False
    return amendment.target_event_id in (effect.source_event_id, effect.event_id)


def _reserve_named_outflow(
    amendment: _Amendment,
    fact: Fact,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Hold the named outflow out of headroom, unless it is already held.

    Corroboration, not a second charge. A disputed duplicate is a `pending` debit that
    ticket 04 already reserved, and a failed debit normally has a linked `scheduled`
    retry row that ticket 04 reserved instead - so the common case is to find the money
    already held and say so. Reserving only fills the gap the dataset leaves: an
    outflow the evidence says is still coming and no open row accounts for.
    """
    target = amendment.target_event_id
    if target is None:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)

    linked_to_target = {
        event.event_id
        for event in context.events_by_id.values()
        if event.linked_event_id == target
    }
    for effect in effects:
        if effect.state != RESERVED_DEBIT:
            continue
        if effect.event_id == target or effect.event_id in linked_to_target:
            return _NoAmendment(EVIDENCE_OUTFLOW_ALREADY_RESERVED)

    event = context.events_by_id.get(target)
    if event is None or event.direction != "debit" or event.amount is None:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)

    when = max(event.cash_date, context.request.request_date)
    if when > context.horizon_end:
        return _NoAmendment(EVIDENCE_CONFIRMED_NO_CHANGE)
    try:
        amount_home = convert(
            event.amount,
            event.currency,
            context.profile.home_currency,
            event.cash_date,
            context.rates,
        )
    except LookupError:
        return _NoAmendment(EVIDENCE_AMOUNT_UNCONVERTIBLE)

    return _Amended(
        effects=effects
        + (
            CashEffect(
                # Its own id, not the dataset row's: the row is still excluded on its
                # own merits, and a duplicate id would make the ledger ambiguous.
                event_id=f"evidence:{fact.subject}:{target}",
                state=RESERVED_DEBIT,
                cash_date=when,
                amount_home=amount_home,
                reason_code=EVIDENCE_OUTFLOW_RESERVED,
                category=event.category,
                flexibility=event.flexibility,
            ),
        ),
        accepted_code=EVIDENCE_OUTFLOW_RESERVED,
    )


def _net_out_transfer(
    amendment: _Amendment,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Exclude both legs of a transfer between the user's own accounts.

    Both legs or neither. Dropping a debit leg alone would hand back headroom the user
    does not have, and dropping a credit leg alone would remove money that did arrive;
    only the matched pair is a de-duplication rather than an invention. The pairing is
    the dataset's own `linked_event_id`, in either direction - never an amount-and-date
    guess, which could match two unrelated rows.

    Where both legs are settled this changes no number, and that is correct: a matched
    transfer already nets to zero inside `current_available_balance`.
    """
    target = amendment.target_event_id
    if target is None:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)
    event = context.events_by_id.get(target)
    if event is None:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)

    # A partner is only a partner if it moves *the other way*. A same-direction
    # `linked_event_id` (the lifecycle links ticket 04 follows, e.g. failed -> retry)
    # is not the matching credit-to-debit pair this op exists to net, and dropping it
    # as if it were would remove a real charge.
    partner_ids: set[str] = set()
    if event.linked_event_id:
        linked = context.events_by_id.get(event.linked_event_id)
        if linked is not None and linked.direction != event.direction:
            partner_ids.add(event.linked_event_id)
    partner_ids |= {
        other.event_id
        for other in context.events_by_id.values()
        if other.linked_event_id == target and other.direction != event.direction
    }
    if not partner_ids:
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)

    legs = {target} | partner_ids
    remaining = tuple(effect for effect in effects if effect.event_id not in legs)
    if len(remaining) == len(effects):
        return _NoAmendment(EVIDENCE_TARGET_UNRESOLVED)
    return _Amended(effects=remaining, accepted_code=amendment.accepted_code)


def _stop_income(
    amendment: _Amendment,
    effects: tuple[CashEffect, ...],
    context: _Context,
) -> _Amended | _NoAmendment:
    """Drop every forecast income occurrence from `from_date` onward.

    Always conservative, so it never needs authority. A missing `from_date` stops the
    stream for the whole window, which is the reading that cannot leave income standing
    on a contract the evidence says has ended.

    `income_ended` and `employment_ended` share this op. The contract calls the second
    the stronger form because it is permanent, but inside a fixed 90-day window nothing
    can restart a stopped stream, so "stopped from the effective date" is the whole of
    both consequences. Payroll already earned before that date stands: it was paid.
    """
    from_date = amendment.from_date or context.request.request_date
    remaining = tuple(
        effect
        for effect in effects
        if not (_is_stream_income(effect, context) and effect.cash_date >= from_date)
    )
    if len(remaining) == len(effects):
        # Nothing was forecast, so there is nothing to stop. The fact still stands as
        # evidence that no income is coming, which the absent stream already says.
        return _NoAmendment(EVIDENCE_CONFIRMED_NO_CHANGE)
    return _Amended(effects=remaining, accepted_code=amendment.accepted_code)


def _home_amount(fact: Fact, on_date: date, context: _Context) -> Decimal | None:
    """`fact.amount` in the user's home currency, or None when it cannot be priced.

    Converted once, at the fact's own effective date, and never at a projected
    occurrence's date: `exchange_rates.csv` supplies one rate per month, so pricing a
    later occurrence would mean inventing a rate the dataset declined to supply - the
    same refusal `cash.convert` already makes for event rows. A fact that cannot be
    priced amends nothing.
    """
    if fact.amount is None:
        return None
    home = context.profile.home_currency
    if fact.currency is None or fact.currency == home:
        return fact.amount
    return _convert_or_none(fact.amount, fact.currency, home, on_date, context.rates)


# --- the directional guard --------------------------------------------------------


def _is_conservative(
    before: tuple[CashEffect, ...],
    after: tuple[CashEffect, ...],
    context: _Context,
) -> bool:
    """True when `after` leaves no more cash available than `before`, at every date.

    Pointwise over the window rather than on a single summary figure, because both
    graded capacity columns read the balance series rather than its total: a credit
    added late in the window can leave `amount_safe_to_pay` untouched and still pull
    `earliest_date_for_full_payment` forward. Requiring the whole series to be no
    higher is the only comparison that makes both safe.

    `UNKNOWN_AMOUNT` effects are skipped. They are unpriced by definition, so they
    cannot be compared; the position still carries them, and `simulate` still refuses
    to certify a window containing one.
    """
    for when in sorted(
        {
            effect.cash_date
            for effect in before + after
            if effect.cash_date <= context.horizon_end
        }
    ):
        if _movement_by(after, when) > _movement_by(before, when):
            return False
    return True


def _movement_by(effects: tuple[CashEffect, ...], when: date) -> Decimal:
    """Cumulative movement on or before `when`, ignoring the opening balance.

    The opening balance is identical on both sides of every comparison, so leaving it
    out keeps this a pure measure of what the amendment changed.
    """
    return sum(
        (
            effect.signed_amount
            for effect in effects
            if effect.cash_date <= when and effect.state != UNKNOWN_AMOUNT
        ),
        ZERO,
    )


# --- blank amounts ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BlankAmounts:
    """Resolved amounts for events whose CSV `amount` was blank.

    `overrides` maps `event_id` to a home-currency amount and goes straight into
    `cash.cash_position(..., amount_overrides=...)`, which is the only seam through
    which evidence reaches classification. An event missing from the map is one the
    engine still cannot price: it stays `UNKNOWN_AMOUNT`, and the simulator degrades
    that request conservatively. **Zero is never a value in this map.**
    """

    overrides: dict[str, Decimal]
    reasons: tuple[Reason, ...]


def resolve_blank_amounts(
    events: Sequence[Event],
    facts: Sequence[Fact],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal] | None = None,
) -> BlankAmounts:
    """Price every blank-amount event: the receipt image first, then imputation.

    Runs *before* the cash position is built, because a blank amount changes how its
    own row is classified. Two steps, in the order the contract fixes (section 6):

      1. the amount read off the linked receipt image, if extraction produced one;
      2. failing that, the median of the same user's settled same-category events
        inside the recurrence lookback window.

    And one rule over both: **a blank amount never becomes zero.** Where neither step
    produces a positive figure the event is left unpriced, which costs the request its
    forecast but never understates an outflow.
    """
    rates = rates or {}
    image_amounts = {
        fact.related_event_id: fact
        for fact in sorted(facts, key=lambda f: f.subject)
        if fact.fact_type == "image_amount" and fact.related_event_id is not None
    }

    overrides: dict[str, Decimal] = {}
    reasons: list[Reason] = []
    for event in sorted(
        (event for event in events if event.amount is None),
        key=lambda event: event.event_id,
    ):
        resolved, code = _price_blank(
            event,
            image_amounts.get(event.event_id),
            events,
            request,
            profile,
            config,
            rates,
        )
        if resolved is not None:
            overrides[event.event_id] = resolved
        reasons.append(Reason(code=code, event_id=event.event_id, amount=resolved))

    return BlankAmounts(overrides=overrides, reasons=tuple(reasons))


def _price_blank(
    event: Event,
    image: Fact | None,
    events: Sequence[Event],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> tuple[Decimal | None, str]:
    """One blank event's home-currency amount and the code naming where it came from."""
    if image is not None and image.amount is not None:
        converted = _convert_or_none(
            image.amount,
            image.currency or event.currency,
            profile.home_currency,
            event.cash_date,
            rates,
        )
        if converted is not None and converted > ZERO:
            return converted, IMAGE_AMOUNT_RESOLVED

    imputed = _impute_amount(event, events, request, profile, config, rates)
    if imputed is not None and imputed > ZERO:
        return imputed, IMPUTED_BLANK_AMOUNT

    return None, BLANK_AMOUNT_UNRESOLVED


# The named ways to impute a blank amount, swept by ticket 14 through
# `Config.blank_amount_estimator`. `none` declines to impute, which leaves the row
# unpriced and the request conservatively degraded - never zero.
BLANK_AMOUNT_ESTIMATORS = {
    "median_same_category": lambda amounts: statistics.median(amounts),
    "mean_same_category": lambda amounts: sum(amounts, ZERO) / len(amounts),
    "none": lambda amounts: None,
}


def _impute_amount(
    event: Event,
    events: Sequence[Event],
    request: Request,
    profile: Profile,
    config: Config,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> Decimal | None:
    """The imputed amount from the settled same-category history in the lookback window.

    The default is the median rather than the mean, because one unusually large settled
    bill should not drag every unpriced one up with it. The same-category restriction
    holds whichever estimator is chosen: a blank telecom bill says nothing about the
    user's groceries. Deterministic by construction - the same rows, in the same order,
    give the same figure.
    """
    estimator = BLANK_AMOUNT_ESTIMATORS.get(config.blank_amount_estimator)
    if estimator is None:
        raise ValueError(
            f"unknown blank_amount_estimator {config.blank_amount_estimator!r}; "
            f"expected one of {sorted(BLANK_AMOUNT_ESTIMATORS)}"
        )
    lookback_start = request.request_date - timedelta(days=config.lookback_days)
    amounts: list[Decimal] = []
    for other in sorted(events, key=lambda e: (e.cash_date, e.event_id)):
        if other.event_id == event.event_id or other.amount is None:
            continue
        if other.status != "settled" or other.direction != event.direction:
            continue
        if other.category != event.category:
            continue
        if not (lookback_start <= other.cash_date < request.request_date):
            continue
        converted = _convert_or_none(
            other.amount,
            other.currency,
            profile.home_currency,
            other.cash_date,
            rates,
        )
        if converted is not None:
            amounts.append(converted)
    if not amounts:
        return None
    estimated = estimator(amounts)
    return None if estimated is None else money_scale(estimated)


def _convert_or_none(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    on_date: date,
    rates: Mapping[tuple[str, str, str], Decimal],
) -> Decimal | None:
    """`cash.convert`, with its refusal turned into a None the caller can fall back on."""
    try:
        return convert(amount, from_currency, to_currency, on_date, rates)
    except LookupError:
        return None

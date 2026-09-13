"""Value types for the Buy or Wait? engine.

Every type here is a frozen value. The core manipulates these and nothing else -
no file handles, no clients, no mutable shared state.

`Fact` lives here rather than in `code/extraction/` on purpose: it is consumed by the
core, so under dependency inversion the core owns the type and adapters depend on it,
never the reverse. See docs/contracts/extraction-fact-schema.md section 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# --- enums, as plain frozensets: the challenge's allowed values -------------------

AFFORDABILITY_STATUSES = frozenset(
    {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
)

PAYMENT_METHODS = frozenset(
    {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
)

CURRENCIES = frozenset({"INR", "IDR", "EUR", "USD", "ZAR"})

OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)


# --- dataset rows ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...]
    protected_categories: tuple[str, ...]
    reducible_categories: tuple[str, ...]
    stoppable_categories: tuple[str, ...]
    payment_methods_considered: tuple[str, ...]
    max_installment_months: int | None


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Decimal | None  # None means blank in the CSV - NEVER treat as zero
    currency: str
    event_date: date
    settlement_date: date | None
    status: str
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: Decimal | None

    @property
    def cash_date(self) -> date:
        """The date cash actually moves: settlement if present, else the event date."""
        return self.settlement_date or self.event_date


@dataclass(frozen=True, slots=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True, slots=True)
class Dataset:
    """The whole parsed dataset, already loaded. The core receives this as a value."""

    requests: tuple[Request, ...]
    profiles: dict[str, Profile]
    events_by_user: dict[str, tuple[Event, ...]]
    options_by_request: dict[str, tuple[PaymentOption, ...]]
    rates: dict[tuple[str, str, str], Decimal]  # (rate_date_iso, from, to) -> rate


# --- evidence ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fact:
    """One validated piece of evidence. See the extraction contract for field rules."""

    fact_type: str
    subject: str
    user_id: str
    request_id: str | None = None
    related_event_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    percent_change: Decimal | None = None
    effective_date: date | None = None
    applies_to_cycles: int | None = None
    verbatim_quote: str = ""
    verbatim_amount_string: str | None = None
    source_type: str = ""
    source_language: str = "en"
    confidence: str = "high"
    extractor: dict = field(default_factory=dict)


# --- provenance -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reason:
    """Why a derived number is what it is. Rendered into decision_explanation."""

    code: str
    event_id: str | None = None
    amount: Decimal | None = None
    # Only set when `amount` is NOT in the user's home currency - a `reduce_to` target
    # is quoted in the commitment's own currency, so a renderer that assumed home would
    # print a foreign figure under the home code. Blank means home currency.
    currency: str = ""
    detail: str = ""


# --- configuration ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    """Injected knobs. No engine module may inline any of them as a literal.

    **The seven calibration parameters are FROZEN as of 2026-09-13** (ticket 14). They
    are `lookback_days`, `min_occurrences`, `variable_spend_estimator`,
    `project_income_beyond_confirmed`, `variable_spend_shape`,
    `variable_spend_placement` and `same_day_ordering`. Their values were settled by
    measurement against the 25 solved samples and are not to be touched again: the
    evidence, the sweep and the freeze are recorded in
    `docs/investigation/calibration.md`, and `engine/tests/test_frozen_calibration.py`
    fails if one moves. The remaining fields were never in the block's scope.
    """

    # simulation
    horizon_days: int = 90
    # FROZEN (ticket 14). The samples do not separate this from `credits_debits_payment`
    # on any discrete cell - only five `amount_safe_to_pay` values move - but the
    # published figure is closer under debits-first on four of those five, and moving
    # the payment to the front of the day costs six `earliest_date` matches outright.
    same_day_ordering: str = "debits_credits_payment"

    # recurrence detection. `lookback_days` and `min_occurrences` are FROZEN (ticket
    # 14) and move together: a 90-day window holds at most three monthly occurrences,
    # so requiring three is nearly unsatisfiable inside it and requiring two is the
    # matching threshold. The pair is worth +11 discrete matches on the 25 samples
    # against the 180/3 they replace.
    lookback_days: int = 90
    min_occurrences: int = 2
    amount_tolerance_pct: Decimal = Decimal("0.05")
    amount_tolerance_abs: Decimal = Decimal("5")
    description_similarity: Decimal = Decimal("0.85")
    # FROZEN (ticket 14) at the shipped default. Every estimator ties at the frozen
    # optimum, so the score does not choose; `max_median3_mean6` is kept because it is
    # the most conservative of the four - it never forecasts less than either of the
    # two it is the maximum of.
    variable_spend_estimator: str = "max_median3_mean6"
    # FROZEN (ticket 14). Not a live choice: switching it off costs 25 discrete matches
    # on its own, more than every other axis combined can recover.
    project_income_beyond_confirmed: bool = True
    # How a variable-spend month is shaped once its total is estimated, ticket 05/14.
    # `monthly_total` puts the whole month on one day; `individual_events` splits the
    # *same* total across the observed days-of-month in proportion to their historical
    # share, so the two shapes differ only in placement within the month.
    # FROZEN (ticket 14) at `individual_events`, the single largest win in the block
    # (+8 discrete on its own). One lump on one day forecast whole weeks of zero
    # variable spend, which mis-timed both the squeeze and the saving that answers it.
    variable_spend_shape: str = "individual_events"
    # Which observed day-of-month a `monthly_total` lands on, ticket 05/14. `earliest`
    # is conservative for the debit itself but makes a matching *saving* arrive as late
    # as it can (CONTEXT.md section 9, sample 11), which is why ticket 14 sweeps it.
    # FROZEN (ticket 14) at the shipped default. Under `individual_events` it applies
    # only to the degenerate all-zero-history case, and all three values tie.
    variable_spend_placement: str = "earliest"

    # plan eligibility, ticket 07/14. Both encode how hard a gate
    # `desired_completion_date` is. Never in the calibration block's scope: ticket 14
    # was limited to the seven axes named in this class's docstring, and these two
    # encode a *reading of the contract*, which is not something a sample score should
    # settle. `problem_statement.md:180` states the plan "must
    # complete the request by desired_completion_date", but criterion 1 of the ranking
    # at line 191 is *also* "complete the full request by desired_completion_date" -
    # which is dead weight if completion is a hard gate on every method. CONTEXT.md
    # section 8 resolves it the only way that keeps both lines live: the two methods
    # the problem statement gates explicitly stay gated, and `wait` is generated even
    # when it lands late so that level 1 has something to decide.
    installments_must_complete_by_deadline: bool = True
    wait_must_complete_by_deadline: bool = False

    # spending changes, ticket 08/14. Three is the contract's ceiling
    # (`problem_statement.md:171`), not a preference - the selection rule already
    # prefers the smallest sufficient set, so lowering this only forbids answers.
    max_spending_changes: int = 3

    # recurrence boundaries / calibration, ticket 05/14
    weekly_date_tolerance_days: int = 2
    biweekly_date_tolerance_days: int = 3
    monthly_date_tolerance_days: int = 5
    quarterly_date_tolerance_days: int = 7
    annual_date_tolerance_days: int = 10
    variable_spend_categories: tuple[str, ...] = ("groceries", "transport", "dining")
    # Which event categories are income. Evidence (ticket 10) amends "the projected
    # income stream", and this names it rather than leaving a literal in that module.
    # `salary` and `windfall` are the only two credit categories in the data, and a
    # windfall is never a stream, so `salary` alone is the income stream.
    income_categories: tuple[str, ...] = ("salary",)
    # How a blank `amount` is imputed when no receipt image resolved it, ticket 10.
    # Never in the calibration block's scope - ticket 14 was limited to the seven axes
    # named in this class's docstring, and this one is settled by the problem
    # statement's "never zero" rule rather than by a sample score. `none` leaves the
    # row unpriced, which degrades that request conservatively - it is never a way of
    # treating a blank amount as zero.
    blank_amount_estimator: str = "median_same_category"
    protected_two_occurrence_project: bool = True
    description_prefix_tokens: int = 3


# --- output -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputRow:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: tuple[tuple[date, Decimal], ...]  # () means "none"
    earliest_date_for_full_payment: date | None
    spending_changes_needed: tuple[str, ...]  # () means "none"
    decision_explanation: str
    reasons: tuple[Reason, ...] = field(default_factory=tuple)

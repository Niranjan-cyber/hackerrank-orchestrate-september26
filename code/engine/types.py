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
    detail: str = ""


# --- configuration ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    """Injected knobs. Everything the calibration block may sweep lives here.

    These are NOT frozen decisions - ticket 14 settles the values. No engine module
    may inline any of them as a literal.
    """

    # simulation
    horizon_days: int = 90
    same_day_ordering: str = "debits_credits_payment"  # unfrozen: ticket 06/14

    # recurrence detection - all unfrozen, ticket 05/14
    lookback_days: int = 180
    min_occurrences: int = 3
    amount_tolerance_pct: Decimal = Decimal("0.05")
    amount_tolerance_abs: Decimal = Decimal("5")
    description_similarity: Decimal = Decimal("0.85")
    variable_spend_estimator: str = "max_median3_mean6"
    project_income_beyond_confirmed: bool = True

    # plan eligibility, ticket 07/14. Both encode how hard a gate
    # `desired_completion_date` is. `problem_statement.md:180` states the plan "must
    # complete the request by desired_completion_date", but criterion 1 of the ranking
    # at line 191 is *also* "complete the full request by desired_completion_date" -
    # which is dead weight if completion is a hard gate on every method. CONTEXT.md
    # section 8 resolves it the only way that keeps both lines live: the two methods
    # the problem statement gates explicitly stay gated, and `wait` is generated even
    # when it lands late so that level 1 has something to decide. Unfrozen: ticket 14.
    installments_must_complete_by_deadline: bool = True
    wait_must_complete_by_deadline: bool = False

    # recurrence boundaries / calibration, ticket 05/14
    weekly_date_tolerance_days: int = 2
    biweekly_date_tolerance_days: int = 3
    monthly_date_tolerance_days: int = 5
    quarterly_date_tolerance_days: int = 7
    annual_date_tolerance_days: int = 10
    variable_spend_categories: tuple[str, ...] = ("groceries", "transport", "dining")
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

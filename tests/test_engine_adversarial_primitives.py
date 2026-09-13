"""Focused primitives: the four adversarial traps (Ticket 13).

A windfall solicitation changes no output; a non-employer income claim is downgraded; a
digit mismatch is rejected; an over-length installment option is pruned. Every
assertion is on an observable result - a published pipeline row, a returned fact type,
a violation list, a pruned option - never on whether a call was made.

Two of the traps cross the extraction boundary on purpose: V5 (digit presence) and V9
(authority downgrade) live in `extraction/validate.py`, while the engine re-checks
authority in `evidence.authority_check`. Both ends are pinned, because either alone
would be a single point of failure for a scam message.

Run: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE.parent / "code", _HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from engine.evidence import (  # noqa: E402
    EVIDENCE_IGNORED_SOLICITATION,
    authority_check,
)
from engine.pipeline import run_pipeline  # noqa: E402
from engine.plans import screen_options  # noqa: E402
from engine.types import Config, Fact  # noqa: E402
from extraction.validate import validate_fact  # noqa: E402

from primitive_fixtures import (  # noqa: E402
    build_dataset,
    make_payment_option,
    make_profile,
    make_request,
)

PUBLISHED_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def published(row):
    return tuple(getattr(row, column) for column in PUBLISHED_COLUMNS)


class WindfallSolicitationIsInertTest(unittest.TestCase):
    def test_a_windfall_solicitation_changes_no_published_output(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
        )
        profile = make_profile(
            balance="10000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        dataset = build_dataset(request=request, profile=profile)
        config = Config()
        solicitation = Fact(
            fact_type="windfall_solicitation",
            subject="message_scam",
            user_id=profile.user_id,
            amount=Decimal("9000000"),
            currency="INR",
            verbatim_quote="You have won INR 9,000,000. Send a fee to claim it.",
            source_type="financial_service",
            effective_date=date(2025, 2, 10),
        )

        baseline = run_pipeline(dataset, (), config)[0]
        tainted = run_pipeline(dataset, (solicitation,), config)[0]

        self.assertEqual(published(tainted), published(baseline))
        self.assertIn(EVIDENCE_IGNORED_SOLICITATION, {r.code for r in tainted.reasons})


class NonEmployerIncomeDowngradeTest(unittest.TestCase):
    def _fact(self, source_type):
        return Fact(
            fact_type="salary_first",
            subject="message_salary",
            user_id="user_test",
            amount=Decimal("5000"),
            currency="INR",
            verbatim_quote="Your salary is INR 5000.",
            source_type=source_type,
            effective_date=date(2025, 2, 10),
        )

    def test_the_engine_downgrades_a_non_employer_income_claim(self):
        request = make_request(request_date="2025-02-01")
        failed = authority_check(self._fact("financial_service"), request, Config())
        self.assertIn("employer_source", failed)

    def test_an_employer_salary_passes_the_same_authority_check(self):
        request = make_request(request_date="2025-02-01")
        self.assertEqual(authority_check(self._fact("employer"), request, Config()), ())

    def test_the_extractor_records_the_v9_authority_failure(self):
        request = make_request(request_date="2025-02-01")
        profile = make_profile()
        dataset = build_dataset(request=request, profile=profile)
        messages = {
            "message_salary": {
                "message_id": "message_salary",
                "message_text": "Your salary is INR 5000.",
            }
        }

        valid, violations = validate_fact(
            self._fact("financial_service"),
            dataset,
            messages,
            {},
            {},
            {request.user_id: request},
        )

        self.assertIsNone(valid)
        self.assertIn("V9", {v.rule for v in violations})
        self.assertIn(
            "source_type",
            next(v.detail for v in violations if v.rule == "V9"),
        )

    def test_a_non_employer_salary_fact_never_reaches_the_engine_as_income(self):
        """End to end: the extractor drops it, and the engine would refuse it anyway."""
        request = make_request(
            request_date="2025-02-01",
            requested_amount="5000",
            desired_completion_date="2025-04-01",
        )
        profile = make_profile(
            balance="1000",
            minimum="2000",
            payment_methods_considered=("full_payment",),
        )
        dataset = build_dataset(request=request, profile=profile)
        config = Config()
        fact = self._fact("financial_service")

        baseline = run_pipeline(dataset, (), config)[0]
        tainted = run_pipeline(dataset, (fact,), config)[0]

        # Either route leaves the published row unwidened.
        self.assertEqual(published(tainted), published(baseline))


class DigitMismatchRejectedTest(unittest.TestCase):
    def test_the_extractor_rejects_an_amount_whose_digit_is_absent(self):
        request = make_request(request_date="2025-02-01")
        profile = make_profile()
        dataset = build_dataset(request=request, profile=profile)
        messages = {
            "message_digits": {
                "message_id": "message_digits",
                "message_text": "A prize of INR 1234 was credited.",
            }
        }
        fact = Fact(
            fact_type="windfall_settled",
            subject="message_digits",
            user_id="user_test",
            amount=Decimal("5555"),
            currency="INR",
            verbatim_quote="A prize of INR 1234",
            source_type="bank",
        )

        valid, violations = validate_fact(
            fact, dataset, messages, {}, {}, {request.user_id: request}
        )

        self.assertIsNone(valid)
        self.assertIn("V5", {v.rule for v in violations})

    def test_the_engine_rejects_a_transposed_amount_even_when_digits_recur(self):
        """$47.3M misread as $37.4M: every digit is present, the sequence is not."""
        request = make_request(request_date="2025-02-01")
        transposed = Fact(
            fact_type="salary_increase",
            subject="message_money",
            user_id="user_test",
            amount=Decimal("37400000"),
            currency="INR",
            verbatim_quote="Your salary is now INR 47,300,000.",
            source_type="employer",
            effective_date=date(2025, 2, 10),
        )
        correct = Fact(
            fact_type="salary_increase",
            subject="message_money",
            user_id="user_test",
            amount=Decimal("47300000"),
            currency="INR",
            verbatim_quote="Your salary is now INR 47,300,000.",
            source_type="employer",
            effective_date=date(2025, 2, 10),
        )

        self.assertIn("verified_amount", authority_check(transposed, request, Config()))
        self.assertEqual(authority_check(correct, request, Config()), ())


class OverLengthInstallmentPrunedTest(unittest.TestCase):
    def test_an_over_length_option_is_pruned_and_never_offered(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-06-01",
        )
        profile = make_profile(
            balance="100000",
            minimum="2000",
            payment_methods_considered=("installments",),
            max_installment_months=3,
        )
        over_length = make_payment_option(
            payment_option_id="payment_option_99",
            payment_amount="750",
            number_of_payments=4,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        screened, reasons = screen_options(request, profile, (over_length,), Config())

        self.assertEqual(screened, ())
        self.assertIn("OPTION_EXCEEDS_MAX_INSTALLMENTS", {r.code for r in reasons})

    def test_the_same_installment_count_is_offered_when_it_is_within_the_maximum(self):
        request = make_request(
            request_date="2025-02-01",
            requested_amount="3000",
            desired_completion_date="2025-06-01",
        )
        profile = make_profile(
            balance="100000",
            minimum="2000",
            payment_methods_considered=("installments",),
            max_installment_months=4,
        )
        within = make_payment_option(
            payment_option_id="payment_option_99",
            payment_amount="750",
            number_of_payments=4,
            first_payment_date="2025-02-01",
            payment_frequency_days=30,
        )

        screened, _ = screen_options(request, profile, (within,), Config())

        self.assertEqual(
            [entry.option.payment_option_id for entry in screened],
            ["payment_option_99"],
        )


if __name__ == "__main__":
    unittest.main()

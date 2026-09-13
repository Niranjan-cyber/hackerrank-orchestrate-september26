"""Tests for currency-aware parsing of printed amounts from receipt images.

The corpus is the arbiter here, not a locale assumption. `image_01` prints an IDR
pay slip as ``IDR 4,365,000`` - comma grouping - so a strict "IDR uses '.' as the
thousands separator" rule would misread it by a factor of a million. The parser reads
separator *structure* first and consults the row's currency only for the one genuinely
ambiguous shape: a single separator followed by exactly three digits.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

from extraction.amounts import (  # noqa: E402
    AmountParseError,
    digit_in_quote,
    parse_printed_amount,
)


class TestParsePrintedAmount(unittest.TestCase):
    def test_idr_dot_grouping(self):
        """The contract's own example: Rp 12.500.000 is twelve and a half million."""
        self.assertEqual(
            parse_printed_amount("Rp 12.500.000", "IDR"), Decimal("12500000")
        )

    def test_idr_comma_grouping_as_actually_printed(self):
        """image_01 is an Indonesian pay slip printed with comma grouping."""
        self.assertEqual(
            parse_printed_amount("IDR 4,365,000", "IDR"), Decimal("4365000")
        )
        self.assertEqual(parse_printed_amount("4,365,000", "IDR"), Decimal("4365000"))

    def test_indian_lakh_grouping(self):
        self.assertEqual(
            parse_printed_amount("1,00,000.00", "INR"), Decimal("100000.00")
        )
        self.assertEqual(parse_printed_amount("1,00,000", "INR"), Decimal("100000"))

    def test_two_decimal_currencies(self):
        self.assertEqual(parse_printed_amount("$33.50", "USD"), Decimal("33.50"))
        self.assertEqual(parse_printed_amount("704.05", "INR"), Decimal("704.05"))
        self.assertEqual(parse_printed_amount("79,679.26", "INR"), Decimal("79679.26"))
        self.assertEqual(parse_printed_amount("15,339.00", "INR"), Decimal("15339.00"))
        self.assertEqual(parse_printed_amount("41272.0", "INR"), Decimal("41272.0"))

    def test_single_comma_with_three_digits_is_grouping_for_inr(self):
        self.assertEqual(parse_printed_amount("2,298", "INR"), Decimal("2298"))
        self.assertEqual(parse_printed_amount("1,599", "INR"), Decimal("1599"))

    def test_currency_symbol_and_code_are_stripped(self):
        self.assertEqual(parse_printed_amount("\u20b9 1,599", "INR"), Decimal("1599"))
        self.assertEqual(parse_printed_amount("Rs. 723.00", "INR"), Decimal("723.00"))
        self.assertEqual(parse_printed_amount("1,050.00", "INR"), Decimal("1050.00"))

    def test_no_separator_is_an_integer(self):
        self.assertEqual(parse_printed_amount("2298", "INR"), Decimal("2298"))
        self.assertEqual(parse_printed_amount("393", "INR"), Decimal("393"))

    def test_european_both_separators(self):
        """'1.299,00' is unambiguous once structure decides: rightmost is the decimal."""
        self.assertEqual(parse_printed_amount("1.299,00", "EUR"), Decimal("1299.00"))

    def test_single_three_digit_group_consults_the_currency(self):
        """The one ambiguous shape: 1.234 is 1234 for rupiah, 1.234 for dollars."""
        self.assertEqual(parse_printed_amount("1.234", "IDR"), Decimal("1234"))
        self.assertEqual(parse_printed_amount("1.234", "USD"), Decimal("1.234"))
        self.assertEqual(parse_printed_amount("1,234", "USD"), Decimal("1234"))
        self.assertEqual(parse_printed_amount("1,234", "IDR"), Decimal("1.234"))

    def test_unknown_currency_falls_back_to_english_conventions(self):
        self.assertEqual(parse_printed_amount("1,234", None), Decimal("1234"))
        self.assertEqual(parse_printed_amount("1.234", None), Decimal("1.234"))

    def test_zero_parses_but_is_zero(self):
        self.assertEqual(parse_printed_amount("0", "INR"), Decimal("0"))
        self.assertEqual(parse_printed_amount("0.00", "USD"), Decimal("0.00"))

    def test_blank_and_non_numeric_raise(self):
        for bad in ("", "   ", "no amount", "N/A", "\u20b9"):
            with self.subTest(bad=bad), self.assertRaises(AmountParseError):
                parse_printed_amount(bad, "INR")

    def test_negative_sign_is_rejected_not_silently_made_positive(self):
        for bad in ("-100", "\u2212123.45"):
            with self.subTest(bad=bad), self.assertRaises(AmountParseError):
                parse_printed_amount(bad, "INR")

    def test_more_than_two_fraction_digits_raise(self):
        with self.assertRaises(AmountParseError):
            parse_printed_amount("1.2345", "USD")


class TestDigitInQuote(unittest.TestCase):
    def test_all_digits_present(self):
        self.assertTrue(digit_in_quote(Decimal("4365000"), "IDR 4,365,000"))

    def test_a_missing_digit_fails(self):
        self.assertFalse(digit_in_quote(Decimal("4365000"), "IDR 4,365,222"))

    def test_transposed_digits_can_still_pass_by_digit_set(self):
        """The contract checks digit presence, not order (V5, section 8)."""
        self.assertTrue(digit_in_quote(Decimal("12500"), "12.500"))

    def test_none_amount_passes_vacuously(self):
        self.assertTrue(digit_in_quote(None, "anything"))


if __name__ == "__main__":
    unittest.main()

from decimal import Decimal

from django.test import SimpleTestCase

from campaigns.templatetags.duree_filters import format_number_fr


class FormatNumberFrTests(SimpleTestCase):
    def test_formats_large_amounts_without_unnecessary_decimals(self):
        self.assertEqual(format_number_fr(Decimal('5000.00')), '5 000')
        self.assertEqual(
            format_number_fr(Decimal('154824205451.25')),
            '154 824 205 451,25',
        )

    def test_preserves_meaningful_decimal_places(self):
        self.assertEqual(format_number_fr(Decimal('45.50')), '45,5')
        self.assertEqual(format_number_fr(Decimal('0.25')), '0,25')
        self.assertEqual(format_number_fr(Decimal('-0.25')), '-0,25')

    def test_formats_zero_and_missing_values(self):
        self.assertEqual(format_number_fr(Decimal('0.00')), '0')
        self.assertEqual(format_number_fr(None), '')

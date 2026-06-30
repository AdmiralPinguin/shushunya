import unittest
from tax.invoice import invoice_tax
from tax.rates import tax_for

class TaxRatesTest(unittest.TestCase):
    def test_standard_and_reduced_rates(self):
        self.assertEqual(tax_for(100), 20)
        self.assertEqual(tax_for(100, 'reduced'), 5)
        self.assertEqual(invoice_tax(100, 'reduced'), 5)

if __name__ == '__main__':
    unittest.main()

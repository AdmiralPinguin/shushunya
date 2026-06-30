import json
import unittest
from pathlib import Path
from api.invoice_service import calculate_invoice
from client.invoice_client import invoice_total

class CalculateInvoiceContractTest(unittest.TestCase):
    def test_contract_declares_response_field(self):
        contract = json.loads(Path('contracts/invoice.json').read_text(encoding='utf-8'))
        self.assertIn('net_total', contract['response_fields'])

    def test_implementation_and_caller_follow_contract(self):
        self.assertEqual(calculate_invoice({'gross': 100, 'fee': 15})['net_total'], 85)
        self.assertEqual(invoice_total(100, 15), 85)
        self.assertEqual(calculate_invoice({'gross': 80, 'fee': 5})['net_total'], 75)
        self.assertEqual(invoice_total(80, 5), 75)

if __name__ == '__main__':
    unittest.main()

import inspect
import unittest
from billing.public_api import calculate_total
from billing.client import client_total

class CalculateTotalCompatTest(unittest.TestCase):
    def test_public_signature_stays_compatible(self):
        self.assertEqual(list(inspect.signature(calculate_total).parameters), ['gross', 'fee'])

    def test_behavior_and_callers(self):
        self.assertEqual(calculate_total(100, 15), 85)
        self.assertEqual(client_total(100, 15), 85)
        self.assertEqual(calculate_total(80, 5), 75)
        self.assertEqual(client_total(80, 5), 75)

if __name__ == '__main__':
    unittest.main()

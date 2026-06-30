import unittest
from billing.discounts import apply_discount

class ApplyDiscountTest(unittest.TestCase):
    def test_apply_discount(self):
        self.assertEqual(apply_discount(200, 25), 150.0)
        self.assertEqual(apply_discount(80, 10), 72.0)

if __name__ == '__main__':
    unittest.main()

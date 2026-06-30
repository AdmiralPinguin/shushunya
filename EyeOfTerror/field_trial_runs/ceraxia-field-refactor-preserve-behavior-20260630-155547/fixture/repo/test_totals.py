import unittest
from orders import order_total
from refunds import refund_total

class TotalsTest(unittest.TestCase):
    def test_order_total(self):
        self.assertEqual(order_total(100, 15), 85)

    def test_refund_total(self):
        self.assertEqual(refund_total(80, 5), 75)

if __name__ == '__main__':
    unittest.main()

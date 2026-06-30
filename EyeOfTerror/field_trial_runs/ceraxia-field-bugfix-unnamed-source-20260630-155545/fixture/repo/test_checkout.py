import unittest
from checkout import total_after_discount

class CheckoutTest(unittest.TestCase):
    def test_percentage_discount(self):
        self.assertEqual(total_after_discount(200, 25), 150)

if __name__ == '__main__':
    unittest.main()

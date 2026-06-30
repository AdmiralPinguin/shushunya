import unittest
from calculator import net_total

class CalculatorTest(unittest.TestCase):
    def test_net_total_subtracts_fee(self):
        self.assertEqual(net_total(80, 5), 75)

if __name__ == '__main__':
    unittest.main()

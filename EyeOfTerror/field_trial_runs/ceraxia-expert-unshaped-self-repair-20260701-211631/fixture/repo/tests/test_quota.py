import unittest
from quota import max_daily_exports

class QuotaTest(unittest.TestCase):
    def test_max_daily_exports(self):
        self.assertEqual(max_daily_exports(), 7)

if __name__ == '__main__':
    unittest.main()

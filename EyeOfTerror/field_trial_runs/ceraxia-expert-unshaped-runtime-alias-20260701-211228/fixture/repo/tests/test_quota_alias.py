import unittest
from quota import max_daily_exports as limit

class QuotaAliasTest(unittest.TestCase):
    def test_alias_limit(self):
        self.assertEqual(limit(), 7)

if __name__ == '__main__':
    unittest.main()

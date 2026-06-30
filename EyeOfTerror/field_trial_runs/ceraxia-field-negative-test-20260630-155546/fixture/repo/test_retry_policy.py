import unittest
from retry_policy import parse_retry_count

class ParseRetryCountEdgeTest(unittest.TestCase):
    def test_positive_cases(self):
        self.assertEqual(parse_retry_count('0'), 0)
        self.assertEqual(parse_retry_count('3'), 3)
        self.assertEqual(parse_retry_count('10'), 10)

    def test_negative_cases(self):
        with self.assertRaises(ValueError):
            parse_retry_count('-1')
        with self.assertRaises(ValueError):
            parse_retry_count('11')
        with self.assertRaises(ValueError):
            parse_retry_count('bad')

if __name__ == '__main__':
    unittest.main()

import unittest
from api import handle_payload

class ApiTest(unittest.TestCase):
    def test_valid_amount(self):
        self.assertEqual(handle_payload({'amount': '12'}), {'amount': 12})

if __name__ == '__main__':
    unittest.main()

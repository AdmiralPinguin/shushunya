import warnings
import unittest
from payments.api import calculate_total
from payments.client import client_total

class ApiDeprecationTest(unittest.TestCase):
    def test_old_positional_fee_still_works_with_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertEqual(calculate_total(100, 15), 85)
        self.assertTrue(any(item.category is DeprecationWarning for item in caught))

    def test_new_keyword_path_and_caller(self):
        self.assertEqual(calculate_total(80, service_fee=5), 75)
        self.assertEqual(client_total(80, 5), 75)

if __name__ == '__main__':
    unittest.main()

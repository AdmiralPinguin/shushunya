import unittest
from archive_paths import safe_archive_path

class SafeArchivePathEdgeTest(unittest.TestCase):
    def test_positive_cases(self):
        self.assertEqual(safe_archive_path('books/chapter1.txt'), 'books/chapter1.txt')
        self.assertEqual(safe_archive_path('./books//chapter2.txt'), 'books/chapter2.txt')

    def test_negative_cases(self):
        with self.assertRaises(ValueError):
            safe_archive_path('../secret.txt')
        with self.assertRaises(ValueError):
            safe_archive_path('/etc/passwd')
        with self.assertRaises(ValueError):
            safe_archive_path('books/../../secret.txt')

if __name__ == '__main__':
    unittest.main()

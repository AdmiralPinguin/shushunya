import unittest
from archive_paths import safe_archive_path

class ArchivePathsTest(unittest.TestCase):
    def test_valid_relative_paths_are_normalized(self):
        self.assertEqual(safe_archive_path('books/chapter1.txt'), 'books/chapter1.txt')
        self.assertEqual(safe_archive_path('./books//chapter2.txt'), 'books/chapter2.txt')

    def test_traversal_and_absolute_paths_are_rejected(self):
        for raw in ('../secret.txt', '/etc/passwd', 'books/../../secret.txt'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    safe_archive_path(raw)

if __name__ == '__main__':
    unittest.main()

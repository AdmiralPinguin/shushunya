import unittest
from service.records import normalize_record, serialize_record

class RecordsMigrationTest(unittest.TestCase):
    def test_reads_old_shape(self):
        self.assertEqual(normalize_record({'id': 'a1', 'amount': 12}), {'id': 'a1', 'total_amount': 12})

    def test_reads_new_shape(self):
        self.assertEqual(normalize_record({'id': 'b2', 'total_amount': 20}), {'id': 'b2', 'total_amount': 20})

    def test_writer_emits_new_shape_only(self):
        self.assertEqual(serialize_record({'id': 'c3', 'amount': 7}), {'id': 'c3', 'total_amount': 7})

if __name__ == '__main__':
    unittest.main()

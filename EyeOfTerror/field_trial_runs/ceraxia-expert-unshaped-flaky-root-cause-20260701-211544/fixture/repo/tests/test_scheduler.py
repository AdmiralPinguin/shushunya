import unittest
from scheduler import schedule_order

class SchedulerTest(unittest.TestCase):
    def test_stable_order_for_equal_priority(self):
        items = [{'id': 'b', 'priority': 1}, {'id': 'a', 'priority': 1}]
        self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b'])

    def test_repeated_stability(self):
        for _ in range(20):
            items = [{'id': 'c', 'priority': 2}, {'id': 'a', 'priority': 1}, {'id': 'b', 'priority': 1}]
            self.assertEqual([item['id'] for item in schedule_order(items)], ['a', 'b', 'c'])

if __name__ == '__main__':
    unittest.main()

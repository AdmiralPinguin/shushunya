import threading
import unittest
from cache_store import CacheStore

class CacheStoreTest(unittest.TestCase):
    def test_invalidate_is_idempotent_and_reloadable(self):
        store = CacheStore()
        calls = []
        self.assertEqual(store.get_or_load('a', lambda: 'old'), 'old')
        self.assertEqual(store.invalidate('a'), 1)
        self.assertEqual(store.invalidate('a'), 2)
        self.assertEqual(store.get_or_load('a', lambda: 'new'), 'new')

    def test_concurrent_readers_share_loaded_value(self):
        store = CacheStore()
        calls = []
        def loader():
            calls.append(1)
            return 'value'
        results = []
        threads = [threading.Thread(target=lambda: results.append(store.get_or_load('k', loader))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ['value'] * 8)
        self.assertEqual(len(calls), 1)

if __name__ == '__main__':
    unittest.main()

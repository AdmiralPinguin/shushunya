import threading

class CacheStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._values = {}
        self._version = 0

    def get_or_load(self, key, loader):
        with self._lock:
            if key not in self._values:
                self._values[key] = loader()
            return self._values[key]

    def invalidate(self, key):
        with self._lock:
            self._values.pop(key, None)
            self._version += 1
            return self._version

    def version(self):
        with self._lock:
            return self._version

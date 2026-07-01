import unittest
from client import publish_event

class FlakyTransport:
    def __init__(self):
        self.calls = 0
    def send(self, event):
        self.calls += 1
        if self.calls < 3:
            raise ConnectionError('temporary outage')
        return {'ok': True, 'event': event}

class ValidationTransport:
    def __init__(self):
        self.calls = 0
    def send(self, event):
        self.calls += 1
        raise ValueError('invalid event')

class ClientTest(unittest.TestCase):
    def test_retries_transient_connection_errors(self):
        transport = FlakyTransport()
        self.assertEqual(publish_event(transport, {'id': 'evt-1'}), {'ok': True, 'event': {'id': 'evt-1'}})
        self.assertEqual(transport.calls, 3)

    def test_validation_errors_are_not_retried(self):
        transport = ValidationTransport()
        with self.assertRaises(ValueError):
            publish_event(transport, {'bad': True})
        self.assertEqual(transport.calls, 1)

if __name__ == '__main__':
    unittest.main()

import os
import unittest
from app.config_loader import load_settings

class ConfigRuntimeTest(unittest.TestCase):
    def test_default_setting(self):
        os.environ.pop('SERVICE_URL', None)
        self.assertEqual(load_settings()['service_url'], 'http://localhost:8080')

    def test_env_override(self):
        os.environ['SERVICE_URL'] = 'override-value'
        try:
            self.assertEqual(load_settings()['service_url'], 'override-value')
        finally:
            os.environ.pop('SERVICE_URL', None)

if __name__ == '__main__':
    unittest.main()

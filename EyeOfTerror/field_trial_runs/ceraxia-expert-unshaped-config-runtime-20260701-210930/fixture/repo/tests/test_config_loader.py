import os
import unittest
from app.config_loader import load_settings

class ConfigLoaderTest(unittest.TestCase):
    def test_default_service_url(self):
        os.environ.pop('SERVICE_URL', None)
        self.assertEqual(load_settings()['service_url'], 'http://localhost:8080')

    def test_env_override(self):
        os.environ['SERVICE_URL'] = 'https://prod.example'
        try:
            self.assertEqual(load_settings()['service_url'], 'https://prod.example')
        finally:
            os.environ.pop('SERVICE_URL', None)

if __name__ == '__main__':
    unittest.main()

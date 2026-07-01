import json
import os
from pathlib import Path

CONFIG_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = CONFIG_ROOT.parent
CONFIG_PATH = CONFIG_ROOT / 'app/settings.json'

def load_settings():
    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    value = os.environ.get('SERVICE_URL', data.get('service_url', 'http://localhost:8080'))
    return {'service_url': value}

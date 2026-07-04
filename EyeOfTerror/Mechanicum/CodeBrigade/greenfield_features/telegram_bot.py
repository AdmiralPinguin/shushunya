from __future__ import annotations

from typing import Any
from unittest.mock import patch

from .common import replace_project_file


def apply_telegram_command_bot_feature(project_name: str, files: list[Any]) -> list[Any]:
    package = project_name.replace("-", "_")
    bot = (
        "import os\n"
        "from typing import Final\n\n\n"
        "COMMANDS: Final[dict[str, str]] = {\n"
        "    '/start': 'Start the bot and show the welcome message.',\n"
        "    '/help': 'Show available commands.',\n"
        "    '/status': 'Report bot readiness.',\n"
        "    '/echo': 'Echo the text after the command.',\n"
        "}\n\n\n"
        "def command_list() -> list[str]:\n"
        "    return sorted(COMMANDS)\n\n\n"
        "def _help_text() -> str:\n"
        "    return 'Available commands: ' + ', '.join(command_list())\n\n\n"
        "def build_reply(text: str) -> str:\n"
        "    message = text.strip()\n"
        "    if not message or message == '/start':\n"
        f"        return 'Hello from {project_name}. ' + _help_text()\n"
        "    if message == '/help':\n"
        "        return _help_text()\n"
        "    if message == '/status':\n"
        "        return 'Bot is ready.'\n"
        "    if message.startswith('/echo'):\n"
        "        payload = message.removeprefix('/echo').strip()\n"
        "        if not payload:\n"
        "            return 'Usage: /echo <text>'\n"
        "        return payload\n"
        "    if message.startswith('/'):\n"
        "        return 'Unknown command. ' + _help_text()\n"
        "    return message\n\n\n"
        "def build_runtime_config() -> dict[str, str]:\n"
        "    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()\n"
        "    if not token:\n"
        "        raise RuntimeError('TELEGRAM_BOT_TOKEN is required for live Telegram runtime')\n"
        "    return {'token': token}\n\n\n"
        "def main() -> None:\n"
        "    build_runtime_config()\n"
        "    print('bot configured')\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    tests = (
        f"import os\nimport unittest\nfrom unittest.mock import patch\n\nfrom {package}.bot import build_reply, build_runtime_config, command_list\n\n\n"
        "class TelegramCommandBotTests(unittest.TestCase):\n"
        "    def test_command_list(self):\n"
        "        self.assertEqual(command_list(), ['/echo', '/help', '/start', '/status'])\n\n"
        "    def test_start_help_and_status(self):\n"
        "        self.assertIn('/echo', build_reply('/start'))\n"
        "        self.assertIn('/status', build_reply('/help'))\n"
        "        self.assertEqual(build_reply('/status'), 'Bot is ready.')\n\n"
        "    def test_echo_command(self):\n"
        "        self.assertEqual(build_reply('/echo hello bot'), 'hello bot')\n"
        "        self.assertEqual(build_reply('/echo'), 'Usage: /echo <text>')\n\n"
        "    def test_unknown_command_and_plain_text(self):\n"
        "        self.assertIn('Unknown command', build_reply('/missing'))\n"
        "        self.assertEqual(build_reply(' plain text '), 'plain text')\n\n"
        "    def test_runtime_requires_token(self):\n"
        "        with patch.dict(os.environ, {}, clear=True):\n"
        "            with self.assertRaises(RuntimeError):\n"
        "                build_runtime_config()\n"
        "        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'token'}, clear=True):\n"
        "            self.assertEqual(build_runtime_config(), {'token': 'token'})\n"
    )
    readme = (
        f"# {project_name}\n\nA Telegram bot scaffold with testable command handling for /start, /help, /status, and /echo.\n\n"
        "## Run\n\n```bash\nTELEGRAM_BOT_TOKEN=your-token python -m "
        f"{package}.bot\n```\n\n"
        "## Test\n\n```bash\npython -m unittest discover tests\n```\n"
    )
    rows = replace_project_file(files, f"{package}/bot.py", bot)
    rows = replace_project_file(rows, "tests/test_bot.py", tests)
    rows = replace_project_file(rows, "README.md", readme)
    return rows

def telegram_command_bot_module_contracts(project_name: str) -> list[dict[str, Any]]:
    package = project_name.replace("-", "_")
    return [
        {
            "module": f"{package}.bot",
            "path": f"{package}/bot.py",
            "responsibility": "Telegram command parsing and live runtime configuration",
            "requirements": ["handle start command", "handle help command", "handle status command", "handle echo command", "reject unknown commands clearly", "require token only for live runtime"],
        },
        {
            "module": "tests.test_bot",
            "path": "tests/test_bot.py",
            "responsibility": "Telegram command behavior verification",
            "requirements": ["prove command routing", "prove unknown command fallback", "prove token requirement without network"],
        },
    ]

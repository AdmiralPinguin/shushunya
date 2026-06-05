# Telegram Bot

Telegram bot for chatting with the local Gemma 4 host.

## Requirements

- The LLM host must be running at `http://127.0.0.1:8080`.
- A Telegram bot token from BotFather must be provided through `TELEGRAM_BOT_TOKEN`.

## Run

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/models
./llm-host/scripts/start-host.sh
TELEGRAM_BOT_TOKEN="123456:token" ./telegram-bot/start-bot.sh
```

## Stop

```bash
./telegram-bot/stop-bot.sh
```

## Bot Commands

- `/start` - show a short greeting.
- `/help` - show a short greeting.
- `/reset` - clear in-memory conversation context for the current chat.

## Settings

Optional environment variables:

- `LLM_BASE_URL` - default `http://127.0.0.1:8080`
- `LLM_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- `MAX_TOKENS` - default `512`
- `TEMPERATURE` - default `0.4`
- `SYSTEM_PROMPT` - bot system prompt

# Telegram Bot

Telegram bot for chatting with the local Gemma 4 host through ArchiveOfHeresy.

## Requirements

- The LLM host must be running at `http://127.0.0.1:8080`.
- ArchiveOfHeresy must be running at `http://127.0.0.1:8090`.
- A Telegram bot token from BotFather must be provided through `TELEGRAM_BOT_TOKEN`.

## Run

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness
./llm-host/scripts/start-host.sh
cd /media/shushunya/SHUSHUNYA/shushunya/ArchiveOfHeresy
./start-main.sh
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness
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

- `LLM_BASE_URL` - default `http://127.0.0.1:8090`
- `LLM_MODEL` - default `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- `MAX_TOKENS` - default `512`
- `TEMPERATURE` - default `0.4`
- `SYSTEM_PROMPT` - bot system prompt
- `STREAM_ENABLED` - default `1`; uses Telegram `sendMessageDraft` while the model is generating
- `STREAM_DRAFT_INTERVAL` - default `0.8`; seconds between Telegram draft updates

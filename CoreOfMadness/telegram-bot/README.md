# Telegram Bot

Telegram bot for chatting with the local Gemma 4 host through ArchiveOfHeresy.

Only allowlisted Telegram chats are archived and receive focus-memory context. Other chats still get model replies, but their requests disable ArchiveOfHeresy JSONL/SQLite writes, librarian updates, and focus injection.

## Requirements

- The LLM host must be running at `http://127.0.0.1:8080`.
- ArchiveOfHeresy must be running at `http://127.0.0.1:8090`.
- A Telegram bot token from BotFather must be provided through `TELEGRAM_BOT_TOKEN`.

The bot also loads local settings from `CoreOfMadness/telegram-bot/.env`. This file is ignored by Git.

## Run

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness
./llm-host/scripts/start-host.sh
cd /media/shushunya/SHUSHUNYA/shushunya/ArchiveOfHeresy
./start-main.sh
cd /media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness
./telegram-bot/start-bot.sh
```

## Stop

```bash
./telegram-bot/stop-bot.sh
```

## Bot Commands

- `/start` - show a short greeting.
- `/help` - show a short greeting.
- `/reset` - report that local message history is disabled; ArchiveOfHeresy focus memory carries context.

## Settings

Optional environment variables:

- `LLM_BASE_URL` - default `http://127.0.0.1:8090`
- `LLM_MODEL` - default `gemma-4-12b-it-Q6_K.gguf`
- `MAX_TOKENS` - default `2048`
- `MAX_CONTINUATIONS` - default `3`; continues automatically when the model stops because of the token limit
- `CONTINUATION_TAIL_CHARS` - default `2500`; characters of the previous answer sent back when asking the model to continue
- `TEMPERATURE` - default `0.4`
- `SYSTEM_PROMPT` - bot system prompt; default personality is Shushunya, a sarcastic daemon of Tzeentch
- `STREAM_ENABLED` - default `1`; uses Telegram `sendMessageDraft` while the model is generating
- `STREAM_DRAFT_INTERVAL` - default `1.1`; seconds between Telegram draft updates
- `STREAM_FINAL_DRAFT_TIMEOUT` - default `30`; seconds to wait while publishing the final draft update
- `TELEGRAM_ARCHIVE_ALLOWLIST` - comma-separated chat ids or usernames allowed to use ArchiveOfHeresy memory; default `7791909246,@Ebuchaya_psina`

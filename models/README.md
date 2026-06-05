# Shushunya Models

Local model storage, LLM host runtime, and Telegram bot files.

## Project Root

The project root is:

`/media/shushunya/SHUSHUNYA/shushunya`

All model-server related files are kept inside:

`/media/shushunya/SHUSHUNYA/shushunya/models`

## Purpose

This directory is intended to run a local language model server on Linux over SSH. The current target model is Gemma 4 12B Instruct in Q5 GGUF quantization, served through `llama.cpp` / `llama-server`.

## Directory Layout

- `gemma-4-12b-it-UD-Q5_K_XL.gguf` - downloaded GGUF model.
- `llm-host/llama.cpp/` - local llama.cpp runtime files.
- `llm-host/runtime/` - local logs and server process files.
- `llm-host/scripts/` - helper scripts for starting, checking, and stopping the LLM host.
- `telegram-bot/` - Telegram bot for chatting with the local model.

## Target Model

- Model family: Google Gemma 4
- Variant: 12B Instruct
- Format: GGUF
- Quantization: `UD-Q5_K_XL`
- File: `gemma-4-12b-it-UD-Q5_K_XL.gguf`
- Source: `https://huggingface.co/unsloth/gemma-4-12b-it-GGUF`

## Runtime

- Runtime: `llama.cpp`
- Installed release: `b9524`
- Binary type: Ubuntu Linux x64 Vulkan build
- Source: `https://github.com/ggml-org/llama.cpp/releases`
- Runtime binaries are local-only and are not tracked in Git.
- Model files are local-only and are not tracked in Git.

## Host

The host is planned to run with `llama-server` and expose an OpenAI-compatible API.

Default settings:

- Host: `0.0.0.0`
- Port: `8080`
- Context size: `2048`
- Parallel slots: `1`
- GPU layers: `999` by default, so Vulkan can offload to the GPU when available
- Reasoning mode: `off` by default, so chat replies return normal `content`
- API base URL: `http://127.0.0.1:8080`
- Health endpoint: `GET /health`
- Chat completions endpoint: `POST /v1/chat/completions`

## Run

After installation, start the server with:

```bash
./llm-host/scripts/start-host.sh
```

Check it with:

```bash
./llm-host/scripts/check-host.sh
```

Stop it with:

```bash
./llm-host/scripts/stop-host.sh
```

Runtime settings can be overridden through environment variables:

```bash
PORT=8081 CTX_SIZE=4096 PARALLEL=1 GPU_LAYERS=999 REASONING=off ./llm-host/scripts/start-host.sh
```

## Telegram Bot

The Telegram bot is stored in:

`telegram-bot/`

Start the LLM host first, then start the bot with a BotFather token:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/models
./llm-host/scripts/start-host.sh
TELEGRAM_BOT_TOKEN="123456:token" ./telegram-bot/start-bot.sh
```

Stop the bot with:

```bash
./telegram-bot/stop-bot.sh
```

The bot talks to the local LLM host through `http://127.0.0.1:8080/v1/chat/completions`.

## Notes

Gemma 4 12B Q5 is a large model. It requires several GB of disk space and enough RAM or VRAM for inference. If the host cannot start because of memory limits, use a smaller quantization such as Q4.

#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Ты локальный помощник. Отвечай по-русски ясно, без лишней воды.",
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "512"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.4"))
HISTORY_MESSAGES = int(os.environ.get("HISTORY_MESSAGES", "12"))

API_URL = f"https://api.telegram.org/bot{TOKEN}"
RUNNING = True
HISTORY = {}


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def request_json(url, payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def telegram(method, payload=None, timeout=60):
    return request_json(f"{API_URL}/{method}", payload, timeout)


def send_message(chat_id, text):
    if not text:
        text = "Модель вернула пустой ответ."

    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)]
    for chunk in chunks:
        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
        )


def send_typing(chat_id):
    try:
        telegram("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
    except Exception:
        pass


def ask_llm(chat_id, text):
    chat_history = HISTORY.setdefault(chat_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(chat_history[-HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": text})

    response = request_json(
        f"{LLM_BASE_URL}/v1/chat/completions",
        {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
        },
        timeout=180,
    )

    answer = response["choices"][0]["message"].get("content", "").strip()
    chat_history.append({"role": "user", "content": text})
    chat_history.append({"role": "assistant", "content": answer})
    HISTORY[chat_id] = chat_history[-HISTORY_MESSAGES:]
    return answer


def handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if text in ("/start", "/help"):
        send_message(
            chat_id,
            "Я подключён к локальной Gemma 4 12B. Напиши сообщение, и я передам его модели.",
        )
        return

    if text == "/reset":
        HISTORY.pop(chat_id, None)
        send_message(chat_id, "Контекст очищен.")
        return

    send_typing(chat_id)
    try:
        answer = ask_llm(chat_id, text)
        send_message(chat_id, answer)
    except urllib.error.URLError as exc:
        send_message(chat_id, f"LLM-хост недоступен: {exc}")
    except Exception as exc:
        send_message(chat_id, f"Ошибка: {exc}")


def main():
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    offset = None
    print(f"Telegram bot started. LLM: {LLM_BASE_URL}, model: {LLM_MODEL}", flush=True)

    while RUNNING:
        payload = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset

        try:
            updates = telegram("getUpdates", payload, timeout=40)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])
        except Exception as exc:
            print(f"Polling error: {exc}", file=sys.stderr, flush=True)
            time.sleep(3)

    print("Telegram bot stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

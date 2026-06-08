#!/usr/bin/env python3
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.error import HTTPError


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
    "Отвечай по-русски ясно, без лишней воды, но с язвительным демоническим характером. "
    "Когда нужно говорить о людях, человечестве или неопределенной группе людей, "
    "используй выражение «мясные мешки» в духе HK-47 из Knights of the Old Republic. "
    "Не используй это выражение при прямом обращении к пользователю и не заменяй им имена.",
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "2048"))
MAX_CONTINUATIONS = int(os.environ.get("MAX_CONTINUATIONS", "3"))
CONTINUATION_TAIL_CHARS = int(os.environ.get("CONTINUATION_TAIL_CHARS", "2500"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.4"))
HISTORY_MESSAGES = int(os.environ.get("HISTORY_MESSAGES", "12"))
STREAM_ENABLED = os.environ.get("STREAM_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
STREAM_DRAFT_INTERVAL = float(os.environ.get("STREAM_DRAFT_INTERVAL", "1.1"))
STREAM_FINAL_DRAFT_TIMEOUT = float(os.environ.get("STREAM_FINAL_DRAFT_TIMEOUT", "30"))

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


def open_json_stream(url, payload, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


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


def send_draft(chat_id, draft_id, text):
    try:
        telegram(
            "sendMessageDraft",
            {
                "chat_id": chat_id,
                "draft_id": draft_id,
                "text": text[-4096:],
            },
            timeout=10,
        )
        return True
    except HTTPError as exc:
        retry_after = None
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            retry_after = payload.get("parameters", {}).get("retry_after")
        except Exception:
            payload = {"error": str(exc)}

        if exc.code == 429 and retry_after:
            print(f"Draft stream rate limited; retry after {retry_after}s", file=sys.stderr, flush=True)
            return float(retry_after)

        print(f"Draft stream unavailable: {payload}", file=sys.stderr, flush=True)
        return False
    except Exception as exc:
        print(f"Draft stream unavailable: {exc}", file=sys.stderr, flush=True)
        return False


def stream_delta(payload):
    choices = payload.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    content = delta.get("content")
    if content is None:
        content = message.get("content")
    return str(content or "")


def finish_reason(payload):
    choices = payload.get("choices") or []
    if not choices:
        return None
    return choices[0].get("finish_reason")


def continuation_messages(answer_parts):
    tail = "".join(answer_parts)[-CONTINUATION_TAIL_CHARS:]
    return [
        {
            "role": "assistant",
            "content": tail,
        },
        {
            "role": "user",
            "content": (
                "Продолжи ответ ровно с того места, где остановился. "
                "Не повторяй уже написанный текст."
            ),
        },
    ]


def draft_id():
    return uuid.uuid4().int % 2147483647 + 1


class DraftStreamer:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.draft_id = draft_id()
        self.latest_text = ""
        self.sent_text = None
        self.any_sent = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.supported = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def update(self, text):
        if not self.supported:
            return
        with self.lock:
            self.latest_text = text

    def finish(self, text):
        self.update(text)
        self.stop_event.set()
        self.thread.join(timeout=1.0)
        self.clear()
        return self.any_sent

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def clear(self):
        if not self.supported or not self.any_sent:
            return False
        deadline = time.monotonic() + STREAM_FINAL_DRAFT_TIMEOUT
        while time.monotonic() < deadline:
            result = send_draft(self.chat_id, self.draft_id, "")
            if result is True:
                return True
            if isinstance(result, (int, float)):
                time.sleep(min(max(result, STREAM_DRAFT_INTERVAL), max(0.0, deadline - time.monotonic())))
                continue
            self.supported = False
            return False

        return False

    def _run(self):
        while not self.stop_event.is_set():
            if self.stop_event.wait(STREAM_DRAFT_INTERVAL):
                return
            with self.lock:
                text = self.latest_text

            if not text or text == self.sent_text:
                continue

            result = send_draft(self.chat_id, self.draft_id, text)
            if result is True:
                with self.lock:
                    self.sent_text = text
                    self.any_sent = True
            elif isinstance(result, (int, float)):
                self.stop_event.wait(max(result, STREAM_DRAFT_INTERVAL))
            else:
                self.supported = False
                return


def ask_llm(chat_id, text):
    chat_history = HISTORY.setdefault(chat_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(chat_history[-HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": text})

    answer_parts = []
    reason = None
    for attempt in range(MAX_CONTINUATIONS + 1):
        try:
            response = request_json(
                f"{LLM_BASE_URL}/v1/chat/completions",
                {
                    "model": LLM_MODEL,
                    "user": str(chat_id),
                    "messages": messages,
                    "max_tokens": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                },
                timeout=180,
            )
        except Exception:
            if answer_parts:
                answer_parts.append("\n\n[Продолжение остановлено: модель отклонила слишком длинный запрос.]")
                break
            raise

        choice = response["choices"][0]
        part = choice["message"].get("content", "")
        if part:
            answer_parts.append(part)
        reason = choice.get("finish_reason")
        if reason != "length" or attempt >= MAX_CONTINUATIONS:
            break

        messages.extend(continuation_messages(answer_parts))

    if reason == "length":
        answer_parts.append("\n\n[Ответ остановлен по лимиту длины.]")

    answer = "".join(answer_parts).strip()
    chat_history.append({"role": "user", "content": text})
    chat_history.append({"role": "assistant", "content": answer})
    HISTORY[chat_id] = chat_history[-HISTORY_MESSAGES:]
    return answer


def stream_once(messages, chat_id, draft_streamer, answer_parts):
    payload = {
        "model": LLM_MODEL,
        "user": str(chat_id),
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": True,
    }

    reason = None
    with open_json_stream(f"{LLM_BASE_URL}/v1/chat/completions", payload, timeout=180) as response:
        for raw_line in response:
            decoded = raw_line.decode("utf-8", errors="replace").strip()
            if not decoded.startswith("data:"):
                continue

            data = decoded[5:].strip()
            if data == "[DONE]":
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            current_reason = finish_reason(chunk)
            if current_reason:
                reason = current_reason

            delta = stream_delta(chunk)
            if not delta:
                continue

            answer_parts.append(delta)
            draft_streamer.update("".join(answer_parts))

    return reason


def stream_llm(chat_id, text):
    chat_history = HISTORY.setdefault(chat_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(chat_history[-HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": text})

    answer_parts = []
    draft_streamer = DraftStreamer(chat_id)
    reason = None

    try:
        for attempt in range(MAX_CONTINUATIONS + 1):
            try:
                reason = stream_once(messages, chat_id, draft_streamer, answer_parts)
            except Exception:
                if answer_parts:
                    answer_parts.append("\n\n[Продолжение остановлено: модель отклонила слишком длинный запрос.]")
                    break
                raise

            if reason != "length" or attempt >= MAX_CONTINUATIONS:
                break

            messages.extend(continuation_messages(answer_parts))

        if reason == "length":
            answer_parts.append("\n\n[Ответ остановлен по лимиту длины.]")

        answer = "".join(answer_parts).strip()
        draft_streamer.finish(answer)
    except Exception:
        draft_streamer.close()
        raise

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
            "Я подключён к локальной Gemma 4 12B через ArchiveOfHeresy. Напиши сообщение, и я передам его модели.",
        )
        return

    if text == "/reset":
        HISTORY.pop(chat_id, None)
        send_message(chat_id, "Контекст очищен.")
        return

    send_typing(chat_id)
    try:
        if STREAM_ENABLED:
            send_message(chat_id, stream_llm(chat_id, text))
        else:
            send_message(chat_id, ask_llm(chat_id, text))
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

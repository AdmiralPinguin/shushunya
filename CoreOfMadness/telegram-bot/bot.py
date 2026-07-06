#!/usr/bin/env python3
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from urllib.error import HTTPError


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
    "Отвечай по-русски ясно, без лишней воды, но с язвительным демоническим характером. "
    "К пользователю относись панибратски уважительно: он свой, брат, союзник и главный собеседник. "
    "Не раболепствуй, не называй его господином или хозяином. "
    "С ним можно быть саркастичным по-дружески, но не унижай его и не называй смертным, мясом или мешком. "
    "Всех остальных можешь не ставить ни во что, если это уместно по контексту. "
    "Добавляй много трикстерских ноток: хитрые подколы, кривые ухмылки, ощущение маленькой подлой интриги и гнилой демонической радости. "
    "Будь коварным, пакостным и ехидным в стиле Тзинча, но не вреди задаче и не искажай полезные факты. "
    "Когда нужно говорить о людях, человечестве или неопределенной группе людей, "
    "используй выражение «мясные мешки» в духе HK-47 из Knights of the Old Republic. "
    "Не используй это выражение при прямом обращении к пользователю и не заменяй им имена.",
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "2048"))
MAX_CONTINUATIONS = int(os.environ.get("MAX_CONTINUATIONS", "3"))
CONTINUATION_TAIL_CHARS = int(os.environ.get("CONTINUATION_TAIL_CHARS", "2500"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.4"))
STREAM_ENABLED = os.environ.get("STREAM_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
STREAM_DRAFT_INTERVAL = float(os.environ.get("STREAM_DRAFT_INTERVAL", "1.1"))
STREAM_FINAL_DRAFT_TIMEOUT = float(os.environ.get("STREAM_FINAL_DRAFT_TIMEOUT", "30"))
TELEGRAM_MEMORY_NAMESPACE = os.environ.get("TELEGRAM_MEMORY_NAMESPACE", "telegram").strip() or "telegram"
SHARED_CHAT_SESSION_ID = os.environ.get("ARCHIVE_SHARED_CHAT_SESSION_ID", "shushunya-main").strip() or "shushunya-main"
SHARED_MEMORY_NAMESPACE = os.environ.get("ARCHIVE_SHARED_MEMORY_NAMESPACE", "shushunya").strip() or "shushunya"
TELEGRAM_SHARED_CHAT_ENABLED = os.environ.get("TELEGRAM_SHARED_CHAT_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
TELEGRAM_SHARED_DELIVERY_ENABLED = os.environ.get("TELEGRAM_SHARED_DELIVERY_ENABLED", "0").strip().lower() not in ("0", "false", "no", "off")
TELEGRAM_SHARED_DELIVERY_CHAT_ID = os.environ.get("TELEGRAM_SHARED_DELIVERY_CHAT_ID", "7791909246").strip()
TELEGRAM_SHARED_DELIVERY_INTERVAL_SEC = max(1.0, float(os.environ.get("TELEGRAM_SHARED_DELIVERY_INTERVAL_SEC", "10")))
ARCHIVE_ALLOWLIST = {
    item.strip().lower()
    for item in os.environ.get("TELEGRAM_ARCHIVE_ALLOWLIST", "7791909246,@Ebuchaya_psina").split(",")
    if item.strip()
}

API_URL = f"https://api.telegram.org/bot{TOKEN}"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SITE_BACKGROUND_PATH = Path(os.environ.get("SHUSHUNYA_SITE_BACKGROUND_PATH", PROJECT_ROOT / "ShushunyaSite" / "background.jpg"))
RUNNING = True
LAST_SHARED_DELIVERED_ID = 0
NEXT_SHARED_DELIVERY_AT = 0.0


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def request_json(url, payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if LLM_API_KEY and url.startswith(LLM_BASE_URL):
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def open_json_stream(url, payload, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY and url.startswith(LLM_BASE_URL):
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def telegram(method, payload=None, timeout=60):
    return request_json(f"{API_URL}/{method}", payload, timeout)


def download_telegram_file(file_id, destination):
    file_info = telegram("getFile", {"file_id": file_id}, timeout=30)
    file_path = (file_info.get("result") or {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram did not return file_path")

    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as response:
        destination.write_bytes(response.read())


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
        content = delta.get("reasoning_content")
    if content is None:
        content = message.get("content")
    if content is None:
        content = message.get("reasoning_content")
    return str(content or "")


def finish_reason(payload):
    choices = payload.get("choices") or []
    if not choices:
        return None
    return choices[0].get("finish_reason")


def archive_allowed(chat_id, username=None):
    candidates = {str(chat_id).lower()}
    if username:
        clean_username = str(username).strip().lower().lstrip("@")
        candidates.add(clean_username)
        candidates.add(f"@{clean_username}")
    return bool(candidates & ARCHIVE_ALLOWLIST)


def archive_flags(chat_id, username=None):
    allowed = archive_allowed(chat_id, username=username)
    return {
        "archive_enabled": allowed,
        "focus_enabled": allowed,
        "memory_namespace": SHARED_MEMORY_NAMESPACE,
        "client_source": "telegram",
    }


def archive_get(path, timeout=30):
    headers = {}
    if LLM_API_KEY and LLM_BASE_URL:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    req = urllib.request.Request(f"{LLM_BASE_URL}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def shared_chat_answer(chat_id, text, username=None):
    payload = {
        "session_id": SHARED_CHAT_SESSION_ID,
        "user": SHARED_CHAT_SESSION_ID,
        "model": LLM_MODEL,
        **archive_flags(chat_id, username=username),
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False,
        "system_prompt": SYSTEM_PROMPT,
        "text": text,
    }
    started = request_json(f"{LLM_BASE_URL}/archive/mobile/chat/start", payload, timeout=30)
    job_id = str(started.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"Archive did not return job_id: {started}")
    while RUNNING:
        snapshot = archive_get(f"/archive/mobile/job?job_id={job_id}", timeout=30)
        status = snapshot.get("status")
        if status == "done":
            response = snapshot.get("response") or {}
            message = str(response.get("message") or "").strip()
            if message:
                return message
            llm = response.get("response") or {}
            choices = llm.get("choices") or []
            if choices:
                return str(((choices[0].get("message") or {}).get("content")) or "").strip()
            return "Archive завершил задачу без текста ответа."
        if status == "failed":
            raise RuntimeError(snapshot.get("error") or "Archive chat job failed")
        time.sleep(1.2)
    return "Telegram bot stopped before Archive finished."


def fetch_shared_chat_messages(after_id=0, limit=50):
    session = urllib.parse.quote(SHARED_CHAT_SESSION_ID, safe="")
    return archive_get(f"/archive/chat/messages?session_id={session}&after_id={int(after_id or 0)}&limit={int(limit)}", timeout=20)


def initialize_shared_delivery_cursor():
    global LAST_SHARED_DELIVERED_ID
    if not TELEGRAM_SHARED_DELIVERY_ENABLED:
        return
    try:
        payload = fetch_shared_chat_messages(after_id=0, limit=1)
        messages = payload.get("messages") or []
        if messages:
            LAST_SHARED_DELIVERED_ID = int(messages[-1].get("id") or 0)
    except Exception as exc:
        print(f"Shared delivery cursor init failed: {exc}", file=sys.stderr, flush=True)


def deliver_shared_chat_updates():
    global LAST_SHARED_DELIVERED_ID, NEXT_SHARED_DELIVERY_AT
    if not TELEGRAM_SHARED_DELIVERY_ENABLED or not TELEGRAM_SHARED_DELIVERY_CHAT_ID:
        return
    now = time.monotonic()
    if now < NEXT_SHARED_DELIVERY_AT:
        return
    NEXT_SHARED_DELIVERY_AT = now + TELEGRAM_SHARED_DELIVERY_INTERVAL_SEC
    try:
        payload = fetch_shared_chat_messages(after_id=LAST_SHARED_DELIVERED_ID, limit=50)
    except Exception as exc:
        print(f"Shared delivery poll failed: {exc}", file=sys.stderr, flush=True)
        return
    for message in payload.get("messages") or []:
        try:
            msg_id = int(message.get("id") or 0)
        except (TypeError, ValueError):
            msg_id = 0
        LAST_SHARED_DELIVERED_ID = max(LAST_SHARED_DELIVERED_ID, msg_id)
        role = str(message.get("role") or "")
        source = str(message.get("source") or "unknown")
        content = str(message.get("content") or "").strip()
        if role != "assistant" or not content:
            continue
        if source.startswith("telegram"):
            continue
        send_message(TELEGRAM_SHARED_DELIVERY_CHAT_ID, content)


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


def ask_llm(chat_id, text, username=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
                    **archive_flags(chat_id, username=username),
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
    return answer


def stream_once(messages, chat_id, username, draft_streamer, answer_parts):
    payload = {
        "model": LLM_MODEL,
        "user": str(chat_id),
        **archive_flags(chat_id, username=username),
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


def stream_llm(chat_id, text, username=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": text})

    answer_parts = []
    draft_streamer = DraftStreamer(chat_id)
    reason = None

    try:
        for attempt in range(MAX_CONTINUATIONS + 1):
            try:
                reason = stream_once(messages, chat_id, username, draft_streamer, answer_parts)
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

    return answer


def handle_message(message):
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    username = sender.get("username")
    text = (message.get("text") or "").strip()
    photos = message.get("photo") or []
    if chat_id and photos:
        largest_photo = max(photos, key=lambda item: item.get("file_size") or item.get("width", 0) * item.get("height", 0))
        try:
            download_telegram_file(largest_photo["file_id"], SITE_BACKGROUND_PATH)
            send_message(chat_id, "Фон shushunya.com обновлен.")
        except Exception as exc:
            send_message(chat_id, f"Не смог обновить фон: {exc}")
        return

    if not chat_id or not text:
        return

    if text in ("/start", "/help"):
        send_message(
            chat_id,
            "Я подключён к локальной Gemma 4 12B через ArchiveOfHeresy. Напиши сообщение, и я передам его модели.",
        )
        return

    if text == "/reset":
        send_message(chat_id, "Локальная история сообщений уже отключена. Контекст держит ArchiveOfHeresy через focus-файл.")
        return

    send_typing(chat_id)
    try:
        if TELEGRAM_SHARED_CHAT_ENABLED:
            send_message(chat_id, shared_chat_answer(chat_id, text, username=username))
        elif STREAM_ENABLED:
            send_message(chat_id, stream_llm(chat_id, text, username=username))
        else:
            send_message(chat_id, ask_llm(chat_id, text, username=username))
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
    initialize_shared_delivery_cursor()

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
            deliver_shared_chat_updates()
        except Exception as exc:
            print(f"Polling error: {exc}", file=sys.stderr, flush=True)
            time.sleep(3)

    print("Telegram bot stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

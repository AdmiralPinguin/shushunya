#!/usr/bin/env python3
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path


MAX_FOCUS_FILES = int(os.environ.get("ARCHIVE_FOCUS_MAX_FILES", "10"))
LIBRARIAN_MODEL = os.environ.get("ARCHIVE_LIBRARIAN_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
LIBRARIAN_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_LIBRARIAN_SYSTEM_PROMPT",
    "Ты изолированный архивариус ArchiveOfHeresy. "
    "Ты не Шушуня, не собеседник пользователя и не используешь личность основного демона. "
    "Ты не наследуешь пользовательские промпты, стиль, шутки, эмоции или роль основного диалога. "
    "Твоя единственная задача: аккуратно поддерживать focus-файлы памяти. "
    "Работай сухо, структурно и консервативно. "
    "Отвечай только валидным JSON без markdown, пояснений и художественного тона.",
)
LIBRARIAN_TASK_PROMPT = os.environ.get(
    "ARCHIVE_LIBRARIAN_TASK_PROMPT",
    "Реши, продолжает ли новый обмен текущую тему focus-файла или открывает новую тему. "
    "Если тема продолжается, обнови summary так, чтобы оно хранило всю важную информацию по текущей теме: "
    "решения, ограничения, имена, пути, команды, статусы, договоренности, открытые вопросы и следующие шаги. "
    "Если тема сменилась, верни action=new и summary только для новой темы. "
    "Не копируй лишнюю болтовню. Не добавляй факты, которых нет во входных данных. "
    "importance от 1 до 5: 1 временное, 3 полезный рабочий контекст, 5 архитектура или долговременная память.",
)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clamp_importance(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(5, value))


def safe_slug(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-zа-яё0-9]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:64] or "focus"


def trim_text(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def extract_json(value):
    value = str(value or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?", "", value).strip()
        value = re.sub(r"```$", "", value).strip()

    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        value = value[start : end + 1]
    return json.loads(value)


def latest_user_message(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


class Librarian:
    def __init__(self, root, proxy_json):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        self.proxy_json = proxy_json
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def process_turn(self, record):
        if record.get("status") != "ok":
            return
        if record.get("conversation_id") == "archive-librarian":
            return

        user_text = latest_user_message(record.get("request", {}).get("messages", []))
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        if not user_text or not assistant_text:
            return

        index = self.load_index()
        active = self.active_focus(index)
        decision = self.decide(record, active, user_text, assistant_text)

        if not active or decision["action"] == "new":
            if active:
                self.pause_focus(active)
            focus = self.create_focus(index, record, decision, user_text, assistant_text)
        else:
            focus = self.update_focus(active, record, decision, user_text, assistant_text)

        index["active_id"] = focus["id"]
        self.enforce_limit(index)
        self.save_index(index)

    def load_index(self):
        if not self.index_path.exists():
            return {"version": 1, "active_id": None, "files": []}
        try:
            with self.index_path.open(encoding="utf-8") as source:
                index = json.load(source)
        except Exception:
            return {"version": 1, "active_id": None, "files": []}

        index.setdefault("version", 1)
        index.setdefault("active_id", None)
        index.setdefault("files", [])
        return index

    def save_index(self, index):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = self.index_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as target:
            json.dump(index, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
        tmp_path.replace(self.index_path)

    def active_focus(self, index):
        active_id = index.get("active_id")
        for focus in index.get("files", []):
            if focus.get("id") == active_id and focus.get("status") == "active":
                return focus
        return None

    def decide(self, record, active, user_text, assistant_text):
        active_text = self.read_focus(active)
        prompt = {
            "current_focus_file": active_text[-6000:],
            "new_exchange": {"user": user_text, "assistant": assistant_text},
            "task": LIBRARIAN_TASK_PROMPT,
            "rules": [
                "If the exchange continues the same topic, use action=continue.",
                "If the user changed topic, use action=new.",
                "Keep summary compact but preserve all useful facts, decisions, constraints, names, paths, commands, and next steps for the current topic.",
                "importance is 1..5: 1 temporary chatter, 3 useful working context, 5 important architecture or long-term memory.",
                "Do not imitate the assistant persona from the conversation.",
                "Do not use the user's conversational style as your own style.",
            ],
            "output": {
                "action": "continue|new",
                "title": "short topic title",
                "importance": "integer 1..5",
                "summary": "compact focus file body for the current topic",
            },
        }
        payload = {
            "model": record.get("model") or LIBRARIAN_MODEL,
            "user": "archive-librarian",
            "messages": [
                {
                    "role": "system",
                    "content": LIBRARIAN_SYSTEM_PROMPT,
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "max_tokens": 900,
            "temperature": 0.1,
        }

        try:
            _status, response = self.proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=180)
            content = response["choices"][0]["message"].get("content", "")
            decision = extract_json(content)
        except Exception:
            decision = {
                "action": "continue" if active else "new",
                "title": active.get("title") if active else user_text[:80],
                "importance": active.get("importance") if active else 3,
                "summary": f"Пользователь: {user_text}\nМодель: {assistant_text}",
            }

        action = str(decision.get("action") or "").strip().lower()
        if action not in ("continue", "new"):
            action = "continue" if active else "new"

        title = str(decision.get("title") or (active or {}).get("title") or user_text[:80]).strip()
        summary = trim_text(decision.get("summary"), 5000)
        if not summary:
            summary = trim_text(f"Пользователь: {user_text}\nМодель: {assistant_text}", 5000)

        return {
            "action": action,
            "title": title,
            "importance": clamp_importance(decision.get("importance")),
            "summary": summary,
        }

    def create_focus(self, index, record, decision, user_text, assistant_text):
        created_at = now_iso()
        focus_id = str(uuid.uuid4())
        title = decision["title"]
        file_name = f"{created_at[:10]}-{safe_slug(title)}-{focus_id[:8]}.md"
        focus = {
            "id": focus_id,
            "title": title,
            "path": str((self.files_dir / file_name).relative_to(self.root)),
            "status": "active",
            "importance": decision["importance"],
            "created_at": created_at,
            "updated_at": created_at,
            "conversation_id": record.get("conversation_id"),
            "turn_id": record.get("turn_id"),
        }
        index.setdefault("files", []).append(focus)
        self.write_focus_file(focus, decision["summary"], user_text, assistant_text)
        return focus

    def update_focus(self, focus, record, decision, user_text, assistant_text):
        focus["status"] = "active"
        focus["title"] = decision["title"] or focus.get("title") or "Focus"
        focus["importance"] = max(clamp_importance(focus.get("importance")), decision["importance"])
        focus["updated_at"] = now_iso()
        focus["conversation_id"] = record.get("conversation_id")
        focus["turn_id"] = record.get("turn_id")
        self.write_focus_file(focus, decision["summary"], user_text, assistant_text)
        return focus

    def pause_focus(self, focus):
        focus["status"] = "paused"
        focus["updated_at"] = now_iso()
        path = self.root / focus.get("path", "")
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^status: active$", "status: paused", text, count=1, flags=re.MULTILINE)
        text = re.sub(
            r"^updated_at: .*$",
            f"updated_at: {focus['updated_at']}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

    def read_focus(self, focus):
        if not focus:
            return ""
        path = self.root / focus.get("path", "")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_focus_file(self, focus, summary, user_text, assistant_text):
        path = self.root / focus["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [
            "---",
            f"id: {focus['id']}",
            f"title: {focus['title']}",
            f"status: {focus['status']}",
            f"importance: {focus['importance']}",
            f"created_at: {focus['created_at']}",
            f"updated_at: {focus['updated_at']}",
            f"conversation_id: {focus.get('conversation_id')}",
            f"turn_id: {focus.get('turn_id')}",
            "---",
            "",
            "# Focus",
            "",
            trim_text(summary, 5000),
            "",
            "## Last Exchange",
            "",
            "User:",
            trim_text(user_text, 1400),
            "",
            "Assistant:",
            trim_text(assistant_text, 2200),
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")

    def enforce_limit(self, index):
        files = index.get("files", [])
        if len(files) <= MAX_FOCUS_FILES:
            return

        active_id = index.get("active_id")
        candidates = sorted(
            files,
            key=lambda item: (
                item.get("id") == active_id,
                clamp_importance(item.get("importance")),
                item.get("updated_at") or "",
            ),
        )
        remove_ids = {item["id"] for item in candidates[: len(files) - MAX_FOCUS_FILES]}
        remaining = []
        for focus in files:
            if focus.get("id") in remove_ids:
                path = self.root / focus.get("path", "")
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                remaining.append(focus)
        index["files"] = remaining

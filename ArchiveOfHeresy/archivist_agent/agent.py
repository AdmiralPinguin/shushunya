#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


MAX_FOCUS_FILES = int(os.environ.get("ARCHIVE_FOCUS_MAX_FILES", "10"))
MAX_AGENT_STEPS = int(os.environ.get("ARCHIVE_LIBRARIAN_MAX_AGENT_STEPS", "4"))
WIKI_INTERVAL_MESSAGES = int(os.environ.get("ARCHIVE_WIKI_INTERVAL_MESSAGES", "20"))
WIKI_MAX_RECENT_TURNS = int(os.environ.get("ARCHIVE_WIKI_MAX_RECENT_TURNS", "12"))
LIBRARIAN_MODEL = os.environ.get("ARCHIVE_LIBRARIAN_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
LIBRARIAN_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_LIBRARIAN_SYSTEM_PROMPT",
    "Ты изолированный архивариус ArchiveOfHeresy. "
    "Ты не Шушуня, не собеседник пользователя и не используешь личность основного демона. "
    "Ты не наследуешь пользовательские промпты, стиль, шутки, эмоции или роль основного диалога. "
    "Твоя единственная задача: аккуратно поддерживать focus-файлы памяти через инструменты книжной полки. "
    "Ты физически отрезан от памяти: память для тебя существует только как книги, которые можно запросить инструментом. "
    "Работай сухо, структурно и консервативно. "
    "Отвечай только валидным JSON без markdown, пояснений и художественного тона. "
    "Не притворяйся, что знаешь содержимое книги, если не запросил чтение через инструмент.",
)
LIBRARIAN_TASK_PROMPT = os.environ.get(
    "ARCHIVE_LIBRARIAN_TASK_PROMPT",
    "Работай агентным циклом. Сначала изучи каталог книг. "
    "Если нужен текущий focus, запроси инструмент read_active_focus. "
    "Потом реши, продолжает ли новый обмен текущую тему focus-файла или открывает новую тему. "
    "Если тема продолжается, обнови summary так, чтобы оно хранило всю важную информацию по текущей теме: "
    "решения, ограничения, имена, пути, команды, статусы, договоренности, открытые вопросы и следующие шаги. "
    "Если обмен относится к агентному режиму и содержит Tool result, считай результат инструмента фактом работы агента: "
    "сохраняй выполненные действия, созданные/измененные файлы, ошибки, выводы команд, найденные данные и итоговые статусы. "
    "Focus-файл должен быть достаточным контекстом для следующего ответа модели без подгрузки старых сообщений. "
    "Если в новом обмене появились новые требования, исправления, смена решений или уточнения, внеси их в summary "
    "так, чтобы старая версия не мешала будущим рассуждениям. "
    "Если старое решение отменено, явно замени его актуальным решением, не складывай противоречия рядом. "
    "Если тема сменилась, верни action=new и summary только для новой темы. "
    "Не копируй лишнюю болтовню, но сохраняй всю инженерно важную информацию. "
    "Не добавляй факты, которых нет во входных данных. "
    "importance от 1 до 5: 1 временное, 3 полезный рабочий контекст, 5 архитектура или долговременная память.",
)
LIBRARIAN_WIKI_TASK_PROMPT = os.environ.get(
    "ARCHIVE_LIBRARIAN_WIKI_TASK_PROMPT",
    "Обнови wiki memory ArchiveOfHeresy по свежим сообщениям. "
    "Wiki memory хранит долговременные актуальные знания: принятые решения, архитектуру, предпочтения пользователя, "
    "устойчивые факты проекта, важные статусы, договоренности, открытые вопросы и отмененные решения. "
    "Для агентного режима Tool result является фактическим журналом выполненной работы: сохраняй устойчивые итоги, "
    "созданные/измененные артефакты, ошибки, найденные причины и принятые решения, но не копируй весь вывод подряд. "
    "Рассортируй информацию по тематическим страницам. Если новые сообщения меняют старое решение, "
    "интегрируй новое как актуальное, а старое пометь как superseded только если это полезно для истории. "
    "Не копируй переписку подряд и не складывай противоречия без статуса актуальности. "
    "Пиши сухо, кратко, проверяемо. Не добавляй фактов, которых нет во входных данных.",
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


class FocusBookshelf:
    def __init__(self, root):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        self.files_dir.mkdir(parents=True, exist_ok=True)

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

    def catalog(self, index):
        return {
            "active_id": index.get("active_id"),
            "max_focus_files": MAX_FOCUS_FILES,
            "books": [
                {
                    "id": focus.get("id"),
                    "title": focus.get("title"),
                    "status": focus.get("status"),
                    "importance": focus.get("importance"),
                    "created_at": focus.get("created_at"),
                    "updated_at": focus.get("updated_at"),
                }
                for focus in index.get("files", [])
            ],
        }

    def active_focus(self, index):
        active_id = index.get("active_id")
        for focus in index.get("files", []):
            if focus.get("id") == active_id and focus.get("status") == "active":
                return focus
        return None

    def read_focus(self, focus):
        if not focus:
            return ""
        path = self.root / focus.get("path", "")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

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

    def create_empty_focus(self, index, title, importance=3, conversation_id=None, turn_id=None, reason=None):
        created_at = now_iso()
        focus_id = str(uuid.uuid4())
        file_name = f"{created_at[:10]}-{safe_slug(title)}-{focus_id[:8]}.md"
        focus = {
            "id": focus_id,
            "title": title,
            "path": str((self.files_dir / file_name).relative_to(self.root)),
            "status": "active",
            "importance": clamp_importance(importance),
            "created_at": created_at,
            "updated_at": created_at,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "created_by": "magos",
            "needs_librarian_fill": "true",
        }
        index.setdefault("files", []).append(focus)
        summary = (
            "Magos opened this focus before the model answer because the current request appears to need "
            "a new or fully refreshed topic context. The librarian must populate this focus after the answer."
        )
        if reason:
            summary += f"\n\nMagos reason: {reason}"
        self.write_focus_file(focus, summary, "", "")
        return focus

    def update_focus(self, focus, record, decision, user_text, assistant_text):
        focus["status"] = "active"
        focus["title"] = decision["title"] or focus.get("title") or "Focus"
        focus["importance"] = max(clamp_importance(focus.get("importance")), decision["importance"])
        focus["updated_at"] = now_iso()
        focus["conversation_id"] = record.get("conversation_id")
        focus["turn_id"] = record.get("turn_id")
        if focus.get("created_by") == "magos":
            focus["needs_librarian_fill"] = "false"
        self.write_focus_file(focus, decision["summary"], user_text, assistant_text)
        return focus

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
        ]
        if focus.get("created_by"):
            body.append(f"created_by: {focus.get('created_by')}")
        if focus.get("needs_librarian_fill"):
            body.append(f"needs_librarian_fill: {focus.get('needs_librarian_fill')}")
        body.extend(
            [
                "---",
                "",
                "# Focus",
                "",
                trim_text(summary, 8000),
                "",
                "## Last Exchange",
                "",
                "User:",
                trim_text(user_text, 1800),
                "",
                "Assistant:",
                trim_text(assistant_text, 2600),
                "",
            ]
        )
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


class Librarian:
    def __init__(
        self,
        focus_root,
        proxy_json,
        wiki_root=None,
        sqlite_path=None,
        vector_memory=None,
        graph_memory=None,
        memory_namespace="default",
    ):
        self.bookshelf = FocusBookshelf(focus_root)
        self.proxy_json = proxy_json
        self.memory_namespace = str(memory_namespace or "default")
        self.wiki_memory = (
            WikiMemory(wiki_root, proxy_json, sqlite_path, memory_namespace=self.memory_namespace)
            if wiki_root and sqlite_path
            else None
        )
        self.vector_memory = vector_memory
        self.graph_memory = graph_memory

    def process_turn(self, record):
        if record.get("status") != "ok":
            return {"status": "skipped", "reason": "turn_not_ok"}
        if record.get("conversation_id") == "archive-librarian":
            return {"status": "skipped", "reason": "archive_librarian"}

        user_text = latest_user_message(record.get("request", {}).get("messages", []))
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        if not user_text or not assistant_text:
            return {"status": "skipped", "reason": "empty_exchange"}

        vector_chunks = 0
        if self.vector_memory is not None:
            vector_chunks = self.vector_memory.index_turn(record)

        index = self.bookshelf.load_index()
        active = self.bookshelf.active_focus(index)
        decision = self.agent_cycle(record, index, active, user_text, assistant_text)

        if not active or decision["action"] == "new":
            if active:
                self.bookshelf.pause_focus(active)
            focus = self.bookshelf.create_focus(index, record, decision, user_text, assistant_text)
        else:
            focus = self.bookshelf.update_focus(active, record, decision, user_text, assistant_text)

        index["active_id"] = focus["id"]
        self.bookshelf.enforce_limit(index)
        self.bookshelf.save_index(index)
        wiki_result = None
        if self.wiki_memory is not None:
            wiki_result = self.wiki_memory.process_turn(record)
        graph_result = None
        if self.graph_memory is not None:
            graph_result = self.graph_memory.process_turn(record)
        return {
            "status": "ok",
            "memory_namespace": self.memory_namespace,
            "vector_chunks": vector_chunks,
            "focus": {
                "action": decision["action"],
                "id": focus.get("id"),
                "title": focus.get("title"),
                "importance": focus.get("importance"),
            },
            "wiki": wiki_result,
            "graph": graph_result,
        }

    def agent_cycle(self, record, index, active, user_text, assistant_text):
        messages = [
            {"role": "system", "content": LIBRARIAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(self.agent_task(index, user_text, assistant_text), ensure_ascii=False),
            },
        ]

        for _step in range(MAX_AGENT_STEPS):
            try:
                decision = self.ask_agent(record, messages)
            except Exception:
                return self.fallback_decision(active, user_text, assistant_text)
            tool = str(decision.get("tool") or "").strip()

            if tool == "read_active_focus":
                observation = {
                    "tool": "read_active_focus",
                    "active_id": active.get("id") if active else None,
                    "content": self.bookshelf.read_focus(active)[-6000:] if active else "",
                }
                messages.append(
                    {
                        "role": "user",
                        "content": "TOOL_RESULT " + json.dumps(observation, ensure_ascii=False),
                    }
                )
                continue

            if tool == "finish":
                return self.normalize_decision(decision, active, user_text, assistant_text)

            messages.append(
                {
                    "role": "user",
                    "content": "TOOL_RESULT "
                    + json.dumps(
                        {
                            "error": "unknown_tool",
                            "available_tools": ["read_active_focus", "finish"],
                        },
                        ensure_ascii=False,
                    ),
                }
            )

        return self.fallback_decision(active, user_text, assistant_text)

    def agent_task(self, index, user_text, assistant_text):
        return {
            "bookshelf_catalog": self.bookshelf.catalog(index),
            "new_exchange": {"user": user_text, "assistant": assistant_text},
            "task": LIBRARIAN_TASK_PROMPT,
            "physical_isolation": (
                "Ты не видишь память напрямую. Работай только с каталогом и результатами инструментов. "
                "Если нужно содержимое активной книги, запроси read_active_focus."
            ),
            "available_tools": {
                "read_active_focus": {
                    "description": "Read the currently active focus book. Use before continuing an existing topic.",
                    "arguments": {},
                },
                "finish": {
                    "description": "Finish the agent cycle with the desired bookshelf action.",
                    "arguments": {
                        "action": "continue|new",
                        "title": "short topic title",
                        "importance": "integer 1..5",
                        "summary": "compact focus book body for the current topic",
                    },
                },
            },
            "rules": [
                "Return exactly one JSON object.",
                "To use a tool, return {\"tool\":\"read_active_focus\"}.",
                "To finish, return {\"tool\":\"finish\",\"action\":\"continue|new\",\"title\":\"...\",\"importance\":1..5,\"summary\":\"...\"}.",
                "If there is an active book and the new exchange may belong to it, read it before finish.",
                "Keep summary compact but preserve all useful facts, decisions, constraints, names, paths, commands, and next steps for the current topic.",
                "Do not imitate the assistant persona from the conversation.",
                "Do not use the user's conversational style as your own style.",
            ],
        }

    def ask_agent(self, record, messages):
        payload = {
            "model": record.get("model") or LIBRARIAN_MODEL,
            "user": "archive-librarian",
            "messages": messages,
            "max_tokens": 900,
            "temperature": 0.1,
        }

        _status, response = self.proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=180)
        content = response["choices"][0]["message"].get("content", "")
        return extract_json(content)

    def fallback_decision(self, active, user_text, assistant_text):
        return {
            "action": "continue" if active else "new",
            "title": active.get("title") if active else user_text[:80],
            "importance": active.get("importance") if active else 3,
            "summary": f"Пользователь: {user_text}\nМодель: {assistant_text}",
        }

    def normalize_decision(self, decision, active, user_text, assistant_text):
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


class WikiBookshelf:
    def __init__(self, root):
        self.root = Path(root)
        self.pages_dir = self.root / "pages"
        self.index_path = self.root / "index.json"
        self.state_path = self.root / "state.json"
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def load_index(self):
        if not self.index_path.exists():
            return {"version": 1, "pages": []}
        try:
            with self.index_path.open(encoding="utf-8") as source:
                index = json.load(source)
        except Exception:
            return {"version": 1, "pages": []}
        index.setdefault("version", 1)
        index.setdefault("pages", [])
        return index

    def save_index(self, index):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = self.index_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as target:
            json.dump(index, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
        tmp_path.replace(self.index_path)

    def load_state(self):
        if not self.state_path.exists():
            return {"version": 1, "pending_messages": 0, "last_sync_at": None, "last_sync_turn_id": None}
        try:
            with self.state_path.open(encoding="utf-8") as source:
                state = json.load(source)
        except Exception:
            return {"version": 1, "pending_messages": 0, "last_sync_at": None, "last_sync_turn_id": None}
        state.setdefault("version", 1)
        state.setdefault("pending_messages", 0)
        state.setdefault("last_sync_at", None)
        state.setdefault("last_sync_turn_id", None)
        return state

    def save_state(self, state):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as target:
            json.dump(state, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
        tmp_path.replace(self.state_path)

    def catalog(self, index):
        return {
            "pages": [
                {
                    "id": page.get("id"),
                    "title": page.get("title"),
                    "kind": page.get("kind"),
                    "importance": page.get("importance"),
                    "created_at": page.get("created_at"),
                    "updated_at": page.get("updated_at"),
                }
                for page in index.get("pages", [])
            ]
        }

    def find_page(self, index, page_id=None, title=None):
        title_slug = safe_slug(title or "")
        for page in index.get("pages", []):
            if page_id and page.get("id") == page_id:
                return page
            if title_slug and safe_slug(page.get("title") or "") == title_slug:
                return page
        return None

    def read_page(self, page):
        if not page:
            return ""
        path = self.root / page.get("path", "")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def upsert_page(self, index, update, record):
        title = str(update.get("title") or "Wiki Page").strip()
        page = self.find_page(index, update.get("id"), title)
        created = False
        if not page:
            created = True
            page_id = str(uuid.uuid4())
            file_name = f"{safe_slug(title)}-{page_id[:8]}.md"
            page = {
                "id": page_id,
                "title": title,
                "kind": str(update.get("kind") or "note").strip() or "note",
                "importance": clamp_importance(update.get("importance")),
                "path": str((self.pages_dir / file_name).relative_to(self.root)),
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "turn_id": record.get("turn_id"),
            }
            index.setdefault("pages", []).append(page)

        page["title"] = title
        page["kind"] = str(update.get("kind") or page.get("kind") or "note").strip() or "note"
        page["importance"] = max(clamp_importance(page.get("importance")), clamp_importance(update.get("importance")))
        page["updated_at"] = now_iso()
        page["turn_id"] = record.get("turn_id")
        self.write_page(page, update.get("body") or update.get("summary") or "", created=created)
        return page

    def write_page(self, page, body, created=False):
        path = self.root / page["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        title = page.get("title") or "Wiki Page"
        content = [
            "---",
            f"id: {page['id']}",
            f"title: {title}",
            f"kind: {page.get('kind')}",
            f"importance: {page.get('importance')}",
            f"created_at: {page.get('created_at')}",
            f"updated_at: {page.get('updated_at')}",
            f"turn_id: {page.get('turn_id')}",
            "---",
            "",
            f"# {title}",
            "",
            trim_text(body, 12000),
            "",
        ]
        path.write_text("\n".join(content), encoding="utf-8")


class WikiMemory:
    def __init__(self, root, proxy_json, sqlite_path, memory_namespace="default"):
        self.bookshelf = WikiBookshelf(root)
        self.proxy_json = proxy_json
        self.sqlite_path = Path(sqlite_path)
        self.memory_namespace = str(memory_namespace or "default")

    def process_turn(self, record):
        if record.get("status") != "ok":
            return {"status": "skipped", "reason": "turn_not_ok"}
        if record.get("conversation_id") == "archive-librarian":
            return {"status": "skipped", "reason": "archive_librarian"}

        user_text = latest_user_message(record.get("request", {}).get("messages", []))
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        message_count = int(bool(user_text)) + int(bool(assistant_text))
        if not message_count:
            return {"status": "skipped", "reason": "empty_exchange"}

        state = self.bookshelf.load_state()
        state["pending_messages"] = int(state.get("pending_messages") or 0) + message_count
        if state["pending_messages"] < WIKI_INTERVAL_MESSAGES:
            self.bookshelf.save_state(state)
            return {"status": "pending", "pending_messages": state["pending_messages"]}

        index = self.bookshelf.load_index()
        recent_turns = self.recent_turns(state.get("last_sync_at"))
        if not recent_turns:
            state["pending_messages"] = 0
            state["last_sync_at"] = record.get("created_at")
            state["last_sync_turn_id"] = record.get("turn_id")
            self.bookshelf.save_state(state)
            return {"status": "skipped", "reason": "no_recent_turns"}

        decision = self.agent_cycle(record, index, recent_turns)
        updated_pages = []
        for update in decision.get("page_updates", []):
            if str(update.get("operation") or "upsert").lower() == "upsert":
                page = self.bookshelf.upsert_page(index, update, record)
                updated_pages.append({"id": page.get("id"), "title": page.get("title")})

        self.bookshelf.save_index(index)
        state["pending_messages"] = 0
        state["last_sync_at"] = record.get("created_at")
        state["last_sync_turn_id"] = record.get("turn_id")
        self.bookshelf.save_state(state)
        return {"status": "synced", "updated_pages": updated_pages, "recent_turns": len(recent_turns)}

    def recent_turns(self, last_sync_at):
        if not self.sqlite_path.exists():
            return []

        sql = """
            SELECT id, conversation_id, created_at, request_json, response_json
            FROM turns
            WHERE status = 'ok'
        """
        params = []
        with sqlite3.connect(self.sqlite_path) as db:
            turn_columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
        if "memory_namespace" in turn_columns:
            sql += " AND memory_namespace = ?"
            params.append(self.memory_namespace)
        if last_sync_at:
            sql += " AND created_at > ?"
            params.append(last_sync_at)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(WIKI_MAX_RECENT_TURNS)

        rows = []
        with sqlite3.connect(self.sqlite_path) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(sql, params):
                rows.append(dict(row))
        rows.reverse()

        turns = []
        for row in rows:
            try:
                request = json.loads(row.get("request_json") or "{}")
            except json.JSONDecodeError:
                request = {}
            try:
                response = json.loads(row.get("response_json") or "{}")
            except json.JSONDecodeError:
                response = {}
            turns.append(
                {
                    "turn_id": row.get("id"),
                    "conversation_id": row.get("conversation_id"),
                    "created_at": row.get("created_at"),
                    "user": latest_user_message(request.get("messages", [])),
                    "assistant": trim_text(str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""), 2200),
                }
            )
        return turns

    def agent_cycle(self, record, index, recent_turns):
        messages = [
            {"role": "system", "content": LIBRARIAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(self.agent_task(index, recent_turns), ensure_ascii=False),
            },
        ]

        for _step in range(MAX_AGENT_STEPS + 2):
            try:
                decision = self.ask_agent(record, messages)
            except Exception:
                return self.fallback_decision(recent_turns)
            tool = str(decision.get("tool") or "").strip()

            if tool == "read_wiki_page":
                page = self.bookshelf.find_page(index, page_id=decision.get("id"), title=decision.get("title"))
                observation = {
                    "tool": "read_wiki_page",
                    "id": page.get("id") if page else None,
                    "title": page.get("title") if page else decision.get("title"),
                    "content": self.bookshelf.read_page(page)[-8000:] if page else "",
                }
                messages.append({"role": "user", "content": "TOOL_RESULT " + json.dumps(observation, ensure_ascii=False)})
                continue

            if tool == "finish":
                return self.normalize_decision(decision)

            messages.append(
                {
                    "role": "user",
                    "content": "TOOL_RESULT "
                    + json.dumps(
                        {
                            "error": "unknown_tool",
                            "available_tools": ["read_wiki_page", "finish"],
                        },
                        ensure_ascii=False,
                    ),
                }
            )

        return self.fallback_decision(recent_turns)

    def agent_task(self, index, recent_turns):
        return {
            "wiki_catalog": self.bookshelf.catalog(index),
            "recent_turns": recent_turns,
            "task": LIBRARIAN_WIKI_TASK_PROMPT,
            "physical_isolation": (
                "Ты не видишь wiki-страницы напрямую. Работай только с каталогом и результатами инструментов. "
                "Если нужно проверить существующую страницу перед обновлением, запроси read_wiki_page."
            ),
            "available_tools": {
                "read_wiki_page": {
                    "description": "Read an existing wiki page by id or title before updating it.",
                    "arguments": {"id": "optional page id", "title": "optional page title"},
                },
                "finish": {
                    "description": "Finish the wiki sorting cycle.",
                    "arguments": {
                        "page_updates": [
                            {
                                "operation": "upsert",
                                "id": "optional existing page id",
                                "title": "page title",
                                "kind": "project|decision|preference|topic|entity|note",
                                "importance": "integer 1..5",
                                "body": "complete updated markdown body for the page",
                            }
                        ]
                    },
                },
            },
            "rules": [
                "Return exactly one JSON object.",
                "To use a tool, return {\"tool\":\"read_wiki_page\",\"id\":\"...\"} or {\"tool\":\"read_wiki_page\",\"title\":\"...\"}.",
                "To finish, return {\"tool\":\"finish\",\"page_updates\":[...]}",
                "Read an existing page before overwriting it when the catalog suggests the topic already exists.",
                "Each page body must represent the current integrated state, not a raw transcript.",
                "Use explicit sections such as Current Facts, Active Decisions, Superseded, Open Questions, Next Steps when useful.",
            ],
        }

    def ask_agent(self, record, messages):
        payload = {
            "model": record.get("model") or LIBRARIAN_MODEL,
            "user": "archive-librarian",
            "messages": messages,
            "max_tokens": 1800,
            "temperature": 0.1,
        }

        _status, response = self.proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=240)
        content = response["choices"][0]["message"].get("content", "")
        return extract_json(content)

    def fallback_decision(self, recent_turns):
        if not recent_turns:
            return {"page_updates": []}
        lines = ["## Current Facts", ""]
        for turn in recent_turns:
            user = trim_text(turn.get("user"), 500)
            if user:
                lines.append(f"- {turn.get('created_at')}: {user}")
        return {
            "page_updates": [
                {
                    "operation": "upsert",
                    "title": "Unsorted Conversation Notes",
                    "kind": "note",
                    "importance": 2,
                    "body": "\n".join(lines),
                }
            ]
        }

    def normalize_decision(self, decision):
        updates = []
        for raw in decision.get("page_updates") or []:
            title = str(raw.get("title") or "").strip()
            body = str(raw.get("body") or raw.get("summary") or "").strip()
            if not title or not body:
                continue
            updates.append(
                {
                    "operation": "upsert",
                    "id": raw.get("id"),
                    "title": title[:120],
                    "kind": str(raw.get("kind") or "note").strip()[:40] or "note",
                    "importance": clamp_importance(raw.get("importance")),
                    "body": trim_text(body, 12000),
                }
            )
        return {"page_updates": updates}

#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

from archivist_agent.agent import FocusBookshelf, clamp_importance, extract_json, now_iso, trim_text
from archivist_agent.vector_memory import VECTOR_TOP_K, latest_user_message, tokenize
from archivist_agent.graph_memory import GRAPH_TOP_K
from semantic_memory import SEMANTIC_MIN_SCORE, semantic_scores


MAGOS_MODEL = os.environ.get(
    "ARCHIVE_MAGOS_MODEL",
    os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"),
)
MAGOS_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_MAGOS_CONTEXT_CHARS", "6000"))
# Minimum token/chargram overlap between the curated memory_context and the raw
# retrieved layers. Below this the context is an ungrounded paraphrase of the
# query (the model "curated" facts that are not in memory) and must be dropped.
MAGOS_GROUNDING_MIN_OVERLAP = float(os.environ.get("ARCHIVE_MAGOS_GROUNDING_MIN_OVERLAP", "0.2"))
MAGOS_MIN_WIKI_SCORE = float(os.environ.get("ARCHIVE_MAGOS_MIN_WIKI_SCORE", "0.35"))
MAGOS_MIN_VECTOR_SCORE = float(os.environ.get("ARCHIVE_MAGOS_MIN_VECTOR_SCORE", "0.32"))
# The "middle memory" replacing the focus file: this many chunks of the current
# conversation, in the band just before the verbatim tail (offset skips what the
# tail already carries), ungated by similarity. Kept small to bound the prompt.
MAGOS_SESSION_RECENT = int(os.environ.get("ARCHIVE_MAGOS_SESSION_RECENT", "8"))
MAGOS_SESSION_TAIL_SKIP = int(os.environ.get("ARCHIVE_MAGOS_SESSION_TAIL_SKIP", "6"))
MAGOS_SESSION_CHUNK_CHARS = int(os.environ.get("ARCHIVE_MAGOS_SESSION_CHUNK_CHARS", "320"))
MAGOS_MIN_GRAPH_SCORE = float(os.environ.get("ARCHIVE_MAGOS_MIN_GRAPH_SCORE", "0.12"))
MAGOS_ENABLED = os.environ.get("ARCHIVE_MAGOS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
MAGOS_CONTEXT_LAYERS = {
    layer.strip().lower()
    for layer in os.environ.get("ARCHIVE_MAGOS_CONTEXT_LAYERS", "").split(",")
    if layer.strip()
}
MAGOS_CONTEXT_LAYERS &= {"wiki", "vector", "graph"}
# Namespaces searched in ADDITION to the chat's own: brigade/agent work must be
# visible to the persona, otherwise finished department tasks stay siloed.
MAGOS_EXTRA_NAMESPACES = {
    ns.strip().lower()
    for ns in os.environ.get("ARCHIVE_MAGOS_EXTRA_NAMESPACES", "agent").split(",")
    if ns.strip()
}
MAGOS_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_MAGOS_SYSTEM_PROMPT",
    "Ты Магос ArchiveOfHeresy: изолированный агент извлечения памяти перед ответом модели. "
    "Ты не Шушуня и не архивариус-писатель после ответа. "
    "Твоя задача: собрать короткий набор релевантных фактов из памяти для ответа. "
    "Отвечай только валидным JSON без markdown и художественного тона.",
)
MAGOS_TASK_PROMPT = os.environ.get(
    "ARCHIVE_MAGOS_TASK_PROMPT",
    "Собери memory_context: только факты, решения, статусы, связи и ограничения, которые помогут ответу. "
    "Раздел 'Недавнее в этом разговоре' — свежая нить текущего диалога, всегда учитывай его для непрерывности "
    "(что обсуждали, как что назвали, что решили). Раздел 'Похожее из памяти' — ассоциативно найденное старое. "
    "Фрагменты vector_context подписаны эпистемическим ярлыком в квадратных скобках: "
    "[факт] можно передавать как информацию; [мнение] передавай только как мнение владельца, не как истину; "
    "[прикол] — это была шутка или сарказм, не выдавай содержимое за факт; "
    "[ошибка] цитируй только вместе с исправлением, само утверждение неверно; "
    "[болтовня] почти никогда не несёт фактов — пропускай; [задача] — поручение, а не факт о мире; "
    "[без ярлыка] — старая запись, оценивай по содержимому сам. "
    "Фрагменты с пометкой namespace=agent — это работа отделов/бригад Шушуни: выполненные исследования, "
    "созданные файлы и журналы задач; используй их, когда владелец спрашивает о задачах, исследованиях или их результатах. "
    "memory_context разрешено собирать ТОЛЬКО из содержимого полей wiki_context, vector_context, graph_context. "
    "Запрещено пересказывать или переформулировать сам query, запрещены мета-описания вида 'пользователь спрашивает о...'. "
    "Не добавляй ничего из собственных знаний: если про сущность из query в этих полях ничего нет, значит в памяти про неё пусто. "
    "Если связь слабая, косвенная или сомнительная, не добавляй этот фрагмент в memory_context. "
    "Лучше вернуть memory_context пустой строкой, чем подмешать шум.",
)


def safe_title(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:90] or "New Focus"


def token_overlap(left, right):
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    token_score = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
    left_grams = chargrams(left_tokens)
    right_grams = chargrams(right_tokens)
    gram_score = 0.0
    if left_grams and right_grams:
        gram_score = len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))
    return max(token_score, gram_score * 0.75)


def chargrams(tokens, size=3):
    grams = set()
    for token in tokens:
        if len(token) < size + 1:
            continue
        for index in range(0, len(token) - size + 1):
            grams.add(token[index : index + size])
    return grams


class Magos:
    def __init__(self, focus_root, wiki_root, proxy_json, vector_memory=None, graph_memory=None, extra_wiki_roots=None):
        self.focus = FocusBookshelf(focus_root)
        self.wiki_root = Path(wiki_root)
        self.proxy_json = proxy_json
        self.vector_memory = vector_memory
        self.graph_memory = graph_memory
        # {namespace: wiki_root} for brigade/agent namespaces searched in addition to our own
        self.extra_wiki_roots = {str(ns): Path(root) for ns, root in (extra_wiki_roots or {}).items()}
        self.last_result = None

    def prepare_request(self, messages, model=None, conversation_id=None, turn_id=None, memory_namespace="default"):
        self.last_result = None
        try:
            if not MAGOS_ENABLED:
                return None
            query = latest_user_message(messages)
            if not query:
                return None

            wiki_context = self.wiki_context(query) if "wiki" in MAGOS_CONTEXT_LAYERS else ""
            vector_context = (
                self.vector_context(query, memory_namespace=memory_namespace, conversation_id=conversation_id, turn_id=turn_id)
                if "vector" in MAGOS_CONTEXT_LAYERS
                else ""
            )
            graph_context = self.graph_context(query) if "graph" in MAGOS_CONTEXT_LAYERS else ""
            context_sources = [
                name
                for name, value in (
                    ("wiki", wiki_context),
                    ("vector", vector_context),
                    ("graph", graph_context),
                )
                if value
            ]

            decision = self.ask_magos(
                model,
                {
                    "task": MAGOS_TASK_PROMPT,
                    "query": query,
                    "wiki_context": wiki_context,
                    "vector_context": vector_context,
                    "graph_context": graph_context,
                    "enabled_context_layers": sorted(MAGOS_CONTEXT_LAYERS),
                    "schema": {
                        "reason": "short reason",
                        "memory_context": "compact facts to pass into the model",
                    },
                },
            )
            if decision is None:
                # No mechanical fallback: if the model is down here, the answer
                # model (same host) is down too, so there is nothing to serve.
                return None

            self.last_result = {
                "turn_id": turn_id,
                "reason": decision.get("reason"),
                "memory_context_chars": len(decision.get("memory_context") or ""),
                "context_sources": context_sources,
                "enabled_context_layers": sorted(MAGOS_CONTEXT_LAYERS),
            }
            print(
                "Magos decision: "
                + json.dumps(self.last_result, ensure_ascii=False, sort_keys=True),
                flush=True,
            )

            memory_context = trim_text(decision.get("memory_context"), MAGOS_CONTEXT_CHARS)
            if memory_context:
                grounding_sources = " ".join(filter(None, [wiki_context, vector_context, graph_context])).strip()
                grounding = token_overlap(memory_context, grounding_sources) if grounding_sources else 0.0
                if grounding < MAGOS_GROUNDING_MIN_OVERLAP:
                    self.last_result["memory_context_dropped"] = f"ungrounded:{grounding:.2f}"
                    self.last_result["memory_context_chars"] = 0
                    print(
                        f"Magos dropped ungrounded memory_context (overlap {grounding:.2f}): "
                        + memory_context[:160].replace("\n", " "),
                        flush=True,
                    )
                    memory_context = ""
            if not memory_context:
                return None
            return {
                "role": "system",
                "content": (
                    "Magos memory context from ArchiveOfHeresy. "
                    "Это предответная выжимка релевантных фактов из явно включённых нижних слоёв памяти. "
                    "Используй её только если она относится к текущему вопросу.\n\n"
                    f"{memory_context}"
                ),
            }
        except Exception as exc:
            print(f"Magos fail-soft: {exc}", flush=True)
            self.last_result = {"turn_id": turn_id, "error": str(exc), "created_empty_focus": False}
            return None

    def focus_candidates(self, index):
        candidates = []
        for focus in index.get("files", []):
            content = self.focus.read_focus(focus)
            candidates.append(
                {
                    "id": focus.get("id"),
                    "title": focus.get("title"),
                    "status": focus.get("status"),
                    "importance": focus.get("importance"),
                    "updated_at": focus.get("updated_at"),
                    "excerpt": trim_text(content, 1800),
                }
            )
        return candidates

    def wiki_context(self, query, limit=4):
        candidates = []
        for ns_label, root in [("", self.wiki_root)] + sorted(self.extra_wiki_roots.items()):
            index_path = root / "index.json"
            if not index_path.exists():
                continue
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for page in index.get("pages", []):
                if str(page.get("kind") or "").strip().lower() == "persona":
                    continue  # identity pages are always injected separately, not knowledge
                path = root / page.get("path", "")
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8")
                text = " ".join([page.get("title", ""), page.get("kind", ""), content])
                candidates.append((ns_label, page, content, text))
        # Semantic gather: high recall including cross-language and paraphrase
        # (lexical token overlap misses e.g. a Russian query vs an English page).
        # Noise is fine here — the Magos LLM curates only relevant facts downstream.
        # Falls back to lexical when the embedder is unavailable.
        semantic = semantic_scores(query, [(str(i), text[:600]) for i, (_ns, _p, _c, text) in enumerate(candidates)])
        scored = []
        if semantic is not None:
            for i, (ns_label, page, content, _text) in enumerate(candidates):
                score = semantic.get(str(i), 0.0)
                if score >= SEMANTIC_MIN_SCORE:
                    scored.append((score, ns_label, page, content))
        else:
            for ns_label, page, content, text in candidates:
                score = token_overlap(query, text)
                if score >= MAGOS_MIN_WIKI_SCORE:
                    scored.append((score, ns_label, page, content))
        scored.sort(key=lambda item: (-item[0], item[2].get("updated_at") or ""))
        lines = []
        for score, ns_label, page, content in scored[:limit]:
            source = f" [namespace={ns_label}]" if ns_label else ""
            lines.append(f"## {page.get('title')}{source} score={score:.3f}\n{trim_text(content, 1200)}")
        return "\n\n".join(lines)

    def vector_context(self, query, memory_namespace="default", conversation_id=None, turn_id=None):
        if self.vector_memory is None:
            return ""
        namespaces = [memory_namespace] + sorted(ns for ns in MAGOS_EXTRA_NAMESPACES if ns != memory_namespace)
        matches = []
        for namespace in namespaces:
            matches.extend(
                self.vector_memory.search(
                    query,
                    limit=VECTOR_TOP_K,
                    min_score=MAGOS_MIN_VECTOR_SCORE,
                    memory_namespace=namespace,
                    exclude_turn_id=turn_id,
                )
            )
        matches.sort(key=lambda item: (-item["score"], item["created_at"]))
        matches = matches[:VECTOR_TOP_K]

        sections = []
        # Recent thread memory: the current conversation's latest chunks, by time,
        # ungated by similarity — the reliable replacement for the focus file.
        recent = self.vector_memory.recent_session_chunks(
            conversation_id,
            limit=MAGOS_SESSION_RECENT,
            offset=MAGOS_SESSION_TAIL_SKIP,
            memory_namespace=memory_namespace,
            exclude_turn_id=turn_id,
        )
        seen = set()
        if recent:
            lines = ["# Недавнее в этом разговоре (нить перед последними репликами)", ""]
            for chunk in recent:
                seen.add(f"{chunk['created_at']}:{chunk['role']}")
                label = str(chunk.get("label") or "").strip() or "без ярлыка"
                lines.append(
                    f"[{label}] {chunk['role']}: " + trim_text(chunk["content"], MAGOS_SESSION_CHUNK_CHARS).replace(chr(10), " ")
                )
            sections.append("\n".join(lines))

        relevant = [m for m in matches if f"{m['created_at']}:{m['role']}" not in seen]
        if relevant:
            lines = ["# Похожее из памяти (по смыслу)", ""]
            for index, match in enumerate(relevant, 1):
                label = str(match.get("label") or "").strip() or "без ярлыка"
                source = str(match.get("memory_namespace") or "")
                source_note = f"; namespace={source}" if source and source != memory_namespace else ""
                lines.append(
                    f"{index}. [{label}] score={match['score']:.3f}; role={match['role']}; created_at={match['created_at']}{source_note}\n"
                    f"   {trim_text(match['content'], 700).replace(chr(10), chr(10) + '   ')}"
                )
            sections.append("\n\n".join(lines))
        return "\n\n".join(sections)

    def graph_context(self, query):
        if self.graph_memory is None:
            return ""
        result = self.graph_memory.search(query, limit=GRAPH_TOP_K)
        nodes = [node for node in result.get("nodes", []) if float(node.get("score") or 0) >= MAGOS_MIN_GRAPH_SCORE]
        if not nodes:
            return ""
        node_ids = {node.get("id") for node in nodes}
        edges = [
            edge
            for edge in result.get("edges", [])
            if edge.get("source_id") in node_ids or edge.get("target_id") in node_ids
        ]
        lines = ["# GraphRAG Memory", "", "## Nodes"]
        for node in nodes:
            lines.append(
                f"- {node['name']} ({node['kind']}, score={node['score']:.3f}, status={node['status']}): "
                f"{trim_text(node['summary'], 400)}"
            )
        if edges:
            lines.extend(["", "## Relations"])
            for edge in edges[: GRAPH_TOP_K * 2]:
                lines.append(
                    f"- {edge['source_name']} --{edge['relation']}--> {edge['target_name']} "
                    f"(status={edge['status']}, weight={edge['weight']}): {trim_text(edge['summary'], 300)}"
                )
        return "\n".join(lines)

    def ask_magos(self, model, task):
        payload = {
            "model": model or MAGOS_MODEL,
            "user": "archive-magos",
            "messages": [
                {"role": "system", "content": MAGOS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 1400,
            "temperature": 0.1,
        }
        try:
            _status, response = self.proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=180)
            return self.normalize_decision(extract_json(response["choices"][0]["message"].get("content", "")))
        except Exception:
            return None

    def normalize_decision(self, decision):
        action = str(decision.get("focus_action") or "").strip().lower()
        if action not in ("use_existing", "new_empty", "keep_active"):
            action = "keep_active"
        return {
            "focus_action": action,
            "focus_id": str(decision.get("focus_id") or "").strip(),
            "new_title": safe_title(decision.get("new_title")),
            "new_importance": clamp_importance(decision.get("new_importance")),
            "reason": trim_text(decision.get("reason"), 500),
            "memory_context": trim_text(decision.get("memory_context"), MAGOS_CONTEXT_CHARS),
        }

    def apply_focus_decision(self, index, decision, conversation_id, turn_id):
        active = self.focus.active_focus(index)
        target = None
        created_empty_focus = False
        if decision["focus_action"] == "use_existing":
            for focus in index.get("files", []):
                if focus.get("id") == decision.get("focus_id"):
                    target = focus
                    break
        elif decision["focus_action"] == "new_empty":
            target = self.focus.create_empty_focus(
                index,
                decision.get("new_title") or "New Focus",
                importance=decision.get("new_importance") or 3,
                conversation_id=conversation_id,
                turn_id=turn_id,
                reason=decision.get("reason"),
            )
            created_empty_focus = True

        if target:
            if active and active.get("id") != target.get("id"):
                self.focus.pause_focus(active)
            self.activate_focus(target)
            index["active_id"] = target.get("id")
            self.focus.enforce_limit(index)
            self.focus.save_index(index)
        return {"focus_id": target.get("id") if target else None, "created_empty_focus": created_empty_focus}

    def abandon_created_focus(self, turn_id, reason):
        index = self.focus.load_index()
        changed = False
        for focus in index.get("files", []):
            if (
                focus.get("created_by") == "magos"
                and focus.get("needs_librarian_fill") == "true"
                and focus.get("turn_id") == turn_id
                and focus.get("status") == "active"
            ):
                focus["status"] = "paused"
                focus["updated_at"] = now_iso()
                path = self.focus.root / focus.get("path", "")
                if path.exists():
                    text = path.read_text(encoding="utf-8")
                    text = re.sub(r"^status: .*$", "status: paused", text, count=1, flags=re.MULTILINE)
                    text = re.sub(
                        r"^updated_at: .*$",
                        f"updated_at: {focus['updated_at']}",
                        text,
                        count=1,
                        flags=re.MULTILINE,
                    )
                    text = text.rstrip() + f"\n\n## Magos Abandoned\n\n{trim_text(reason, 500)}\n"
                    path.write_text(text, encoding="utf-8")
                if index.get("active_id") == focus.get("id"):
                    index["active_id"] = None
                changed = True
                print(f"Magos abandoned empty focus {focus.get('id')}: {reason}", flush=True)
        if changed:
            self.focus.save_index(index)

    def activate_focus(self, focus):
        focus["status"] = "active"
        focus["updated_at"] = now_iso()
        path = self.focus.root / focus.get("path", "")
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^status: .*$", "status: active", text, count=1, flags=re.MULTILINE)
        text = re.sub(
            r"^updated_at: .*$",
            f"updated_at: {focus['updated_at']}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        path.write_text(text, encoding="utf-8")

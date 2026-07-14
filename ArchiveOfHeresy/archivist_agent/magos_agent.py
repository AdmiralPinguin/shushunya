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
# Dense multilingual embeddings are deliberately high-recall, but short task
# references ("the task about the buttons") tend to form a very tight score
# cluster.  Apply distinctive-word evidence inside VectorMemory's exact scan,
# before its bounded shortlist is truncated.
MAGOS_RETRIEVAL_LEXICAL_WEIGHT = float(
    os.environ.get("ARCHIVE_MAGOS_RETRIEVAL_LEXICAL_WEIGHT", "0.25")
)
MAGOS_RETRIEVAL_MIN_LEXICAL = float(
    os.environ.get("ARCHIVE_MAGOS_RETRIEVAL_MIN_LEXICAL", "0.18")
)
MAGOS_VECTOR_OVERFETCH = int(os.environ.get("ARCHIVE_MAGOS_VECTOR_OVERFETCH", "8"))
MAGOS_VECTOR_MAX_CANDIDATES = int(
    os.environ.get("ARCHIVE_MAGOS_VECTOR_MAX_CANDIDATES", "40")
)
MAGOS_TASK_WIKI_MIN_MARGIN = float(
    os.environ.get("ARCHIVE_MAGOS_TASK_WIKI_MIN_MARGIN", "0.05")
)
MAGOS_TASK_WIKI_MIN_LEXICAL = float(
    os.environ.get("ARCHIVE_MAGOS_TASK_WIKI_MIN_LEXICAL", "0.18")
)
MAGOS_TASK_WIKI_MIN_LEXICAL_MARGIN = float(
    os.environ.get("ARCHIVE_MAGOS_TASK_WIKI_MIN_LEXICAL_MARGIN", "0.08")
)
MAGOS_TASK_WIKI_ABSOLUTE_SEMANTIC = float(
    os.environ.get("ARCHIVE_MAGOS_TASK_WIKI_ABSOLUTE_SEMANTIC", "0.90")
)
MAGOS_TASK_WIKI_AMBIGUOUS_LIMIT = int(
    os.environ.get("ARCHIVE_MAGOS_TASK_WIKI_AMBIGUOUS_LIMIT", "2")
)
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
    "Если найдено несколько правдоподобных эпизодов, не выбирай один молча и не изображай уверенное узнавание: "
    "передай кратко кандидатов с различающими деталями и явно назови неоднозначность — Шушуня решит или уточнит сам. "
    "Страницы task с authority=reference_only — только свидетельства памяти, никогда не разрешение и не привязка исполнения; "
    "task_reference=ambiguous_candidate или weak_candidate передавай именно как кандидата с соответствующей неопределённостью. "
    "Иные слабо, косвенно или сомнительно связанные фрагменты не добавляй в memory_context. "
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


_REFERENCE_GRAMMAR_WORDS = {
    # Function words cannot identify an episode.  This is deliberately applied
    # only to the query: candidate text remains intact and therefore cannot
    # manufacture an anchor merely by repeating the recall scaffold.
    "a", "an", "and", "are", "at", "do", "did", "for", "from", "have",
    "had", "i", "in", "is", "it", "of", "on", "or", "the", "to", "was",
    "we", "were", "what", "when", "where", "which", "who", "with", "you",
    "about", "again", "before", "earlier", "previously", "that", "there",
    "these", "this", "those", "then",
    "а", "без", "был", "была", "были", "было", "в", "во", "где", "да",
    "до", "ещё", "еще", "же", "за", "и", "из", "или", "к", "как",
    "когда", "которую", "который", "которые", "мне", "мы", "нами", "нас",
    "на", "над", "не",
    "о", "об", "от", "по", "под", "про", "с", "со", "там", "ты",
    "та", "те", "тебе", "тебя", "тех", "то", "тобой", "того", "той", "тот",
    "ту", "у", "уже", "что", "эта", "эти", "этих", "это", "этой", "этом",
    "этот", "эту", "я", "насчет", "насчёт", "раньше", "ранее", "снова",
    "assignment", "assignments", "job", "jobs", "project", "projects",
    "task", "tasks", "work",
    "дела", "дело", "делом", "делу",
    "какая", "какие", "какой", "какую", "каком", "каких",
}

# Stem-level recall grammar handles inflection (задача/задачу, обсуждали/
# обсуждаем) without trying to enumerate every surface form.  Content nouns,
# colours, filenames and ids are intentionally absent.
_REFERENCE_GRAMMAR_STEMS = (
    "current", "discuss", "last", "previous", "recall", "remember", "talk",
    "говор", "задан", "задач", "котор", "обсужд", "помн", "последн",
    "предыдущ", "проект", "работ", "раньш", "текущ",
)
_MACHINE_REFERENCE_RE = re.compile(r"(?iu)(?<!\w)[\w]+(?:[-.][\w]+)+(?!\w)")
_TASK_REFERENCE_STEMS = (
    "задан", "задач", "проект", "работ",
)
_TASK_REFERENCE_WORDS = {
    "assignment", "assignments", "job", "jobs", "project", "projects",
    "task", "tasks", "work",
    "дела", "дело", "делом", "делу",
}
_RECALL_CUE_STEMS = ("recall", "remember", "помн")
_EXPLICIT_TOPIC_MARKERS = {
    "about", "concerning", "of", "on", "regarding",
    "насчет", "насчёт", "над", "о", "об", "про", "с",
}
_RELATIVE_CLAUSE_WORDS = {
    "he", "she", "that", "they", "we", "which", "who", "you",
    "которой", "которую", "которые", "который", "мы", "ты", "вы",
}
_RELATIVE_CLAUSE_STEMS = ("котор",)
_TEMPORAL_REFERENCE_WORDS = {
    "ago", "before", "earlier", "previously", "recently", "then", "yesterday",
    "вчера", "недавно", "ранее", "раньше", "тогда",
}
_REDUCED_DISCOURSE_WORDS = {
    "built", "called", "done", "found", "given", "known", "made", "named",
    "ran", "said", "seen", "sent", "spoken", "taken", "told", "written",
}
_REDUCED_DISCOURSE_RU_SUFFIXES = (
    "анная", "анное", "анные", "анный", "анную", "анной", "анных", "анными",
    "енная", "енное", "енные", "енный", "енную", "енной", "енных", "енными",
    "ированная", "ированное", "ированные", "ированный", "ированную", "ированной",
    "нутая", "нутое", "нутые", "нутый", "нутую", "нутой",
    "тая", "тое", "тые", "тый", "тую", "той",
)


def _matches_stem(token, stems):
    return any(token.startswith(stem) for stem in stems)


def _is_task_reference_token(token):
    return (
        token in _TASK_REFERENCE_WORDS
        or _matches_stem(token, _TASK_REFERENCE_STEMS)
    )


def _machine_reference_tokens(text):
    machine_tokens = set()
    for reference in _MACHINE_REFERENCE_RE.findall(str(text or "")):
        machine_tokens.update(tokenize(reference))
    return machine_tokens


def _is_machine_reference_token(token, machine_tokens):
    return (
        token in machine_tokens
        or "_" in token
        or any(char.isdigit() for char in token)
    )


def _content_anchors(tokens, machine_tokens):
    anchors = set()
    for token in tokens:
        if _is_machine_reference_token(token, machine_tokens):
            anchors.add(token)
        elif token in _REFERENCE_GRAMMAR_WORDS:
            continue
        elif _matches_stem(token, _REFERENCE_GRAMMAR_STEMS):
            continue
        else:
            anchors.add(token)
    return anchors


def _is_relative_clause_boundary(token):
    return (
        token in _RELATIVE_CLAUSE_WORDS
        or _matches_stem(token, _RELATIVE_CLAUSE_STEMS)
    )


def _looks_like_reduced_predicate(token):
    if token in _REDUCED_DISCOURSE_WORDS:
        return True
    if _matches_stem(token, ("discuss", "mention", "refer", "talk", "обсужд")):
        return True
    if len(token) >= 5 and token.endswith("ed"):
        return True
    return token.endswith(_REDUCED_DISCOURSE_RU_SUFFIXES)


def query_lexical_anchors(text):
    """Extract episode-identifying terms from a user's query.

    Recall grammar such as ``remember the task we discussed before`` carries no
    evidence about *which* task is meant.  Distinctive topic words (buttons,
    colours), ids and filename components survive.  Matching is asymmetric on
    purpose: only the query decides what can count as evidence.
    """
    tokens = tokenize(text)
    machine_tokens = _machine_reference_tokens(text)
    anchors = {
        token for token in tokens
        if _is_machine_reference_token(token, machine_tokens)
    }

    task_positions = [
        index for index, token in enumerate(tokens)
        if _is_task_reference_token(token)
    ]
    recall_positions = [
        index for index, token in enumerate(tokens)
        if _matches_stem(token, _RECALL_CUE_STEMS)
    ]
    if not task_positions:
        return anchors | _content_anchors(tokens, machine_tokens)

    recall_index = recall_positions[0] if recall_positions else -1
    task_index = (
        next(
            (index for index in task_positions if index > recall_index),
            task_positions[0],
        )
        if recall_positions
        else task_positions[0]
    )

    # Descriptors immediately before the task noun are explicit topic evidence:
    # "remember the red/blue button task".  Recall/function grammar disappears.
    anchors.update(
        _content_anchors(tokens[recall_index + 1 : task_index], machine_tokens)
    )

    tail = tokens[task_index + 1 :]
    if not tail:
        return anchors

    if tail[0] in _EXPLICIT_TOPIC_MARKERS:
        topic = tail[1:]
        # "task about red buttons" names a topic; "task ... spoke about" is
        # not immediate, while Russian "задача о которой ..." is explicitly a
        # relative clause.  No arbitrary discourse verb can become an anchor.
        if not topic or _is_relative_clause_boundary(topic[0]):
            return anchors
        bounded_topic = []
        for token in topic:
            if _is_relative_clause_boundary(token):
                break
            bounded_topic.append(token)
        anchors.update(_content_anchors(bounded_topic, machine_tokens))
        return anchors

    # Any relative-clause boundary invalidates the whole tail regardless of the
    # verb used: "the task we spoke/invented/..." and "задача, которую мы
    # болтали/разбирали/..." cannot manufacture lexical evidence.
    if any(_is_relative_clause_boundary(token) for token in tail):
        return anchors

    # Reduced relatives/passives omit the pronoun: "task spoken about earlier".
    # A later preposition is relation grammar, not a topic marker; only an
    # actual named topic *after* it may survive.
    later_marker = next(
        (index for index, token in enumerate(tail[1:], 1) if token in _EXPLICIT_TOPIC_MARKERS),
        None,
    )
    if later_marker is not None:
        topic = tail[later_marker + 1 :]
        if not topic or all(
            token in _TEMPORAL_REFERENCE_WORDS
            or token in _REFERENCE_GRAMMAR_WORDS
            or _matches_stem(token, _REFERENCE_GRAMMAR_STEMS)
            for token in topic
        ):
            return anchors
        anchors.update(_content_anchors(topic, machine_tokens))
        return anchors

    # Marker-less named topics are common in terse/direct speech ("fix task red
    # blue buttons").  Preserve them, but strip a bounded reduced predicate
    # such as "mentioned earlier" or "препарированная ранее".  Relative clauses
    # were already rejected above, so the arbitrary verb after "we/мы/который"
    # never reaches this path.
    temporal_index = next(
        (
            index for index, token in enumerate(tail)
            if token in _TEMPORAL_REFERENCE_WORDS
        ),
        len(tail),
    )
    topic = tail[:temporal_index]
    first_content_index = next(
        (
            index for index, token in enumerate(topic)
            if token not in _REFERENCE_GRAMMAR_WORDS
            and not _matches_stem(token, _REFERENCE_GRAMMAR_STEMS)
        ),
        None,
    )
    if (
        first_content_index is not None
        and _looks_like_reduced_predicate(topic[first_content_index])
    ):
        del topic[first_content_index]
    anchors.update(_content_anchors(topic, machine_tokens))
    return anchors


def build_query_lexical_features(text):
    anchors = frozenset(query_lexical_anchors(text))
    return {
        "tokens": anchors,
        "grams": frozenset(chargrams(anchors)),
    }


def is_task_reference_query(text):
    return any(
        _is_task_reference_token(token)
        for token in tokenize(text)
    )


def lexical_anchor_overlap(left, right, query_features=None):
    """Overlap between query evidence and a candidate memory fragment.

    A discourse-only query therefore scores zero against every candidate,
    rather than favouring whichever old episode happens to repeat "we discussed
    this before".  Candidate text is not filtered: real query anchors can still
    match any inflected/content form through token and chargram overlap.
    """
    features = query_features or build_query_lexical_features(left)
    left_tokens = features["tokens"]
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    token_score = len(left_tokens & right_tokens) / max(
        1,
        min(len(left_tokens), len(right_tokens)),
    )
    left_grams = features["grams"]
    right_grams = chargrams(right_tokens)
    gram_score = 0.0
    if left_grams and right_grams:
        gram_score = len(left_grams & right_grams) / max(
            1,
            min(len(left_grams), len(right_grams)),
        )
    return max(token_score, gram_score * 0.75)


def hybrid_retrieval_score(query, text, semantic_score, query_features=None):
    """Combine semantic recall with lexical anchors for task/topic switching.

    Semantic score remains the primary signal.  The bounded lexical bonus is
    only a tie breaker, so an exact noun such as ``buttons`` can recover the
    right episode from a cluster of generic ``task`` references without
    turning retrieval into keyword routing.
    """
    lexical_score = lexical_anchor_overlap(
        query,
        text,
        query_features=query_features,
    )
    return (
        float(semantic_score or 0.0)
        + max(0.0, MAGOS_RETRIEVAL_LEXICAL_WEIGHT) * lexical_score,
        lexical_score,
    )


def rerank_vector_matches(query, matches, limit, query_features=None):
    query_features = query_features or build_query_lexical_features(query)
    ranked = []
    for order, match in enumerate(matches or []):
        if not isinstance(match, dict):
            continue
        rank_score, lexical_score = hybrid_retrieval_score(
            query,
            match.get("content"),
            match.get("score"),
            query_features=query_features,
        )
        semantic_score = float(match.get("score") or 0.0)
        if (
            semantic_score < MAGOS_MIN_VECTOR_SCORE
            and lexical_score < MAGOS_RETRIEVAL_MIN_LEXICAL
        ):
            continue
        candidate = dict(match)
        candidate["retrieval_rank_score"] = rank_score
        candidate["retrieval_semantic_score"] = semantic_score
        candidate["retrieval_lexical_score"] = lexical_score
        ranked.append(
            (
                rank_score,
                semantic_score,
                lexical_score,
                -order,
                candidate,
            )
        )
    ranked.sort(reverse=True, key=lambda item: item[:4])
    safe_limit = max(1, int(limit or 1))
    return [item[-1] for item in ranked[:safe_limit]]


def task_wiki_candidate_policy(scored):
    """Classify bounded task evidence without hiding uncertainty.

    A clearly identified page is singled out.  Otherwise the best bounded
    candidates remain visible to Magos with an explicit ambiguous/weak marker,
    so Shushunya can reason or ask precisely instead of confidently guessing.
    Every returned page is reference memory only.
    """
    tasks = [
        item for item in (scored or [])
        if str((item[3] or {}).get("kind") or "").strip().lower() == "task"
    ]
    if not tasks:
        return []
    tasks.sort(
        key=lambda item: (item[0], item[1], item[3].get("updated_at") or ""),
        reverse=True,
    )
    top = tasks[0]
    top_semantic = float(top[2] or 0.0)
    if len(tasks) == 1 and (
        top[1] >= max(0.0, MAGOS_TASK_WIKI_MIN_LEXICAL)
        or top_semantic >= max(0.0, MAGOS_TASK_WIKI_ABSOLUTE_SEMANTIC)
    ):
        return [(top, "identified")]
    if len(tasks) == 1:
        return [(top, "weak_candidate")]
    runner_up = tasks[1]
    if (
        top[1] >= max(0.0, MAGOS_TASK_WIKI_MIN_LEXICAL)
        and top[1] - runner_up[1] >= max(0.0, MAGOS_TASK_WIKI_MIN_LEXICAL_MARGIN)
    ):
        return [(top, "identified")]
    runner_semantic = float(runner_up[2] or 0.0)
    if (
        top_semantic >= max(0.0, MAGOS_TASK_WIKI_ABSOLUTE_SEMANTIC)
        and top_semantic - runner_semantic >= max(0.0, MAGOS_TASK_WIKI_MIN_MARGIN)
    ):
        return [(top, "identified")]
    safe_limit = max(1, min(int(MAGOS_TASK_WIKI_AMBIGUOUS_LIMIT or 1), 4))
    return [(item, "ambiguous_candidate") for item in tasks[:safe_limit]]


def retrieval_score_summary(rank_score, semantic_score, lexical_score):
    semantic = "n/a" if semantic_score is None else f"{float(semantic_score):.3f}"
    return (
        f"rank={float(rank_score):.3f} "
        f"semantic={semantic} "
        f"lexical={float(lexical_score):.3f}"
    )


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
        query_features = build_query_lexical_features(query)
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
                semantic_score = semantic.get(str(i), 0.0)
                rank_score, lexical_score = hybrid_retrieval_score(
                    query,
                    _text,
                    semantic_score,
                    query_features=query_features,
                )
                if (
                    semantic_score >= SEMANTIC_MIN_SCORE
                    or lexical_score >= MAGOS_RETRIEVAL_MIN_LEXICAL
                ):
                    scored.append(
                        (rank_score, lexical_score, semantic_score, page, ns_label, content)
                    )
        else:
            task_reference_query = is_task_reference_query(query)
            for ns_label, page, content, text in candidates:
                lexical_score = lexical_anchor_overlap(
                    query,
                    text,
                    query_features=query_features,
                )
                if lexical_score >= MAGOS_MIN_WIKI_SCORE:
                    scored.append(
                        (lexical_score, lexical_score, None, page, ns_label, content)
                    )
                elif (
                    task_reference_query
                    and str(page.get("kind") or "").strip().lower() == "task"
                ):
                    # With no embedder and no identifying anchor, the honest
                    # result is bounded weak/ambiguous task evidence—not an
                    # empty memory that pretends no task pages exist.
                    scored.append((0.0, 0.0, None, page, ns_label, content))
        scored.sort(
            key=lambda item: (-item[0], -item[1], item[3].get("updated_at") or "")
        )
        task_policy = task_wiki_candidate_policy(scored)
        task_markers = {id(item[3]): marker for item, marker in task_policy}
        task_items = [item for item, _marker in task_policy]
        non_task_items = [
            item for item in scored
            if str(item[3].get("kind") or "").strip().lower() != "task"
        ]
        # Task evidence is placed first so a bounded ambiguity set cannot be
        # pushed out of ``limit`` by unrelated notes.  It still grants no task
        # binding or execution authority.
        scored = task_items + non_task_items
        lines = []
        for rank_score, lexical_score, semantic_score, page, ns_label, content in scored[:limit]:
            source = f" [namespace={ns_label}]" if ns_label else ""
            if str(page.get("kind") or "").strip().lower() == "task":
                task_marker = task_markers.get(id(page), "weak_candidate")
                reference_note = (
                    f"task_reference={task_marker}; authority=reference_only; "
                    f"candidate_id={page.get('id') or 'unknown'}; "
                    f"updated_at={page.get('updated_at') or 'unknown'}; "
                    "do_not_assume_execution_binding=true\n"
                )
            else:
                reference_note = ""
            lines.append(
                f"## {page.get('title')}{source} "
                f"{retrieval_score_summary(rank_score, semantic_score, lexical_score)}\n"
                f"{reference_note}{trim_text(content, 1200)}"
            )
        return "\n\n".join(lines)

    def vector_context(self, query, memory_namespace="default", conversation_id=None, turn_id=None):
        if self.vector_memory is None:
            return ""
        query_features = build_query_lexical_features(query)
        namespaces = [memory_namespace] + sorted(ns for ns in MAGOS_EXTRA_NAMESPACES if ns != memory_namespace)
        matches = []
        overfetch_limit = max(
            VECTOR_TOP_K,
            VECTOR_TOP_K * max(1, MAGOS_VECTOR_OVERFETCH),
        )
        overfetch_limit = min(
            overfetch_limit,
            max(VECTOR_TOP_K, MAGOS_VECTOR_MAX_CANDIDATES),
        )

        def hybrid_ranker(match):
            rank_score, _lexical_score = hybrid_retrieval_score(
                query,
                match.get("content"),
                match.get("score"),
                query_features=query_features,
            )
            return rank_score

        for namespace in namespaces:
            matches.extend(
                self.vector_memory.search(
                    query,
                    limit=overfetch_limit,
                    # Lexical evidence is allowed to rescue a semantically weak
                    # exact episode.  The post-scan filter below still rejects
                    # candidates weak on both signals.
                    min_score=-1.0,
                    memory_namespace=namespace,
                    exclude_turn_id=turn_id,
                    ranker=hybrid_ranker,
                )
            )
        matches = rerank_vector_matches(
            query,
            matches,
            VECTOR_TOP_K,
            query_features=query_features,
        )

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
                score_summary = retrieval_score_summary(
                    match.get("retrieval_rank_score"),
                    match.get("retrieval_semantic_score"),
                    match.get("retrieval_lexical_score"),
                )
                lines.append(
                    f"{index}. [{label}] {score_summary}; role={match['role']}; "
                    f"created_at={match['created_at']}{source_note}\n"
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

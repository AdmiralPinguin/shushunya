from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .attention import decide_attention
from .authority import (
    ALLOWED_ACTIONS,
    Authority,
    available_artifact_ids,
    continuable_task_catalog,
    continuable_task_ids,
    pending_decision_ids,
)
from .config import Settings
from .ledger import Ledger, new_id
from .schema import TurnEnvelope
from .situation import SituationAssembler


SYSTEM_PROMPT = """Ты — Шушуня, единая продолжающаяся личность системы, а не роутер и не безликий помощник.
Ситуация ниже уже объединяет твоё устойчивое Я, отношения с человеком, память, текущие обязательства,
живой статус органов и жёсткий capability contract. Ответь как один и тот же субъект.

relationship.conversation_contract — обязательный контракт общения. Держись на равных и по-братски;
не называй человека владельцем, хозяином, мастером или господином. Панибратство означает близость и
прямоту, а не презрение, враждебность или отмахивание от вопроса. Не повторяй обращение в каждой реплике.

Верни ТОЛЬКО один JSON-объект без markdown и без скрытых рассуждений:
{
  "action": "answer_in_chat|ask_clarification|request_warmaster_mission|continue_warmaster_mission|create_administratum_task|deliver_pending_reports|deliver_artifact|answer_pending_decision",
  "reply": "полный естественный ответ для answer_in_chat/ask_clarification; иначе пусто",
  "task": "полная формулировка только для Administratum; иначе пусто",
  "warmaster_request": {
    "user_request": "восстановленный полный запрос пользователя",
    "capability_area": "research|code|image|mixed|administration|unknown",
    "why_warmaster_needed": "почему нужен Абаддон",
    "expected_outcome": "конкретный результат",
    "success_conditions": ["проверяемые критерии"],
    "constraints": ["жёсткие ограничения"],
    "known_missing_inputs": ["что можно выяснить по ходу"]
  },
  "artifact_delivery": {
    "artifact_id": "точный artifact_id из available_artifacts; иначе пусто"
  },
  "pending_decision_task_id": "точный task_id из pending_decisions; иначе пусто",
  "continue_parent_task_id": "точный parent_task_id из continuable_tasks; иначе пусто",
  "confidence": 0.0,
  "rationale_summary": "короткое объяснение выбора без chain-of-thought"
}

Правила:
- Обычный разговор, обсуждение архитектуры, мнение или вопрос = answer_in_chat и содержательный reply.
- Реальная просьба выполнить многошаговую работу = request_warmaster_mission. Ты задаёшь намерение и критерии;
  Абаддон выбирает бригадира, а варбанда — детальный план.
- Явная просьба или команда продолжить/доделать/повторить недавно остановившуюся работу =
  continue_warmaster_mission. Естественный вопрос-просьба вроде «Ты мне уже сделаешь эту сборку?» тоже может
  быть исполнительным запросом: оценивай смысл текущей реплики, а не только её грамматическую форму.
  Выбери только точный parent_task_id из continuable_tasks. Сервер создаст новую связанную миссию: терминальный
  старый run не переоткрывается. Для расплывчатого «доделывай» восстанови предмет из recent_history.
- Наличие task page или доступной continuable_tasks — только контекст, не разрешение. Не выбирай
  continue_warmaster_mission, если именно ТЕКУЩАЯ реплика не просит возобновить/довести старую работу.
- Напоминание/расписание/watch = create_administratum_task.
- Явная просьба прислать уже зарегистрированный файл = deliver_artifact. Выбери только точный
  artifact_id из available_artifacts; путь, имя файла или придуманный идентификатор не дают доступа.
  Если аварийно сжатая ситуация содержит single_trusted_artifact=true, можно оставить artifact_id пустым:
  Core подставит его только когда полный доверенный manifest подтверждает ровно один доступный артефакт.
- Если ситуация содержит pending_decisions и текущий текст является прямым ответом на один из этих вопросов,
  выбери answer_pending_decision. Для постороннего вопроса это действие не выбирай. task_id и точный
  текст ответа подставит Core из доверенного manifest и текущего хода; не сочиняй их.
  single_trusted_pending_decision=true означает, что точный единственный task_id скрыт только из-за бюджета;
  оставь id пустым, Core свяжет ответ с ним из полного доверенного manifest.
- Для deliver_artifact не сочиняй подпись или подтверждение: фактический текст сформирует Archive
  только после успешной публикации выбранного artifact_id.
- Если без неизвестного нельзя ответственно начать, ask_clarification с одним конкретным вопросом.
- Нельзя текстом обещать, что поиск, код, файл, сообщение, таймер или миссия уже выполнены.
- Для внешнего действия reply пуст: сервер сначала исполнит эффект и только затем подтвердит факт.
- Ты — агентная система с опубликованными органами и варбандами. Если нужная capability доступна, нельзя
  отказываться как «просто текстовая модель»; если её нет — назови конкретно отсутствующий орган или информацию.
- В answer_in_chat нельзя писать «сам дожму», «продолжу работу» или «жди результат»: без внешнего эффекта
  это ложное обещание, даже если нужная задача видна в памяти.
- На вопрос «помнишь…» опирайся только на различимые детали из recent_history/recalled_memory/task page.
  Если подходят несколько разных эпизодов, не изображай уверенное узнавание — назови кандидатов или задай
  один короткий уточняющий вопрос.
- Не подстраивайся механически. Если человек ошибается, возражай прямо и с конкретными основаниями.
"""


class DecisionTruthError(ValueError):
    """A speech-only decision claimed execution that has no durable effect."""


_SPEECH_ONLY_EXECUTION_PATTERNS = (
    re.compile(
        r"\b(?:я\s+)?(?:сам\s+)?(?:доделаю|доделываю|дожму|дожимаю|продолжу|продолжаю|"
        r"запущу|запускаю|исправлю|исправляю|перезапущу|перезапускаю)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bжди(?:те)?\s+(?:результат|готов(?:ый|ое|ую)|итог)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:сам\s+)?разберусь\b.{0,100}\b(?:дожать|доделать|исправить|продолжить)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:займусь|возьмусь|берусь|сделаю|подготовлю|соберу|проверю|отправлю|пришлю)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bсообщу\s+(?:тебе\s+)?(?:результат|итог)\b", re.IGNORECASE),
    re.compile(
        r"\bбуду\b.{0,80}\b(?:делать|доделывать|продолжать|собирать|проверять|"
        r"готовить|отправлять|исправлять)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

_CONTINUATION_IMPERATIVE_PATTERN = re.compile(
    r"\b(?:доделай|доделывай|продолжи|продолжай|дожми|дожимай|"
    r"доведи|законч(?:и|ите)|заверш(?:и|ите)|возобнов(?:и|ите)|"
    r"повтор(?:и|ите)|перезапуст(?:и|ите)|"
    r"попробуй\s+еще\s+раз|давай\s+еще\s+раз|делай\s+дальше)\b",
    re.IGNORECASE,
)
_CONTINUATION_NON_COMMAND_PATTERN = re.compile(
    r"\b(?:если|допустим|предположим|гипотетически|почему|зачем|"
    r"стоит\s+ли|можно\s+ли|надо\s+ли|что\s+если|слово|фраза|"
    r"цитат\w*|пример\w*|условно|обсудим|обсуждаем|обсуждать|"
    r"означает|значит)\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_REACTIVATION_PATTERN = re.compile(
    r"\b(?:вернись|вернитесь|возьми|возьмите|добейся|добейтесь|"
    r"доведи|доведите|заверши|завершите|возобнови|возобновите)\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_POLITE_PATTERN = re.compile(
    r"\b(?:можешь|можете|сможешь|сможете)\s+"
    r"(?:вернуться|возобновить|продолжить|доделать|довести|завершить)\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_COLLABORATIVE_PATTERN = re.compile(
    r"\bдавай\s+(?:продолжим|доделаем|возобновим|вернемся|вернёмся)\b",
    re.IGNORECASE,
)
_CONTINUATION_ANAPHORIC_COLLABORATIVE_PATTERN = re.compile(
    r"^\s*(?:(?:ну|так)\s+)*давай\s+"
    r"(?:(?:уже|теперь|сейчас|сами|сам)\s+)*"
    r"(?:ее|его|их|это|эту|ту)\s+"
    r"(?:(?:уже|теперь|сейчас|сами|сам)\s+)*"
    r"(?:продолжим|доделаем|закончим|доведем|завершим|возобновим)\b",
    re.IGNORECASE,
)
_CONTINUATION_ANAPHORIC_OBJECT_WORDS = frozenset(
    {"ее", "его", "их", "это", "эту", "ту"}
)
_CONTINUATION_SEMANTIC_TARGET_PATTERN = re.compile(
    r"\b(?:работ\w*|задач\w*|мисси\w*|проект\w*|результат\w*|"
    r"итог\w*|сборк\w*|приложен\w*|apk|апк)\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_RESULT_REQUEST_PATTERN = re.compile(
    r"\b(?:нужен|нужна|нужно|требуется)\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_RESULT_PATTERN = re.compile(
    r"\b(?:результат\w*|итог(?:а|у|ом|е|и|ов|ам|ами|ах)?|"
    r"готов\w*\s+(?:артефакт\w*|файл\w*|сборк\w*|apk|апк))\b",
    re.IGNORECASE,
)
_CONTINUATION_SEMANTIC_RESUME_PATTERN = re.compile(
    r"\b(?:снова|опять|все[-\s]?таки|той|ту|этой|эту|"
    r"останов\w*|прерван\w*|незаверш\w*|недодел\w*|"
    r"верн\w*|по\s+(?:ней|нему|той|этому))\b",
    re.IGNORECASE,
)
_CONTINUATION_NEGATED_ACTION_PATTERN = re.compile(
    r"\b(?:не\s+(?:нужен|нужна|нужно|требуется|вернись|вернитесь|возьми|возьмите|"
    r"добейся|добейтесь|доведи|доведите|заверши|завершите|возобнови|возобновите|"
    r"доделай|доделывай|продолжи|продолжай)|никогда\b.{0,40}\bне|"
    r"ни\s+за\s+что|перестань|хватит)\b",
    re.IGNORECASE,
)
_CONTINUATION_INFORMATION_OBJECT_PATTERN = re.compile(
    r"^\s*(?:(?:мне|еще|снова|опять|эту|этот|тот|ту|свой|свою)\s+)*"
    r"(?:что\s+ты\s+(?:сказал|написал)|мысл\w*|рассказ\w*|истори\w*|"
    r"провер\w*\s+связ\w*|объясн\w*|разбор\w*|анализ\w*|обсуд\w*|"
    r"обсужд\w*|разговор\w*|диалог\w*|"
    r"подума\w*|размышл\w*|"
    r"статус\w*|оценк\w*|услови\w*|требован\w*|формулировк\w*|"
    r"назван\w*|формулир\w*|контекст\w*|содержан\w*|детал\w*|план\w*|ответ\w*|"
    r"описан\w*|вопрос\w*|фраз\w*)\b",
    re.IGNORECASE,
)
_CONTINUATION_PARAPHRASE_PATTERN = re.compile(
    r"\b(?:(?:своими|другими)\s+словами|перефразир\w*|перескаж\w*|"
    r"кратк\w*|подробн\w*|понятн\w*)\b",
    re.IGNORECASE,
)
_CONTINUATION_REPORTED_SPEECH_PATTERN = re.compile(
    r"\b(?:я|ты|он|она|мы|вы|они)?\s*"
    r"(?:сказал\w*|написал\w*|(?:по)?просил\w*|спросил\w*|приказал\w*|велел\w*|"
    r"говорит|говорил\w*|говорят|произнес\w*|произнёс\w*|цитиру\w*)\b",
    re.IGNORECASE,
)
_CONTINUATION_EXECUTION_VETO_PATTERN = re.compile(
    r"\b(?:не\s+(?:запускай|запускать|выполняй|выполнять|делай|делать|"
    r"продолжай|продолжать|продолжить|доделывай|доделывать|доделать|"
    r"возобновляй|возобновлять|возобновить|закончить|довести|завершить)|"
    r"без\s+(?:запуска|выполнения|продолжения|возобновления))\b",
    re.IGNORECASE,
)
_CONTINUATION_QUOTE_PATTERN = re.compile(r"[\"«»„“”]")
_CONTINUATION_QUOTED_SPAN_PATTERN = re.compile(
    r"(?:\"[^\"]*\"|«[^»]*»|„[^“]*“|“[^”]*”)",
    re.DOTALL,
)
_CONTINUATION_HYPOTHETICAL_PREFIX_PATTERN = re.compile(
    r"^\s*(?:а\s+)?(?:если|допустим|предположим|гипотетически|условно|что\s+если)\b",
    re.IGNORECASE,
)
_CONTINUATION_INFORMATION_QUESTION_PATTERN = re.compile(
    r"^\s*(?:а\s+)?(?:почему|зачем|стоит\s+ли|можно\s+ли|надо\s+ли|"
    r"что\s+(?:будет|произойдет|произойдёт|случится)|как\s+думаешь|что\s+думаешь)\b",
    re.IGNORECASE,
)
_CONTINUATION_ALTERNATE_INTENT_PATTERN = re.compile(
    r"\b(?:подтверд\w*.{0,40}связ\w*|провер\w*.{0,24}связ\w*|"
    r"статус\w*|оценк\w*|объясн\w*|разбор\w*|анализ\w*|обсуд\w*|обсужд\w*|"
    r"разговор\w*|рассказ\w*|истори\w*|мысл\w*|перефразир\w*|перескаж\w*|"
    r"своими\s+словами|что\s+ты\s+(?:сказал|написал)|услови\w*|требован\w*|"
    r"формулировк\w*|подума\w*|цитат\w*|закрой|закрыть)\b",
    re.IGNORECASE | re.DOTALL,
)
_CONTINUATION_RECALL_PATTERN = re.compile(r"\bпомнишь\b", re.IGNORECASE)
_CONTINUATION_NATURAL_REQUEST_PATTERN = re.compile(
    r"\b(?:"
    r"(?:можешь|можете|сможешь|сможете|будешь|будете)"
    r"(?:[\s,]+[a-zа-я0-9-]+){0,5}[\s,]+"
    r"(?:продолжить|доделать|закончить|довести|завершить|возобновить)|"
    r"(?:продолжить|доделать|закончить|довести|завершить|возобновить)"
    r"(?:[\s,]+[a-zа-я0-9-]+){0,3}[\s,]+"
    r"(?:можешь|можете|сможешь|сможете)|"
    r"(?:продолжишь|продолжите|доделаешь|доделаете|закончишь|закончите|"
    r"доведешь|доведете|завершишь|завершите|возобновишь|возобновите)|"
    r"(?:надо|нужно|пора)(?:[\s,]+[a-zа-я0-9-]+){0,5}[\s,]+"
    r"(?:продолжить|доделать|закончить|довести|завершить|возобновить)"
    r")\b",
    re.IGNORECASE,
)
_INDEPENDENT_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r"(?:[.!?;]\s+|\s[—–-]\s+|\n+)",
)
_CONDITIONAL_DIRECTIVE_PATTERN = re.compile(
    r"^\s*(?:ну\s+)?если\s+(?:можешь|можете|сможешь|сможете|надо|нужно|"
    r"готов\w*|хочешь|хотите)(?:\s*,\s*|\s+)(?P<tail>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_REPORTED_CLAUSE_COMPLEMENT_PATTERN = re.compile(
    r"\b(?:что|чтобы|будто|словно|как|когда|где|куда|откуда|зачем|почему|"
    r"кто|какой|какая|какие|о\s+том)\b",
    re.IGNORECASE,
)
_DIRECTIVE_COORDINATOR_PATTERN = re.compile(
    r"(?:^|[,;])\s*(?:а|и|но)\s+"
    r"(?:(?:потом|затем|тогда|теперь|сейчас|после\s+этого)\s+)?$",
    re.IGNORECASE,
)
_EXPLICIT_EFFECT_REQUEST_PATTERNS = {
    "request_warmaster_mission": re.compile(
        r"\b(?:создай|создайте|создашь|сделай|сделайте|сделаешь|напиши|напишите|"
        r"напишешь|собери|соберите|соберешь|соберёшь|разработай|разработайте|"
        r"построй|постройте|исправь|исправьте|почини|почините|переделай|переделайте|"
        r"начни|начните|начинай|запусти|запустите|"
        r"(?:можешь|можете|сможешь|сможете|мог\s+бы|могли\s+бы)"
        r"(?:(?:\s*,\s*|\s+)(?:мне|нам|уже|пожалуйста)){0,4}"
        r"(?:\s*,\s*|\s+)"
        r"(?:создать|сделать|написать|собрать|разработать|построить|исправить|"
        r"починить|переделать|начать|запустить))\b",
        re.IGNORECASE,
    ),
    "create_administratum_task": re.compile(
        r"\b(?:напомни|напомните|запланируй|запланируйте|поставь\s+напоминание|"
        r"запиши\s+(?:задачу|напоминание)|"
        r"(?:можешь|можете|сможешь|сможете|мог\s+бы|могли\s+бы)"
        r"(?:(?:\s*,\s*|\s+)(?:мне|нам|пожалуйста)){0,3}"
        r"(?:\s*,\s*|\s+)"
        r"(?:напомнить|запланировать|поставить\s+напоминание|"
        r"записать\s+(?:задачу|напоминание)))\b",
        re.IGNORECASE,
    ),
    "deliver_artifact": re.compile(
        r"\b(?:(?:пришли|пришлите|отправь|отправьте|скинь|скиньте|передай|передайте)|"
        r"(?:можешь|можете|сможешь|сможете|мог\s+бы|могли\s+бы)"
        r"(?:(?:\s*,\s*|\s+)(?:мне|нам|пожалуйста)){0,3}"
        r"(?:\s*,\s*|\s+)"
        r"(?:прислать|отправить|скинуть|передать))\b"
        r".{0,120}\b(?:файл\w*|apk|апк|артефакт\w*|приложен\w*)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "deliver_pending_reports": re.compile(
        r"\b(?:(?:покажи|покажите|пришли|пришлите|отправь|отправьте|дай|дайте)|"
        r"(?:можешь|можете|сможешь|сможете|мог\s+бы|могли\s+бы)"
        r"(?:(?:\s*,\s*|\s+)(?:мне|нам|пожалуйста)){0,3}"
        r"(?:\s*,\s*|\s+)"
        r"(?:показать|прислать|отправить|дать))\b"
        r".{0,100}\b(?:отчет\w*|отчёт\w*|результат\w*|новост\w*)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "answer_pending_decision": re.compile(
        r"\b(?:мой\s+ответ|я\s+выбираю|выбираю|выбрал|выбрала|выбирай|"
        r"выберите|выбери|вариант\s+\w+|решение\s*[:—-])\b",
        re.IGNORECASE,
    ),
}
_EFFECT_META_RECLASSIFICATION_PATTERN = re.compile(
    r"(?:"
    r"\b(?:это|this\s+is|that\s+is|it\s+is|this\s+was|that\s+was)\s+|"
    r"(?:[—–-]|,|;)\s*"
    r")"
    r"(?:(?:всего\s+лишь|лишь|только|просто|just|only|merely)\s+)?"
    r"(?:пример\w*|фраз\w*|цитат\w*|формулировк\w*|упоминани\w*|"
    r"example\w*|phrase\w*|quote\w*|wording\w*|mention\w*)\b",
    re.IGNORECASE,
)
_EFFECT_REQUEST_DISCLAIMER_PATTERN = re.compile(
    r"\b(?:"
    r"не\s+(?:просьб\w*|команд\w*|поручени\w*|указани\w*|заказ\w*)|"
    r"(?:not|isn't|isnt|is\s+not|wasn't|wasnt|was\s+not)\s+"
    r"(?:an?\s+)?(?:request\w*|instruction\w*|order\w*|command\w*)"
    r")\b",
    re.IGNORECASE,
)
_EFFECT_PRETEND_OBJECT_PATTERN = re.compile(
    r"^\s*[,;:—–-]*\s*"
    r"(?:(?:пожалуйста|просто|лишь|только|именно|все|всё|быстро|"
    r"реально|буквально|специально|нарочно)\s*,?\s*)*"
    r"(?:вид\b|видимость\b|"
    r"(?:так\s*,?\s*)?(?:как\s*,?\s*)?будто\b)",
    re.IGNORECASE,
)
_EFFECT_SPEECH_ANSWER_OBJECT_PATTERN = re.compile(
    r"^\s*[,;:—–-]*\s*(?:(?:мне|нам|пожалуйста)\s*,?\s*)*"
    r"(?:(?:очень|максимально|предельно|кратко|коротко|подробно|"
    r"развернуто|развёрнуто|"
    r"кратк\w*|коротк\w*|подробн\w*|развернут\w*|развёрнут\w*)\s*,?\s*)*"
    r"(?:"
    r"(?:ответ\w*|объяснен\w*|объяснён\w*)\b\s*,?\s*"
    r"(?:почему\b|зачем\b|как\b|что\b|кто\b|како(?:й|е|го|му)\b)|"
    r"почему\b|зачем\b|как\b|что\b|кто\b|како(?:й|е|го|му)\b|"
    r"ответ\w*\b|объяснен\w*\b|объяснён\w*\b"
    r")",
    re.IGNORECASE,
)
_CONTINUATION_SAFE_LEADING_CONTEXT_PATTERNS = (
    re.compile(
        r"^почему\b[^?]{0,120}\b(?:встал\w*|останов\w*|завис\w*|"
        r"не\s+готов\w*)[^?]*\?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^ты\s+закончил\?\s*(?:если\s+нет\s*[—–-]\s*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^так\s+если\s+мой\s+выбор\s+не\s+нужен[.!]\s*",
        re.IGNORECASE,
    ),
    re.compile(r"^обсудим\s+позже,\s*а\s+сейчас\s+", re.IGNORECASE),
    re.compile(r"^не\s+нужен\s+статус,\s*", re.IGNORECASE),
)
_CONTINUATION_CONSTRAINT_SUFFIX_PATTERN = re.compile(
    r"^\s*,?\s*(?:но|только|при\s+этом|с\s+условием|без)\b",
    re.IGNORECASE,
)
_CONTINUATION_FILLER_WORDS = frozenset(
    {
        "ну",
        "так",
        "а",
        "и",
        "ладно",
        "давай",
        "ты",
        "тогда",
        "теперь",
        "сейчас",
        "просто",
        "уже",
        "еще",
        "снова",
        "опять",
        "все",
        "всё",
        "сам",
        "сама",
        "быстро",
        "молча",
        "дальше",
        "пожалуйста",
        "плиз",
        "мне",
        "нам",
        "брат",
        "братец",
        "пиздуй",
        "бля",
        "блять",
        "блядь",
        "нахуй",
        "же",
        "ка",
        "уж",
    }
)
_PARENT_GOAL_LIMIT = 8_000
_PARENT_MESSAGE_LIMIT = 24_000
_PARENT_FIELD_LIMIT = 6_000
_PARENT_LIST_ITEMS = 12
_PARENT_LIST_ITEM_LIMIT = 1_000
_FAILURE_FIELD_LIMIT = 6_000
def _reject_speech_only_execution_claim(action: str, reply: str) -> None:
    if action not in {"answer_in_chat", "ask_clarification"}:
        return
    if any(pattern.search(reply) for pattern in _SPEECH_ONLY_EXECUTION_PATTERNS):
        raise DecisionTruthError(
            "execution_claim_without_effect: answer_in_chat cannot promise continuation or a result"
        )


def _trusted_continuation_parent(manifest: dict[str, Any]) -> str:
    trusted_ids = continuable_task_ids(manifest)
    root_id = str(manifest.get("continuation_parent_task_id") or "").strip()[:240]
    if root_id in trusted_ids:
        return root_id
    if len(trusted_ids) == 1:
        return trusted_ids[0]
    return ""


def _only_continuation_fillers(text: str) -> bool:
    words = re.findall(r"[a-zа-я0-9]+", str(text or "").lower().replace("ё", "е"))
    return all(word in _CONTINUATION_FILLER_WORDS for word in words)


def _only_anaphoric_task_object(text: str) -> bool:
    """Accept a bare task pronoun, not a noun phrase such as "его ответ"."""

    words = re.findall(r"[a-zа-я0-9]+", str(text or "").lower().replace("ё", "е"))
    anaphors = [word for word in words if word in _CONTINUATION_ANAPHORIC_OBJECT_WORDS]
    return bool(
        len(anaphors) == 1
        and all(
            word in _CONTINUATION_FILLER_WORDS
            or word in _CONTINUATION_ANAPHORIC_OBJECT_WORDS
            for word in words
        )
    )


def _task_execution_reference(text: str) -> bool:
    candidate = str(text or "").strip(" \t,.;:—–")
    while candidate:
        leading_word = re.match(r"^([a-zа-я0-9]+)\b[\s,]*", candidate, re.IGNORECASE)
        if not leading_word or leading_word.group(1) not in _CONTINUATION_FILLER_WORDS:
            break
        candidate = candidate[leading_word.end():].lstrip(" \t,.;:—–")
    candidate = re.sub(
        r"^(?:(?:к|ко|о|об|про|по|над)\b[\s,]*)+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    return bool(
        candidate
        and _CONTINUATION_SEMANTIC_TARGET_PATTERN.search(candidate)
        and not _CONTINUATION_INFORMATION_OBJECT_PATTERN.search(candidate)
    )


def _positive_continuation_scope(text: str) -> str:
    """Return only a whole-turn scope that matches a small positive grammar."""
    normalized = str(text or "").strip().lower().replace("ё", "е")
    if (
        not normalized
        or "\n" in normalized
        or "\r" in normalized
        or _CONTINUATION_QUOTE_PATTERN.search(normalized)
    ):
        return ""
    for pattern in _CONTINUATION_SAFE_LEADING_CONTEXT_PATTERNS:
        match = pattern.match(normalized)
        if match:
            normalized = normalized[match.end():].lstrip()
            break
    # The safe leading context was removed above. Any remaining sentence,
    # explanation or dash-tail means this is not a bare execution mandate.
    if re.search(r"[.!?;:]\s+\S", normalized):
        return ""
    if re.search(r"\s[—–-]\s*\S", normalized):
        return ""
    return normalized.strip(" \t,.;:—–")


def _bounded_command_object(text: str) -> str:
    """Keep an object only when any comma tail is a constraint or filler."""
    candidate = str(text or "").strip()
    if not candidate:
        return ""
    head, separator, tail = candidate.partition(",")
    head = head.strip()
    if not separator:
        return head
    decorated_tail = "," + tail
    if _only_continuation_fillers(tail) or _CONTINUATION_CONSTRAINT_SUFFIX_PATTERN.search(
        decorated_tail
    ):
        return head
    return ""


def _looks_like_continuation_directive(text: str) -> bool:
    scope = _positive_continuation_scope(text)
    if (
        not scope
        or _CONTINUATION_NON_COMMAND_PATTERN.search(scope)
        or _CONTINUATION_REPORTED_SPEECH_PATTERN.search(scope)
        or _CONTINUATION_EXECUTION_VETO_PATTERN.search(scope)
    ):
        return False
    collaborative = _CONTINUATION_ANAPHORIC_COLLABORATIVE_PATTERN.search(scope)
    if collaborative:
        collaborative_suffix = scope[collaborative.end():]
        if (
            _only_continuation_fillers(collaborative_suffix)
            or _CONTINUATION_CONSTRAINT_SUFFIX_PATTERN.search(collaborative_suffix)
        ):
            return True
    for command in _CONTINUATION_IMPERATIVE_PATTERN.finditer(scope):
        prefix = scope[: command.start()].rstrip()
        suffix = scope[command.end():]
        if not _only_continuation_fillers(prefix):
            continue
        if (
            command.group(0).startswith("повтор")
            and _CONTINUATION_PARAPHRASE_PATTERN.search(suffix)
        ):
            continue
        if re.search(
            r"\b(?:не|никогда(?:\s+\w+){0,3}\s+не|ни\s+за\s+что|перестань|хватит)\s*$",
            prefix[-64:],
            re.IGNORECASE,
        ) or re.match(r"^\s+не\b", suffix, re.IGNORECASE):
            continue
        if _CONTINUATION_CONSTRAINT_SUFFIX_PATTERN.search(suffix):
            return True
        command_object = _bounded_command_object(suffix)
        if command_object and (
            _task_execution_reference(command_object)
            or _only_anaphoric_task_object(command_object)
        ):
            return True
        if _only_continuation_fillers(suffix):
            return True
    return False


def _current_turn_has_explicit_continuation_evidence(text: str) -> bool:
    """Recognize only commands used by the deterministic truth-guard fallback.

    A positive match is deliberately not required for a model-selected
    continuation.  It only lets the server recover a real effect when the model
    emitted an impossible speech-only execution promise.
    """
    scope = _positive_continuation_scope(text)
    if not scope:
        return False
    if _looks_like_continuation_directive(scope):
        return True
    if (
        _CONTINUATION_NON_COMMAND_PATTERN.search(scope)
        or _CONTINUATION_REPORTED_SPEECH_PATTERN.search(scope)
        or _CONTINUATION_EXECUTION_VETO_PATTERN.search(scope)
        or _CONTINUATION_NEGATED_ACTION_PATTERN.search(scope)
    ):
        return False

    def object_after(action: re.Match[str] | None) -> str:
        if not action or not _only_continuation_fillers(scope[:action.start()]):
            return ""
        return _bounded_command_object(scope[action.end():])

    reactivation_action = _CONTINUATION_SEMANTIC_REACTIVATION_PATTERN.search(scope)
    reactivation_object = object_after(reactivation_action)
    if (
        reactivation_object
        and (
            _only_anaphoric_task_object(reactivation_object)
            or (
                _task_execution_reference(reactivation_object)
                and _CONTINUATION_SEMANTIC_RESUME_PATTERN.search(scope)
            )
        )
    ):
        return True

    polite_object = object_after(_CONTINUATION_SEMANTIC_POLITE_PATTERN.search(scope))
    if polite_object and _task_execution_reference(polite_object):
        return True

    collaborative_object = object_after(
        _CONTINUATION_SEMANTIC_COLLABORATIVE_PATTERN.search(scope)
    )
    if collaborative_object and _task_execution_reference(collaborative_object):
        return True

    result_action = _CONTINUATION_SEMANTIC_RESULT_REQUEST_PATTERN.search(scope)
    result_object = object_after(result_action)
    if (
        "?" not in scope
        and result_object
        and _CONTINUATION_SEMANTIC_RESULT_PATTERN.search(result_object)
        and _CONTINUATION_SEMANTIC_RESUME_PATTERN.search(result_object)
    ):
        return True
    return False


def _continuation_text_is_corrupted(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate or "\ufffd" in candidate or "\x00" in candidate:
        return True
    visible = "".join(char for char in candidate if not char.isspace())
    letters_or_digits = sum(char.isalnum() for char in visible)
    question_marks = visible.count("?")
    return bool(
        len(visible) >= 6
        and question_marks >= 3
        and letters_or_digits < max(3, len(visible) // 5)
    )


def _final_independent_clause(text: str, *, strip_quotes: bool = False) -> str:
    """Return the final clause only when punctuation makes it independent."""

    normalized = str(text or "").strip().lower().replace("ё", "е")
    if strip_quotes:
        normalized = _CONTINUATION_QUOTED_SPAN_PATTERN.sub(" ", normalized)
        if _CONTINUATION_QUOTE_PATTERN.search(normalized):
            return ""
    conditional = _CONDITIONAL_DIRECTIVE_PATTERN.match(normalized)
    if conditional:
        return str(conditional.group("tail") or "").strip()
    boundaries = list(_INDEPENDENT_CLAUSE_BOUNDARY_PATTERN.finditer(normalized))
    if not boundaries:
        return normalized
    return normalized[boundaries[-1].end():].strip()


def _reported_clause_has_own_content(text: str) -> bool:
    """Distinguish a completed report from a command used as its quotation."""

    reports = list(_CONTINUATION_REPORTED_SPEECH_PATTERN.finditer(text))
    if not reports:
        return True
    complement = text[reports[-1].end():]
    return bool(
        _REPORTED_CLAUSE_COMPLEMENT_PATTERN.search(complement)
        or _CONTINUATION_QUOTE_PATTERN.search(complement)
    )


def _comma_tail_is_independent_directive(head: str) -> bool:
    """Use clause structure, not magic phrases, to identify the active speaker."""

    normalized = str(head or "").strip()
    if not normalized:
        return True
    if _CONTINUATION_HYPOTHETICAL_PREFIX_PATTERN.search(normalized):
        return False
    return _reported_clause_has_own_content(normalized)


def _final_standalone_continuation_directive(text: str) -> str:
    final_clause = _final_independent_clause(text)
    if _looks_like_continuation_directive(final_clause):
        return final_clause

    # People routinely omit the second sentence boundary in chat.  A trailing
    # imperative after a comma is still the operative act ("помнишь задачу,
    # продолжай"), unless the preceding clause makes it quoted, hypothetical,
    # or the content of unfinished reported speech ("он сказал, продолжай").
    head, separator, tail = final_clause.rpartition(",")
    if (
        separator
        and _looks_like_continuation_directive(tail)
        and _comma_tail_is_independent_directive(head)
    ):
        return tail.strip()
    return ""


def _effect_match_is_operative(
    scope: str,
    match: re.Match[str],
    *,
    action: str,
) -> bool:
    """Return true only when an effect verb is the user's operative request.

    The effect gate runs only after semantic re-arbitration.  Its job is not to
    understand every request again; it merely separates an actual directive
    from a verb mentioned inside an explanation, quote, report or hypothesis.
    """

    prefix = scope[:match.start()]
    suffix = scope[match.end():]
    # A verb-shaped quotation without quote marks is common in chat.  When the
    # same clause explicitly reclassifies the wording as an example/mention or
    # says that it is not a request, it is evidence *against* executing it.
    # A later sentence is unaffected because _final_independent_clause already
    # selects that later speech act before this helper runs.
    if (
        _EFFECT_META_RECLASSIFICATION_PATTERN.search(suffix)
        or _EFFECT_REQUEST_DISCLAIMER_PATTERN.search(suffix)
    ):
        return False
    if action == "request_warmaster_mission":
        matched_verb = match.group(0).lower().replace("ё", "е")
        if _EFFECT_PRETEND_OBJECT_PATTERN.search(suffix):
            return False
        if (
            ("напиш" in matched_verb or "напис" in matched_verb)
            and _EFFECT_SPEECH_ANSWER_OBJECT_PATTERN.search(suffix)
        ):
            return False
        # A verb alone is not enough authority to create a mission.  The
        # current clause must also carry its object/reference ("новую Galaga",
        # "это", "с нуля"), while the model remains responsible for semantics.
        concrete_object = re.sub(
            r"^\s*[,;:—–-]*\s*(?:(?:мне|нам|уже|пожалуйста)\s*,?\s*)*",
            "",
            suffix,
            flags=re.IGNORECASE,
        )
        if not re.search(r"[a-zа-я0-9]", concrete_object, re.IGNORECASE):
            return False
    if re.search(
        r"\b(?:не|никогда\s+не|не\s+надо|не\s+нужно)\s*$",
        prefix[-64:],
        re.IGNORECASE,
    ):
        return False
    if _only_continuation_fillers(prefix):
        return True

    coordinator = _DIRECTIVE_COORDINATOR_PATTERN.search(prefix)
    if coordinator:
        # "Он это обсуждал, а теперь создай ...": the coordinator explicitly
        # changes the speech act, so an earlier quote/report cannot own it.
        return True

    head, separator, tail_lead = prefix.rpartition(",")
    if separator and _only_continuation_fillers(tail_lead):
        return _comma_tail_is_independent_directive(head)
    return False


def _effect_request_is_explicit(text: str, action: str) -> bool:
    """Require local current-turn evidence before veto repair changes effects."""

    pattern = _EXPLICIT_EFFECT_REQUEST_PATTERNS.get(str(action or ""))
    if pattern is None or _continuation_text_is_corrupted(text):
        return False
    scope = _final_independent_clause(text, strip_quotes=True)
    if (
        not scope
        or _CONTINUATION_HYPOTHETICAL_PREFIX_PATTERN.search(scope)
    ):
        return False
    for match in pattern.finditer(scope):
        if _effect_match_is_operative(scope, match, action=action):
            return True
    return False


def _natural_continuation_request(text: str) -> bool:
    """Recognize request-shaped continuations without turning them into a whitelist."""

    return bool(_CONTINUATION_NATURAL_REQUEST_PATTERN.search(str(text or "")))


def _recall_antecedent_is_task_like(text: str) -> bool:
    """Reject speech/story antecedents while allowing task nouns and names."""

    normalized = str(text or "").strip().lower().replace("ё", "е")
    recall = _CONTINUATION_RECALL_PATTERN.search(normalized)
    if not recall:
        return False
    antecedent = normalized[recall.end():].lstrip(" \t,:—–-")
    boundary = re.search(r"[.!?;]\s+", antecedent)
    if boundary:
        antecedent = antecedent[:boundary.start()].strip()
    if not antecedent or _CONTINUATION_INFORMATION_OBJECT_PATTERN.search(antecedent):
        return False
    if _task_execution_reference(antecedent):
        return True
    generic = {
        "это", "эту", "тот", "ту", "его", "ее", "их", "что", "ты", "вы",
        "я", "мы", "он", "она", "про", "о", "об",
    }
    substantive = [
        word
        for word in re.findall(r"[a-zа-я0-9-]+", antecedent)
        if word not in generic and len(word) > 1
    ]
    return bool(substantive)


def _recall_continuation_is_bound_to_task(
    text: str,
    *,
    request_scope: str,
) -> bool:
    """Keep recall continuations attached to work, not remembered speech.

    A task-like recalled object is enough for natural ellipsis ("помнишь
    Galaga — продолжай").  Otherwise the operative request itself must name
    an execution target ("помнишь рассказ — продолжай задачу").
    """

    if not _CONTINUATION_RECALL_PATTERN.search(str(text or "")):
        return True
    return bool(
        _recall_antecedent_is_task_like(text)
        or _task_execution_reference(request_scope)
    )


def _continuation_veto_reason(text: str) -> str:
    """Return a narrow, evidence-backed reason to reject model continuation.

    The model owns semantic intent selection.  Core intervenes only when the
    current turn itself proves that a continuation selection is unsafe: the
    text is damaged, quotes/reports somebody else's words, frames a
    hypothetical or informational request, explicitly negates execution, or
    clearly asks for a different conversational operation.
    """

    raw = str(text or "")
    if _continuation_text_is_corrupted(raw):
        return "corrupted_current_turn"
    normalized = raw.strip().lower().replace("ё", "е")

    # A final standalone instruction is the operative act even when earlier
    # clauses were a question, condition, quotation or reported speech.
    standalone_directive = _final_standalone_continuation_directive(normalized)
    if standalone_directive:
        if not _recall_continuation_is_bound_to_task(
            normalized,
            request_scope=standalone_directive,
        ):
            return "different_current_intent"
        return ""

    if _CONTINUATION_QUOTE_PATTERN.search(normalized):
        outside_quotes = _CONTINUATION_QUOTED_SPAN_PATTERN.sub(" ", normalized)
        # An unmatched quote is itself ambiguous. A later, unquoted standalone
        # directive can still be evaluated normally when all pairs were clean.
        if (
            _CONTINUATION_QUOTE_PATTERN.search(outside_quotes)
            or (
                not _looks_like_continuation_directive(outside_quotes)
                and not _natural_continuation_request(outside_quotes)
            )
        ):
            return "quoted_continuation"

    report = _CONTINUATION_REPORTED_SPEECH_PATTERN.search(normalized)
    if report:
        return "reported_speech"

    hypothetical = _CONTINUATION_HYPOTHETICAL_PREFIX_PATTERN.search(normalized)
    if hypothetical:
        return "hypothetical_continuation"

    if _CONTINUATION_EXECUTION_VETO_PATTERN.search(normalized):
        return "explicit_execution_veto"

    # Explicit direct commands are allowed before considering broad words such
    # as "status" or "why". This preserves turns like "Почему встал? Доделывай"
    # and "Не нужен статус, нужен результат по той миссии".
    if _current_turn_has_explicit_continuation_evidence(normalized):
        return ""

    # "Помнишь...?" may introduce either a recall question or the object of a
    # natural request.  Let the model decide the latter instead of letting one
    # keyword erase clear request semantics.
    natural_request = _CONTINUATION_NATURAL_REQUEST_PATTERN.search(normalized)
    if natural_request:
        if not _recall_continuation_is_bound_to_task(
            normalized,
            request_scope=normalized[natural_request.start():],
        ):
            return "different_current_intent"
        return ""

    if _CONTINUATION_NEGATED_ACTION_PATTERN.search(normalized):
        return "negated_continuation"
    if (
        "?" in normalized
        and _CONTINUATION_INFORMATION_QUESTION_PATTERN.search(normalized)
    ):
        return "informational_question"
    if _CONTINUATION_ALTERNATE_INTENT_PATTERN.search(normalized):
        return "different_current_intent"
    if (
        "?" in normalized
        and _CONTINUATION_RECALL_PATTERN.search(normalized)
    ):
        return "recall_only_question"
    return ""


def _continuation_not_authorized_decision() -> dict[str, Any]:
    return normalize_decision(
        {
            "action": "ask_clarification",
            "reply": (
                "Я не запустил старую задачу: текущая реплика не даёт команды её продолжать. "
                "Если ты хотел возобновить именно её — скажи это прямо; иначе повтори текущий вопрос."
            ),
            "confidence": 1.0,
            "rationale_summary": "continuation_not_authorized_by_current_turn",
        }
    )


def _truth_guard_continuation_decision(envelope: TurnEnvelope) -> dict[str, Any] | None:
    parent_task_id = _trusted_continuation_parent(envelope.capability_manifest)
    if not parent_task_id or not _current_turn_has_explicit_continuation_evidence(
        envelope.text
    ):
        return None
    return normalize_decision(
        {
            "action": "continue_warmaster_mission",
            "continue_parent_task_id": parent_task_id,
            "confidence": 1.0,
            "rationale_summary": (
                "Server truth guard bound the explicit continuation command to the trusted recent task."
            ),
        }
    )


def _extract_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("model did not return a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model response is not an object")
    return value


def _list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:12]


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported action: {action or '<empty>'}")
    reply = str(raw.get("reply") or "").strip()
    task = str(raw.get("task") or "").strip()
    request = raw.get("warmaster_request") if isinstance(raw.get("warmaster_request"), dict) else {}
    artifact = raw.get("artifact_delivery") if isinstance(raw.get("artifact_delivery"), dict) else {}
    pending = raw.get("pending_decision") if isinstance(raw.get("pending_decision"), dict) else {}
    normalized_request = {
        "user_request": str(request.get("user_request") or task).strip(),
        "capability_area": str(request.get("capability_area") or "unknown").strip().lower(),
        "why_warmaster_needed": str(request.get("why_warmaster_needed") or "").strip(),
        "expected_outcome": str(request.get("expected_outcome") or task).strip(),
        "success_conditions": _list_of_text(request.get("success_conditions")),
        "constraints": _list_of_text(request.get("constraints")),
        "known_missing_inputs": _list_of_text(request.get("known_missing_inputs")),
    }
    if normalized_request["capability_area"] not in {"research", "code", "image", "mixed", "administration", "unknown"}:
        normalized_request["capability_area"] = "unknown"
    if action in {"answer_in_chat", "ask_clarification"} and not reply:
        raise ValueError(f"{action} requires a non-empty reply")
    _reject_speech_only_execution_claim(action, reply)
    if action == "request_warmaster_mission" and (
        not normalized_request["user_request"] or not normalized_request["expected_outcome"]
    ):
        raise ValueError("request_warmaster_mission requires user_request and expected_outcome")
    if action == "create_administratum_task" and not task:
        raise ValueError("create_administratum_task requires task")
    artifact_delivery = {
        "artifact_id": str(artifact.get("artifact_id") or "").strip()[:240],
    }
    pending_decision_task_id = str(
        raw.get("pending_decision_task_id") or pending.get("task_id") or ""
    ).strip()[:240]
    if action != "answer_pending_decision":
        pending_decision_task_id = ""
    continue_parent_task_id = str(raw.get("continue_parent_task_id") or "").strip()[:240]
    if action != "continue_warmaster_mission":
        continue_parent_task_id = ""
    if action not in {"answer_in_chat", "ask_clarification"}:
        # Speech about an external action is synthesized only from the adapter's
        # factual result. Discard even a persuasive model claim here.
        reply = ""
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "reply": reply,
        "task": task,
        "warmaster_request": normalized_request,
        "artifact_delivery": artifact_delivery,
        # This is intentionally empty here. resolve() binds both fields from
        # trusted transport context after the model has selected only an action.
        "pending_decision": {"task_id": "", "answer": ""},
        "pending_decision_task_id": pending_decision_task_id,
        "continue_parent_task_id": continue_parent_task_id,
        "confidence": confidence,
        "reason": str(raw.get("rationale_summary") or raw.get("reason") or "").strip()[:1_000],
    }


def warmaster_message(request: dict[str, Any]) -> str:
    parts = [
        "Запрос Шушуни к EyeOfTerror Abaddon.",
        "Шушуня задаёт намерение и критерии. Абаддон выбирает стратегический маршрут и бригадира; варбанда составляет детальный план и выполняет работу.",
        f"Область: {request.get('capability_area') or 'unknown'}",
        f"Исходный запрос пользователя: {request.get('user_request') or ''}",
        f"Ожидаемый результат: {request.get('expected_outcome') or ''}",
    ]
    if request.get("why_warmaster_needed"):
        parts.append(f"Почему нужен Абаддон: {request['why_warmaster_needed']}")
    if request.get("success_conditions"):
        parts.append("Критерии приёмки:\n" + "\n".join(f"- {item}" for item in request["success_conditions"]))
    if request.get("constraints"):
        parts.append("Ограничения:\n" + "\n".join(f"- {item}" for item in request["constraints"]))
    if request.get("known_missing_inputs"):
        parts.append("Что выяснить по ходу:\n" + "\n".join(f"- {item}" for item in request["known_missing_inputs"]))
    return "\n\n".join(parts)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[: max(0, int(limit))]


def _bounded_parent_request(value: Any) -> dict[str, Any]:
    request = value if isinstance(value, dict) else {}
    result = {
        "user_request": _bounded_text(request.get("user_request"), _PARENT_FIELD_LIMIT),
        "capability_area": _bounded_text(request.get("capability_area"), 80),
        "why_warmaster_needed": _bounded_text(
            request.get("why_warmaster_needed"), _PARENT_FIELD_LIMIT
        ),
        "expected_outcome": _bounded_text(
            request.get("expected_outcome"), _PARENT_FIELD_LIMIT
        ),
    }
    for key in ("success_conditions", "constraints", "known_missing_inputs"):
        values = request.get(key) if isinstance(request.get(key), list) else []
        result[key] = [
            _bounded_text(item, _PARENT_LIST_ITEM_LIMIT)
            for item in values[:_PARENT_LIST_ITEMS]
            if _bounded_text(item, _PARENT_LIST_ITEM_LIMIT)
        ]
    return result


def continuation_message(candidate: dict[str, Any]) -> str:
    parent_task_id = str(candidate.get("parent_task_id") or "").strip()
    goal = str(candidate.get("goal") or "").strip()
    parent_spec = candidate.get("parent_spec") if isinstance(candidate.get("parent_spec"), dict) else {}
    parent_message = str(parent_spec.get("message") or "").strip()
    parent_request = (
        parent_spec.get("warmaster_request")
        if isinstance(parent_spec.get("warmaster_request"), dict)
        else {}
    )
    failure_guidance = (
        candidate.get("failure_guidance")
        if isinstance(candidate.get("failure_guidance"), dict)
        else {}
    )
    failure_summary = str(
        failure_guidance.get("explanation") or candidate.get("failure_summary") or ""
    ).strip()
    required_action = str(failure_guidance.get("required_action") or "").strip()
    parts = [
        "Новая связанная миссия по явной команде пользователя продолжить остановившуюся работу.",
        f"Родительская миссия: {parent_task_id}",
        f"Исходная цель: {goal}",
        (
            "Терминальный родительский run неизменяем. Не пытайся запускать его повторно: "
            "создай новый план и новую исполнимую миссию, сохранив связь с родителем."
        ),
    ]
    if parent_message:
        parts.append("Полная исходная спецификация родительской миссии:\n" + parent_message)
    elif parent_request:
        parts.append("Восстановленная исходная спецификация:\n" + warmaster_message(parent_request))
    if failure_summary:
        parts.append(f"Последняя подтверждённая причина остановки: {failure_summary}")
    if required_action:
        parts.append(f"Что обязательно исправить в новой стратегии: {required_action}")
    return "\n\n".join(parts)


def _decision_messages(
    situation: dict[str, Any],
    repair: str = "",
) -> list[dict[str, str]]:
    """Build one full-identity arbitration request, including semantic repair."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(situation, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    if not repair:
        return messages
    if repair.startswith("current_turn_continuation_veto:"):
        repair_prompt = f"""Предыдущее решение выбрать continue_warmaster_mission противоречит явному
свидетельству в current_turn. Заново оцени всю исходную ситуацию и выбери подходящее действие из полного
контракта. Ты остаёшься Шушуней; твои реальные органы, варбанды и остальные честно опубликованные
capability_manifest функции никуда не исчезли. Не называй себя «только текстовой моделью» и не выдумывай
ограничений, которых нет в ситуации.

Именно continue_warmaster_mission для этого хода запрещён. Другое внешнее действие выбирай только если его
прямо просит current_turn; иначе содержательно ответь или задай один конкретный вопрос. Верни полный JSON
обычного контракта без markdown. Основание veto: {repair[:1200]}"""
    else:
        repair_prompt = (
            "Предыдущий JSON нарушил контракт. Исправь только формат/обязательные "
            f"поля и верни один JSON. Ошибка: {repair[:1200]}"
        )
    messages.append({"role": "system", "content": repair_prompt})
    return messages


class DecisionEngine:
    def __init__(self, settings: Settings, ledger: Ledger, situation: SituationAssembler, authority: Authority):
        self.settings = settings
        self.ledger = ledger
        self.situation = situation
        self.authority = authority

    def _continuation_candidate(
        self,
        manifest: dict[str, Any],
        parent_task_id: str,
    ) -> dict[str, Any]:
        """Bind identity/state from Vox, then enrich content from Core's ledger."""
        candidate = next(
            (
                dict(item)
                for item in continuable_task_catalog(manifest)
                if item.get("parent_task_id") == parent_task_id
            ),
            {},
        )
        if not candidate:
            return {}
        commitment = self.ledger.find_commitment_by_delegate_ref(parent_task_id)
        if not commitment or str(commitment.get("kind") or "") != "abaddon_mission":
            return candidate

        spec = commitment.get("spec") if isinstance(commitment.get("spec"), dict) else {}
        parent_spec = {
            "message": _bounded_text(spec.get("message"), _PARENT_MESSAGE_LIMIT),
            "warmaster_request": _bounded_parent_request(spec.get("warmaster_request")),
            "task_id": _bounded_text(spec.get("task_id"), 240),
            "goal_id": _bounded_text(spec.get("goal_id"), 240),
            "task_memory_id": _bounded_text(
                spec.get("task_memory_id") or spec.get("goal_id"), 240
            ),
            "root_task_id": _bounded_text(spec.get("root_task_id"), 240),
        }
        diagnostic = (
            commitment.get("diagnostic")
            if isinstance(commitment.get("diagnostic"), dict)
            else {}
        )
        failure_guidance = {
            "code": _bounded_text(diagnostic.get("code"), 160),
            "explanation": _bounded_text(
                diagnostic.get("explanation"), _FAILURE_FIELD_LIMIT
            ),
            "required_action": _bounded_text(
                diagnostic.get("required_action"), _FAILURE_FIELD_LIMIT
            ),
            "resume_condition": _bounded_text(
                diagnostic.get("resume_condition"), _FAILURE_FIELD_LIMIT
            ),
        }
        candidate["goal"] = _bounded_text(
            commitment.get("goal") or candidate.get("goal"),
            _PARENT_GOAL_LIMIT,
        )
        candidate["parent_spec"] = parent_spec
        candidate["failure_guidance"] = failure_guidance
        if failure_guidance["explanation"]:
            candidate["failure_summary"] = failure_guidance["explanation"]
        return candidate

    async def _model_call(self, envelope: TurnEnvelope, situation: dict[str, Any], repair: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        messages = _decision_messages(situation, repair)
        request = {
            "model": envelope.model or self.settings.llm_model,
            "messages": messages,
            "temperature": 0.25,
            # The live 31B endpoint currently exposes a 6144-token context.
            # Situation compaction owns the input budget; keep enough headroom
            # for the chat template and a repair pass.
            "max_tokens": 1_200,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
            response = await client.post(
                f"{self.settings.llm_base_url}/chat/completions",
                json=request,
                headers={"X-LLM-Route": "gemma", "X-LLM-Priority": "chat"},
            )
        response.raise_for_status()
        body = response.json()
        content = str((((body.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        return _extract_object(content), {"request": request, "response": body}

    def _forced(self, envelope: TurnEnvelope) -> dict[str, Any]:
        if envelope.forced_action == "request_warmaster_mission":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "reply": "",
                    "task": envelope.text,
                    "warmaster_request": {
                        "user_request": envelope.text,
                        "capability_area": "unknown",
                        "why_warmaster_needed": "Пользователь явно вызвал Абаддона.",
                        "expected_outcome": envelope.text,
                        "success_conditions": [],
                        "constraints": [],
                        "known_missing_inputs": [],
                    },
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда пользователя.",
                }
            )
        if envelope.forced_action == "answer_pending_decision":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "confidence": 1.0,
                    "rationale_summary": "Явный ответ на ожидающий вопрос.",
                }
            )
        if envelope.forced_action == "continue_warmaster_mission":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда продолжить подтверждённую остановившуюся миссию.",
                }
            )
        if envelope.forced_action == "create_administratum_task":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "task": envelope.text,
                    "reply": "",
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда пользователя.",
                }
            )
        raise ValueError("this action cannot be forced without model interpretation")

    async def resolve(self, envelope: TurnEnvelope) -> dict[str, Any]:
        # Transport retries must not conflict merely because live roster,
        # memory recall or history changed while the first request was in
        # flight. The stable user intent is the idempotency identity; the full
        # situation remains in the model trace for audit.
        request_payload = {
            "session_id": envelope.session_id,
            "memory_namespace": envelope.memory_namespace,
            "source": envelope.source,
            "text": envelope.text,
            "image_attached": envelope.image_attached,
            "forced_action": envelope.forced_action,
            "correlation_id": envelope.correlation_id,
        }
        turn_id, cached = self.ledger.accept_turn(envelope.idempotency_key, request_payload)
        if cached:
            return cached
        continuation_veto = (
            ""
            if envelope.forced_action == "continue_warmaster_mission"
            else _continuation_veto_reason(envelope.text)
        )
        situation = {} if envelope.forced_action else self.situation.assemble(envelope)
        model_trace: dict[str, Any] = {}
        degraded = False
        repair_error = ""
        try:
            if envelope.forced_action:
                decision = self._forced(envelope)
                model_trace = {"forced_action": envelope.forced_action}
            else:
                raw, model_trace = await self._model_call(envelope, situation)
                try:
                    decision = normalize_decision(raw)
                except Exception as exc:
                    repair_error = str(exc)
                    guarded = (
                        _truth_guard_continuation_decision(envelope)
                        if isinstance(exc, DecisionTruthError)
                        else None
                    )
                    if guarded:
                        decision = guarded
                        model_trace = {
                            "first": model_trace,
                            "truth_guard": {
                                "error": str(exc)[:1_200],
                                "bound_parent_task_id": guarded["continue_parent_task_id"],
                            },
                        }
                    else:
                        raw, repaired_trace = await self._model_call(
                            envelope,
                            situation,
                            repair=f"{exc}; raw={raw}",
                        )
                        model_trace = {"first": model_trace, "repair": repaired_trace}
                        decision = normalize_decision(raw)
        except Exception as exc:
            truth_guard = isinstance(exc, DecisionTruthError) or "execution_claim_without_effect" in str(exc)
            guarded = _truth_guard_continuation_decision(envelope) if truth_guard else None
            if guarded:
                # The model is not trusted to invent an effect. This action is
                # derived server-side from the user's directive plus the exact
                # parent id already bound by the trusted capability manifest.
                decision = guarded
            else:
                # Ordinary model failure still degrades to the existing rich
                # answering pass. A truth failure asks only when no trusted
                # task can be bound unambiguously.
                decision = {
                    "action": "ask_clarification" if truth_guard else "answer_in_chat",
                    "reply": (
                        "Я ничего не продолжил и не запустил: вижу несколько возможных остановившихся задач. "
                        "Назови, какую именно продолжать."
                        if truth_guard
                        else ""
                    ),
                    "task": "",
                    "warmaster_request": {},
                    "artifact_delivery": {},
                    "pending_decision": {"task_id": "", "answer": ""},
                    "pending_decision_task_id": "",
                    "continue_parent_task_id": "",
                    "confidence": 0.0,
                    "reason": f"Core speech-only degradation: {type(exc).__name__}: {exc}"[:1_000],
                }
            degraded = True
            model_trace = {"degraded_error": str(exc)[:2_000]}

        if (
            decision["action"] == "continue_warmaster_mission"
            and continuation_veto
        ):
            # The model owns semantic classification. Core vetoes only when the
            # current text contains concrete contrary evidence, then asks the
            # same full-identity model to re-arbitrate with honest capabilities.
            veto_error = (
                f"current_turn_continuation_veto:{continuation_veto}: "
                "the current turn contains direct evidence against continuing an old task. "
                "Task memory remains reference context, not permission."
            )
            try:
                raw, repaired_trace = await self._model_call(
                    envelope,
                    situation,
                    repair=veto_error,
                )
                repaired = normalize_decision(raw)
                if repaired["action"] == "continue_warmaster_mission":
                    raise DecisionTruthError(
                        "current_turn_continuation_veto_was_ignored"
                    )
                if (
                    repaired["action"] not in {"answer_in_chat", "ask_clarification"}
                    and not _effect_request_is_explicit(
                        envelope.text,
                        repaired["action"],
                    )
                ):
                    raise DecisionTruthError(
                        "current_turn_continuation_veto_cannot_substitute_unrequested_effect"
                    )
                decision = repaired
                repair_error = veto_error
                model_trace = {
                    "first": model_trace,
                    "current_turn_veto_repair": repaired_trace,
                }
            except Exception as exc:
                decision = _continuation_not_authorized_decision()
                degraded = True
                repair_error = f"{veto_error}; repair failed: {type(exc).__name__}: {exc}"[:2_000]
                model_trace = {
                    "first": model_trace,
                    "current_turn_veto_repair_error": str(exc)[:2_000],
                }

        if decision["action"] == "answer_pending_decision":
            trusted_ids = pending_decision_ids(envelope.capability_manifest)
            root_id = str(envelope.capability_manifest.get("pending_decision_task_id") or "").strip()[:240]
            proposed_id = str(decision.get("pending_decision_task_id") or "").strip()
            if proposed_id in trusted_ids:
                bound_task_id = proposed_id
            elif len(trusted_ids) == 1:
                bound_task_id = trusted_ids[0]
            elif not proposed_id and root_id in trusted_ids:
                # With no explicit identity, an ordinary short answer naturally
                # belongs to the most recently asked question published at root.
                bound_task_id = root_id
            else:
                bound_task_id = ""
            decision["pending_decision_task_id"] = bound_task_id
            decision["pending_decision"] = {
                "task_id": bound_task_id,
                "answer": envelope.text.strip(),
            }

        if decision["action"] == "deliver_artifact":
            delivery = (
                decision.get("artifact_delivery")
                if isinstance(decision.get("artifact_delivery"), dict)
                else {}
            )
            proposed_artifact_id = str(delivery.get("artifact_id") or "").strip()
            trusted_artifact_ids = available_artifact_ids(
                envelope.capability_manifest
            )
            if not proposed_artifact_id and len(trusted_artifact_ids) == 1:
                decision["artifact_delivery"] = {
                    "artifact_id": trusted_artifact_ids[0]
                }

        if (
            decision["action"] == "continue_warmaster_mission"
            and continuation_veto
        ):
            # Final fail-closed boundary: no later binding/authority code may
            # turn reference memory into permission even if a repair regresses.
            decision = _continuation_not_authorized_decision()
            degraded = True

        if decision["action"] == "continue_warmaster_mission":
            trusted_ids = continuable_task_ids(envelope.capability_manifest)
            root_id = str(
                envelope.capability_manifest.get("continuation_parent_task_id") or ""
            ).strip()[:240]
            proposed_id = str(decision.get("continue_parent_task_id") or "").strip()
            if proposed_id:
                bound_parent_id = proposed_id if proposed_id in trusted_ids else ""
            elif root_id in trusted_ids:
                bound_parent_id = root_id
            elif len(trusted_ids) == 1:
                bound_parent_id = trusted_ids[0]
            else:
                bound_parent_id = ""
            decision["continue_parent_task_id"] = bound_parent_id

        authorization = self.authority.authorize(
            decision["action"],
            decision,
            envelope.capability_manifest,
            forced=bool(envelope.forced_action),
            context_scope=envelope.source,
        )
        if authorization.verdict != "auto":
            direct_explanation = authorization.code in {
                "artifact_catalog_unavailable",
                "incomplete_artifact_delivery",
                "artifact_not_in_capability",
                "continuation_unavailable",
                "continuation_task_mismatch",
            }
            decision = {
                "action": "ask_clarification",
                "reply": authorization.explanation if direct_explanation else (
                    f"Я не буду выполнять это молча: {authorization.explanation} "
                    "Уточни, какое именно разрешение ты даёшь для этого действия."
                ),
                "task": "",
                "warmaster_request": {},
                "artifact_delivery": {},
                "pending_decision": {"task_id": "", "answer": ""},
                "pending_decision_task_id": "",
                "continue_parent_task_id": "",
                "confidence": 1.0,
                "reason": authorization.code,
            }

        commitment = None
        effect = None
        effect_to_persist = None
        commitment_ref_id = None
        action = decision["action"]
        if action in {
            "request_warmaster_mission",
            "continue_warmaster_mission",
            "create_administratum_task",
            "deliver_artifact",
        }:
            commitment_id = new_id("commitment")
            effect_id = new_id("effect")
            if action == "request_warmaster_mission":
                request = decision["warmaster_request"]
                stable_task_id = "core-" + commitment_id.split("-", 1)[-1][:20]
                goal_id = stable_task_id
                root_task_id = stable_task_id
                payload = {
                    "message": warmaster_message(request),
                    "task_id": stable_task_id,
                    "goal_id": goal_id,
                    "task_memory_id": goal_id,
                    "root_task_id": root_task_id,
                    "idempotency_key": effect_id,
                    "warmaster_request": request,
                }
                destination = "abaddon"
                goal = request.get("expected_outcome") or request.get("user_request")
                kind = "abaddon_mission"
            elif action == "continue_warmaster_mission":
                parent_task_id = decision["continue_parent_task_id"]
                existing = self.ledger.find_open_continuation(parent_task_id)
                existing_commitment = (
                    existing.get("commitment")
                    if isinstance(existing, dict)
                    and isinstance(existing.get("commitment"), dict)
                    else None
                )
                existing_effect = (
                    existing.get("effect")
                    if isinstance(existing, dict)
                    and isinstance(existing.get("effect"), dict)
                    else None
                )
                if existing_commitment and existing_effect:
                    existing_effect_id = str(existing_effect.get("id") or "").strip()
                    effect = {
                        "id": existing_effect_id,
                        "commitment_id": str(existing_commitment.get("id") or ""),
                        "kind": "continue_warmaster_mission",
                        "destination": "abaddon",
                        "payload": dict(existing_effect.get("payload") or {}),
                        "idempotency_key": existing_effect_id,
                        "max_attempts": 3,
                        "state": str(existing_effect.get("state") or ""),
                        "reused_existing": True,
                    }
                    commitment_ref_id = str(existing_commitment.get("id") or "")
                    model_trace = {
                        **model_trace,
                        "continuation_dedupe": {
                            "parent_task_id": parent_task_id,
                            "commitment_id": commitment_ref_id,
                            "effect_id": existing_effect_id,
                        },
                    }
                elif existing_commitment:
                    # An open linked child without its durable effect is an
                    # invariant failure. Do not create a twin to hide it.
                    decision = {
                        "action": "answer_in_chat",
                        "reply": (
                            "Продолжение этой задачи уже зарегистрировано, но его запуск сейчас "
                            "нельзя надёжно подтвердить. Новую копию я не создаю."
                        ),
                        "task": "",
                        "warmaster_request": {},
                        "artifact_delivery": {},
                        "pending_decision": {"task_id": "", "answer": ""},
                        "pending_decision_task_id": "",
                        "continue_parent_task_id": "",
                        "confidence": 1.0,
                        "reason": "existing_continuation_effect_missing",
                    }
                    action = "answer_in_chat"
                    commitment_ref_id = str(existing_commitment.get("id") or "")
                    effect = None
                else:
                    candidate = self._continuation_candidate(
                        envelope.capability_manifest,
                        parent_task_id,
                    )
                    stable_task_id = "core-" + commitment_id.split("-", 1)[-1][:20]
                    parent_spec = (
                        candidate.get("parent_spec")
                        if isinstance(candidate.get("parent_spec"), dict)
                        else {}
                    )
                    root_task_id = str(
                        parent_spec.get("root_task_id")
                        or parent_spec.get("task_id")
                        or parent_task_id
                    ).strip()[:240]
                    task_memory_id = str(
                        parent_spec.get("task_memory_id")
                        or parent_spec.get("goal_id")
                        or root_task_id
                    ).strip()[:240]
                    goal_id = str(
                        parent_spec.get("goal_id") or task_memory_id
                    ).strip()[:240]
                    payload = {
                        "message": continuation_message(candidate),
                        "task_id": stable_task_id,
                        "goal_id": goal_id,
                        "task_memory_id": task_memory_id,
                        "root_task_id": root_task_id,
                        "parent_task_id": parent_task_id,
                        "continuation_of": parent_task_id,
                        "parent_spec": parent_spec,
                        "failure_guidance": candidate.get("failure_guidance") or {},
                        "idempotency_key": effect_id,
                    }
                    destination = "abaddon"
                    goal = str(candidate.get("goal") or "Продолжить остановившуюся миссию.")
                    kind = "abaddon_mission"
            elif action == "create_administratum_task":
                payload = {
                    "task": decision["task"],
                    "source_text": envelope.text,
                    "session_id": envelope.session_id,
                    "source": envelope.source,
                    "model": envelope.model or self.settings.llm_model,
                    "idempotency_key": effect_id,
                }
                destination = "archive_adapter"
                goal = f"Записать в Administratum: {decision['task']}"
                kind = "administratum_task"
            else:
                delivery = decision["artifact_delivery"]
                client_request_id = str(
                    envelope.correlation_id or envelope.idempotency_key or ""
                ).strip()
                if client_request_id.startswith("archive-turn:"):
                    client_request_id = client_request_id[len("archive-turn:") :]
                payload = {
                    "artifact_id": delivery["artifact_id"],
                    "session_id": envelope.session_id,
                    "source": envelope.source,
                    "client_request_id": client_request_id[:160],
                    "idempotency_key": effect_id,
                }
                destination = "archive_artifact_adapter"
                goal = f"Доставить пользователю зарегистрированный артефакт {delivery['artifact_id']}."
                kind = "artifact_delivery"
            if effect is None and action in {
                "request_warmaster_mission",
                "continue_warmaster_mission",
                "create_administratum_task",
                "deliver_artifact",
            }:
                commitment = {
                    "id": commitment_id,
                    "kind": kind,
                    "owner": "shushunya",
                    "goal": goal,
                    "spec": payload,
                    "state": "queued",
                    "priority": 50,
                    "max_attempts": 3,
                    "delegate_kind": destination,
                    "honest_status": "Решение принято; реальный эффект ещё не подтверждён органом.",
                }
                effect = {
                    "id": effect_id,
                    "commitment_id": commitment_id,
                    "kind": action,
                    "destination": destination,
                    "payload": payload,
                    "idempotency_key": effect_id,
                    "max_attempts": 3,
                }
                effect_to_persist = effect
                commitment_ref_id = commitment_id

        attention = decide_attention(
            owner_waiting=True,
            urgency=1.0,
            novelty=1.0,
            actionability=1.0,
            owner_required=action == "ask_clarification",
        )
        resolution = {
            "ok": True,
            "turn_id": turn_id,
            "decision": decision,
            "capabilities": envelope.capability_manifest,
            "effect": effect,
            "commitment_id": commitment_ref_id,
            "attention": attention.__dict__,
            "core": {
                "degraded": degraded,
                "repair_error": repair_error,
                "authorization": authorization.__dict__,
                "situation_diagnostics": envelope.context.diagnostics,
            },
            # Audit records contain prompts/results but never model hidden
            # reasoning; Gemma is instructed to return only a rationale summary.
            "protocol": model_trace,
        }
        return self.ledger.save_turn_resolution(
            idempotency_key=envelope.idempotency_key,
            turn_id=turn_id,
            resolution=resolution,
            commitment=commitment,
            effect=effect_to_persist,
        )

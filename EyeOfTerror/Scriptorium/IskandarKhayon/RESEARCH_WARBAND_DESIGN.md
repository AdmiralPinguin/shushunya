# Research Warband — исполнимый дизайн v2

Статус: **контракт к строительству, legacy остаётся production до cutover**
(2026-07-12).

Этот документ конкретизирует `REWRITE_DECISION.md`. Он сохраняет сильную часть
первого проекта — evidence-first исследование — и исправляет три смешанных уровня:

1. целостность и воспроизводимость провенанса;
2. семантическую поддержку claim источником;
3. истинность claim.

Система может гарантировать первое, проверять второе отдельным semantic pass и
обязана честно выражать неопределённость третьего. Внутренний semantic pass не
является эпистемически независимым: все смысловые роли исполняет одна физическая
Gemma 31B. Независимую оценку даёт только внешний evaluator. Хэш или дословная
цитата сами по себе не превращают утверждение в истину.

## Неподвижная граница полномочий

```text
Abaddon
  -> IskandarKhayon (governor, public port 7101)
       -> ResearchWarband (native execution backend, shadow port 7201)
```

- **Abaddon** принимает приказ, выбирает губернатора и ведёт внешний lifecycle.
- **Iskandar** принимает лидерское решение: цель исследования, приоритеты,
  область, допустимые источники и языки, стандарт уверенности, условия успеха,
  компромиссы и причины эскалации.
- **ResearchWarband** сама строит подробный ResearchSpec, поисковые запросы,
  порядок чтения, гипотезы, evidence graph, текст и циклы проверки/доработки.

Искандар не является Scout, Reader, подробным планировщиком или Writer. Варбанда
не регистрируется фиктивным Mechanicum worker. Для неё нужен native backend route,
как у Ceraxia/Skitarii, но без копирования code-specific контрактов.

Legacy Iskandar продолжает слушать `7101` до прохождения eval. Во время shadow
публичный facade может направлять явно помеченные native-миссии на `7201`, а
обычные production-миссии — в старый pipeline. Cutover переключает backend за
facade, а не переносит личность Искандара в worker-service.

## Iskandar Research Directive

Единственный лидерский handoff имеет строгую схему и caller bindings:

```json
{
  "kind": "iskandar_research_directive",
  "version": 1,
  "task_id": "...",
  "mission_id": "...",
  "leader": "IskandarKhayon",
  "decision": "delegate | needs_clarification | escalate | reject",
  "delegated_to": "ResearchWarband",
  "research_objective": "...",
  "depth": "brief | standard | deep | exhaustive",
  "source_policy": "primary_required | authoritative_preferred | balanced | open_discovery",
  "error_tolerance": "strict | balanced | exploratory",
  "answer_mode": "direct_answer | research_brief | investigation | comparative_review | source_map | translation_analysis",
  "priorities": ["..."],
  "allowed_source_classes": ["primary_source | official_documentation | standards_specification | legal_or_regulatory | peer_reviewed_research | scholarly_secondary | reputable_journalism | archival_catalog | user_provided_corpus | community_source | anonymous_or_unverified_web | machine_generated_summary"],
  "prohibited_source_classes": ["same strict source-class enum"],
  "constraints": ["..."],
  "success_conditions": ["..."],
  "output_requirements": ["..."],
  "escalation_conditions": ["..."],
  "clarification_question": "one exact question only for needs_clarification; otherwise empty"
}
```

Схема запрещает подробные планы, роли, шаги, URL, поисковые запросы, выбор
конкретных источников, имена файлов, команды и tool calls — в том числе когда
они спрятаны внутрь строковых значений разрешённых полей. Source-class поля
принимают только перечисленные enum-значения, а не URL, домены или запросы.
Явные ограничения, success conditions и причины эскалации из
`commander_order` не могут быть отброшены моделью.

Native run package хранит точный bounded `commander_order.json`. Receipt
содержит отдельный `commander_order_sha256`; `prepare_request_sha256` также
вычисляется из полной канонической команды, цели, task/mission identity. Поэтому
директива проверяется не только по собственной схеме, но и повторно против
сохранённого caller authority. Исходный `user_request` остаётся отдельным
acceptance source и не подменяется лидерской директивой.

## Один backend, логические роли

```text
directive
  -> ResearchSpec + visible coverage tree
  -> Scout <-> Reader
  -> immutable SourceSnapshots + EvidenceLedger
  -> Analyst (gap loop; hypotheses only when justified by research mode)
  -> Writer (draft units carry claim refs)
  -> deterministic verifier
  -> context-isolated Gemma 31B semantic review
  -> accepted | accepted_with_uncertainty | search_more | clarify | blocked
```

Scout, Reader, Analyst, Writer и Verifier — изолированные контексты и политики
одного stateful backend, а не десять процессов и не обязательные фиксированные
шаги каждой миссии. Все внутренние смысловые роли исполняет Gemma 31B. Reader
coverage и semantic review запускаются как fresh context-isolated same-model
passes и обязаны публиковать `epistemic_independence_claimed=false`.

ResearchSpec выбирает режим:

- `lookup` — поиск конкретного ответа и gap-driven проверка;
- `synthesis` — объединение независимых источников;
- `investigation` — конкурирующие гипотезы и различающие улики;
- `interpretation` — аргументы, контраргументы и область применимости;
- `translation` — выравнивание оригинала и перевода, терминология и варианты.

Обязательные 2–3 гипотезы допустимы только в `investigation`/`interpretation`.
Для lookup они создают выдуманные альтернативы и лишний расход.

## Источник истины: не один JSON, а связанный ledger

Минимальное ядро данных:

```text
SourceSnapshot
  id, original_uri, final_uri, retrieved_at,
  raw_sha256, normalized_sha256, media_type, language,
  parser_name/version, archive_ref, fetch/redirect metadata

SourceSpan
  id, snapshot_id, typed_locator, normalized_start/end,
  excerpt, excerpt_sha256, extraction_method

Claim
  id, proposition, epistemic_role, scope, importance,
  verification_status, confidence

EvidenceEdge
  claim_id, span_id,
  relation: reports | supports | refutes | qualifies | context,
  entailment_status, reviewer_provenance

Inference
  conclusion_claim_id, premise_claim_ids, rationale, alternatives

Hypothesis
  claim_id, discriminating_questions, status

Gap
  question, search_attempts, searched_scope, remaining_uncertainty
```

`epistemic_role` не называется самоуверенным `fact`: допустимы
`source_assertion`, `direct_observation`, `inference`, `assumption`.
`verification_status` хранится отдельно. Assumption никогда не становится
verified только из-за уверенного текста модели.

Локатор зависит от носителя:

- HTML: content-addressed snapshot, DOM path и offsets канонического текста;
- PDF: hash сырых байтов, страница, bbox и mapping нормализованного текста;
- EPUB: spine item/CFI и canonical span;
- FB2/XML: element path и canonical span;
- plain text: byte/character span с явной кодировкой и normalization version.

Raw bytes сохраняются отдельно от нормализованного текста. Live URL после этого
служит диагностикой свежести, а не acceptance-гейтом исторического снапшота.

## Четыре слоя проверки

### 1. Acquisition/security

Проверяются scheme/redirect/SSRF policy, media type, byte limits, parser result,
prompt-injection markers и content-addressed archive. Сбой Reader означает
`source_unavailable`, а не отрицательный факт.

### 2. Детерминированная целостность

Жёстко проверяются:

- raw/normalized hashes и parser provenance;
- разрешимость typed locator;
- excerpt и excerpt hash в конкретном snapshot;
- все cross-references ledger;
- evidence у major source assertions;
- premise graph у inference;
- claim refs каждого существенного draft unit;
- числовые, временные и entity-конфликты как кандидаты на проверку.

Эти проверки доказывают провенанс и целостность, не истинность.

### 3. Context-isolated semantic entailment

Fresh-контекст той же Gemma 31B проверяет, действительно ли span поддерживает
claim, не вырвана ли цитата из отрицания/оговорки, не выдано ли мнение за факт,
независимы ли источники и корректен ли inference. Это отдельный процессный pass,
но не отдельная физическая модель и не независимый судья; контракт явно хранит
`epistemic_independence_claimed=false`. Семантический провал может блокировать
acceptance или вернуть `search_more`; он не может переписать механические факты.
При несогласии система эскалирует неопределённость, а не выбирает удобный ответ.

### 4. Coverage и final alignment

В production Writer видит обязательные требования и coverage tree. Fresh
same-model semantic-review pass ищет пропуски и новые контраргументы. Настоящие
скрытые answer keys, rubrics и независимая оценка существуют только во внешнем
eval, недоступном всей Варбанде.

## Честные исходы

- `accepted` — вопрос закрыт в объявленной области, существенные claims прошли
  integrity и semantic gates;
- `accepted_with_uncertainty` — полезный ответ готов, но конфликт или ограничение
  невозможно снять; оно явно показано;
- `search_more` — есть конкретный различающий пробел, который можно исследовать;
- `clarify` — пользовательский выбор меняет область или вид результата;
- `blocked` — инфраструктура/источники не позволяют компетентный ответ.

«Не найдено» всегда означает «не найдено в перечисленных корпусах, языках,
запросах и временном бюджете», а не доказательство отсутствия события.

## Кумулятивная память

MVP хранит неизменяемые миссии, CAS-снапшоты и принятые ledger. Он может искать
по прошлым миссиям, но не вливает их автоматически в глобальную истину.

Постоянный knowledge graph — отдельный этап и отдельный gate. Promotion требует
версионирования, scope/time validity, source provenance, `supersedes/retracts`,
повторной валидации и защиты от межпроектного загрязнения. Wiki является
проекцией для чтения, не каноническим хранилищем claims.

## Что переиспользовать из legacy

Переиспользуются проверенные primitives за новыми адаптерами, а не worker-модули
и не старый `worker_plan`:

- HTTP/search primitives и retry policy после SSRF/redirect аудита;
- Playwright rendering после ограничения subresources;
- HTML, EPUB и FB2 extraction;
- локальный Corpus, hashing и metadata;
- commander-order bindings, progress events и run lifecycle;
- старые failure artifacts и тестовые документы.

Готового PDF parser в legacy нет; его надо реализовать. Старые функции,
проверяющие только префикс `/work/`, не считаются безопасными без resolve +
containment + symlink policy.

## Модели

Все внутренние смысловые роли Искандара исполняет Gemma 31B; модель не
наследуется от code warband. Reader coverage и semantic review — fresh
context-isolated same-model passes. Разные контексты дают процессное разделение,
но не эпистемическую независимость, поэтому каждый внутренний результат несёт
`epistemic_independence_claimed=false`.

Доверие строится на точных application-owned SourceSnapshot/SourceSpan,
локаторах и хэшах, неизменяемом evidence/provenance ledger, content-bound review
sessions и детерминированных acceptance gates. Ни имя роли, ни новый контекст не
создают независимого авторитета. Настоящая независимая оценка выполняется только
внешним evaluator с недоступными Варбанде fixtures и rubrics.

Qwen Coder принадлежит кодовой варбанде. Он не используется Искандаром, не
входит в dependencies или readiness ResearchWarband, и его недоступность не
может блокировать исследовательскую миссию. Back-translation — advisory signal,
не hard oracle.

## Eval и cutover

30 открытых задач — development smoke. Cutover требует внешнего evaluator,
замороженных private fixtures, post-freeze metamorphic canaries и shadow traffic.
Evaluator сам выделяет claims/importance и проверяет citation entailment, чтобы
Варбанда не могла выиграть метрику, помечая всё `minor` или уходя в `blocked`.

Обязательные метрики внешнего evaluator:

- provenance/locator/quote integrity;
- citation entailment precision и claim coverage;
- factual/known-answer correctness;
- hidden-facet coverage;
- contradiction detection;
- precision/recall для `clarify`, `search_more` и `blocked`;
- translation meaning errors;
- latency, model budget и recovery;
- ноль critical false-accept и ноль corruption persistent knowledge.

Legacy удаляется не сразу после переключения: сначала staged shadow/canary,
потом rollback window. Полные правила — в `RESEARCH_WARBAND_EVAL.md`.

### Ограничение текущего evaluator и future work

Текущий детерминированный oracle для части known-answer задач сравнивает ответ
с заготовленными английскими строками. Поэтому семантически правильный принятый
ответ на другом языке или в корректной перефразировке может быть ошибочно
помечен как `false_accept`. Нельзя подгонять Варбанду под эти строки или
расширять список заготовок: это маскирует дефект оценки и создаёт утечку ключа.

Нужен отдельный физически независимый semantic judge. При этом
детерминированные проверки provenance, точных span/locator и relations остаются
обязательными и не передаются модели. Пока независимого судьи нет, семантическая
правильность такого результата должна считаться `unverified`, а не
`false_accept`. Ни та же Gemma 31B в новом контексте, ни Qwen Coder на роль
независимого судьи не подходят.

## Порядок реализации

1. Машинные schema/fixtures и внешний evaluator до production pipeline.
2. Evidence core: CAS snapshots, typed spans, ledger, deterministic verifier.
3. Reader + safe acquisition/parsers; затем Scout и iterative gap loop.
4. Analyst по research modes и semantic entailment verifier.
5. Writer со structured draft units и claim refs.
6. Async mission lifecycle и shadow service `7201`.
7. Iskandar directive + native Abaddon route без удаления legacy.
8. Visible/private eval, RISC-V false-accept regression, failure injection.
9. Shadow/canary и только затем cutover.

Ни один этап не объявляется готовым по самоотчёту той же Варбанды.

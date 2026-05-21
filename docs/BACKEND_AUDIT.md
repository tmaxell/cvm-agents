# Аудит backend cvm-agents

> Дата: 2026-05-21
> Скоуп: `backend/` (~10 700 LOC активного Python).
> Цель: оценить соответствие best practices мультиагентных систем, разделить «работает на LLM» / «работает на mock», зафиксировать идеи улучшений.

## 1. Карта системы

### 1.1 Entry point и оркестрация

```
HTTP (FastAPI app.py)
   └── POST /api/chat
         └── supervisor.handle(ctx)
               ├── есть action? → _ACTION_DISPATCH → нужный агент
               └── есть message? → chat_orchestrator.classify_intent → registry.agent_for_intent → агент
```

Единая точка входа `/api/chat` — хорошо. Action‑дispatch (`save_campaign`, `start_campaign` и т. п.) маршрутизируется отдельно от message‑intent — это правильное разделение «команда» vs «свободный текст».

### 1.2 Registry активных агентов

[`backend/agents/registry.py`](../backend/agents/registry.py) регистрирует **6 агентов** через `FunctionAgent` адаптер:

| Имя | Файл | Intent / Action | Назначение |
|---|---|---|---|
| `docs` | [agent_docs.py](../backend/agents/agent_docs.py) | `documentation_qa` | RAG QA через `qa_copilot.answer` |
| `builder` | [agent_builder.py](../backend/agents/agent_builder.py) | `build_campaign`, `build_campaign_from_segment` | Сборка draft_flow: template → brief → LLM‑plan → fallback |
| `refiner` | [agent_refiner.py](../backend/agents/agent_refiner.py) | `refine_campaign` | 3 режима: по campaign_id, LLM‑modify draft_flow, append‑activity |
| `segments` | [agent_segments.py](../backend/agents/agent_segments.py) | `suggest_segments` | Гипотезы аудитории через `segment_agent.suggest_segments` |
| `attention` | [agent_attention.py](../backend/agents/agent_attention.py) | `campaign_attention` | Отчёт по портфелю + LLM enrichment топ‑5 |
| `runtime` | [agent_runtime.py](../backend/agents/agent_runtime.py) | `runtime_action` (save/start/pause) | Вызовы AdTarget |

### 1.3 Внешние интеграции

- **LLM**: единственная точка — [`backend/llm.py`](../backend/llm.py). 5 провайдеров (gemini / groq / gigachat / ollama / anthropic). Все агенты ходят через `get_llm()`.
- **AdTarget REST**: единственная точка — [`backend/tools/adtarget.py`](../backend/tools/adtarget.py). Авто‑fallback на mock при ConnectError.
- **RAG**: [`backend/rag/`](../backend/rag/) — ChromaDB + BM25 hybrid поверх документации AdTarget.
- **БД**: SQLAlchemy async + SQLite (`backend/data/cvm_agents.sqlite3`). Можно переопределить `DATABASE_URL`.

### 1.4 Schema БД (chat side)

| Таблица | Что хранит |
|---|---|
| `chat_sessions` | Сессия + last_message_preview + (опц.) campaign_id |
| `chat_messages` | Сообщения user/assistant + metadata (citations, actions, agent_meta) |
| `chat_runs` | Один run = один `/api/chat` запрос, статус + intent |
| `chat_run_events` | Trace‑события (route_selected, plan_created, step_started/completed) |
| `saved_artifacts` | draft_flow / segment_draft / target_group_draft / monitor_report / recommendation_bundle / attention_report. Дедуп по (source_run_id, sha256(content)) |

Plus demo‑данные:
- `demo_campaigns` (30 кампаний, seed при старте если `SEED_DEMO_CAMPAIGNS_ON_STARTUP=true`)
- `campaign_health` (issues_json + recommended_actions_json + severity + attention_score)

## 2. Что работает на LLM, что на mock

### 2.1 LLM используется здесь (всегда реальный вызов через get_llm)

| Место | Что просит у LLM | Формат | Fallback при ошибке |
|---|---|---|---|
| [`chat_orchestrator.classify_intent`](../backend/agents/chat_orchestrator.py#L133) | Выбрать один intent из 5 (по сообщению + last 4 messages) | JSON `{intent, confidence, reason}` | → `documentation_qa` |
| [`qa_copilot.answer`](../backend/agents/qa_copilot.py) | Ответ по документации (RAG + system prompt) | Free‑form markdown | Сообщение об ошибке наверх (`agent_docs` ловит) |
| [`segment_agent.suggest_segments`](../backend/agents/segment_agent.py) | 2–3 гипотезы сегментов с привязкой к существующим Target Groups | JSON через pydantic | Эвристический `_fallback_raw_response` |
| [`agent_attention._llm_enriched_plans`](../backend/agents/agent_attention.py#L220) | «Диагноз» + «План фикса» для топ‑5 кампаний | JSON `{plans: {<id>: {analysis, actions}}}` | `_fallback_analysis` + `_fallback_actions` из БД |
| [`builder/brief.analyze_brief`](../backend/agents/builder/brief.py) | Извлечь структурированный бриф из истории (product, channels, audience, goal) | JSON | Эвристический парсер сообщения |
| [`builder/planner.plan_flow_with_llm`](../backend/agents/builder/planner.py) | План шагов flow (типы Activity + параметры) | JSON `{steps: [...]}` | `_build_fallback_flow` (Common+TG+SMS) |
| [`builder/modify_llm.plan_modifications_with_llm`](../backend/agents/builder/modify_llm.py) | План операций модификации draft_flow | JSON `{operations: [...]}` | `detect_add_intent` deterministic → если и тот пусто, аналитические рекомендации |

**Текущий провайдер (после нашего переключения):** Gemini `gemini-2.5-flash`, free tier 250k TPM / 250 RPD.

### 2.2 Что работает на mock

| Поверхность | Mock включается когда | Источник данных |
|---|---|---|
| **Весь AdTarget REST** (campaigns, flow, statistics, validate, create, start, pause, target_groups, channels, events, offer_templates, campaign_types) | `ADTARGET_MOCK=true` в `.env` (сейчас true) **или** автоматически после первой `httpx.ConnectError` | `backend/tools/mock_data.py` |
| **Demo‑кампании в attention‑отчёте** | Всегда (если `SEED_DEMO_CAMPAIGNS_ON_STARTUP=true`) | `backend/scripts/seed_demo_campaigns.py` — 30 кампаний с детерминированно сгенерированными issues |
| `get_campaign / get_campaign_flow / get_campaign_statistics` | _Они НЕ имеют ветки `_is_mock()`_ — пойдут в реальный API, и при ConnectError упадут | — |
| `list_product_catalog / get_product_actions` | В mock‑режиме возвращают пустой `[]` (заглушка) | — |

> ⚠️ **Текущее состояние:** `ADTARGET_MOCK=true`. Значит все вызовы create/start/pause/list_*/validate проходят через `mock_data.py` — кампания «создаётся» с фейковым ID, реальной связи с AdTarget сейчас нет.

### 2.3 Что НЕ использует LLM (детерминистическая логика)

- **Структура attention‑отчёта** (summary, by_severity, kpi, issue_breakdown, top‑N ranking) — целиком Python.
- **Сборка JSON‑flow** из плана (`assemble_flow_from_plan`) — детерминистический сборщик.
- **Templates‑first** в builder: 3 эталонных сценария (data_package / gift / demo) — мгновенный возврат без LLM.
- **`refine_existing` для campaign по id** — рассчитывает fix‑рекомендации по правилам метрик (open_rate < 12, CR < 3, burn > 0.9 и т. п.).
- **`refine_draft` без LLM** — структурный анализ activities, советует добавить EventActivity / ResponseActivity и т. п.
- **Intent matching через regex** — `_RULES` в `chat_orchestrator.py` ловит однозначные команды до LLM.
- **Persistence** — никакого AI в save/start/pause (это и правильно).

### 2.4 Dead code: ~4 600 LOC

Файлы зарегистрированы в тестах, но **не вызываются** ни из registry, ни из активных агентов:

| Файл | LOC | Статус | Использование |
|---|---|---|---|
| [`campaign_builder.py`](../backend/agents/campaign_builder.py) | 2 579 | Legacy F2 builder с `bind_tools()` | Только тесты + импорт из flow_optimizer |
| [`flow_optimizer.py`](../backend/agents/flow_optimizer.py) | 306 | Legacy | Только тесты |
| [`flow_composer.py`](../backend/agents/flow_composer.py) | 341 | Legacy deterministic composer | Только тесты + campaign_builder |
| [`safety_review.py`](../backend/agents/safety_review.py) | 194 | Legacy review checklist | Только тесты + flow_optimizer + campaign_builder |
| [`campaign_optimizer.py`](../backend/agents/campaign_optimizer.py) | 449 | Legacy LLM optimizer | Только тесты + campaign_monitor |
| [`campaign_monitor.py`](../backend/agents/campaign_monitor.py) | 411 | Legacy monitor с LLM | Только тесты, единственная упоминание в проде — log_legacy_usage в schemas.py |

Это ~45% backend‑кодa, который никто не запускает в проде, но он замедляет навигацию и иногда тащит зависимости (e.g. brief/planner перетекают в новый builder через свои пути, но старые остаются «на всякий»).

## 3. Соответствие best practices MAS

Сравниваю с типовыми паттернами из LangGraph, OpenAI Agents SDK, Anthropic «Building Effective Agents».

### 3.1 Что сделано правильно

| Паттерн | Реализация |
|---|---|
| **Single supervisor / orchestrator** | `supervisor.handle()` — единая точка маршрутизации; action vs message разделены чисто. |
| **Agent registry + protocol** | `AgentProtocol` + `FunctionAgent` + `registry._AGENTS`. Добавление нового агента = 1 файл + 1 строка в registry. |
| **Tool centralization** | Все AdTarget вызовы через `tools/adtarget.py`. Все LLM — через `llm.py`. Нет «теневых» http‑клиентов. |
| **Trace / observability** | `ctx.emit(event, status, detail, metadata)` пишет в `chat_run_events` — фронт показывает план/шаги/тайминги. |
| **Persistent artifacts** | Каждый агент сохраняет результат как Artifact с дедупом по hash — можно возобновить сессию и читать draft_flow между запросами. |
| **Multi‑modal fallback** | Везде LLM‑first → deterministic fallback. Падение модели не валит весь сценарий. |
| **Hybrid retrieval** | BM25 + semantic ensemble — стандарт RAG. |
| **Mock mode для интеграций** | AdTarget API можно отключить через env, авто‑fallback при сетевой ошибке — отлично для прототипа без VPN. |
| **Sticky context** | `_detect_sticky_agent` ловит `stage=collect_brief` в последнем assistant‑метадата и продолжает диалог в Builder без re‑classify. |
| **Action contract** | Frontend получает `actions_available: [ChatAction(...)]` — единая модель для quick‑replies, save, refine, navigate. |

### 3.2 Что хромает / не дотягивает

1. **Plan = всегда 1 шаг.** [`supervisor._build_plan`](../backend/agents/supervisor.py#L147) возвращает `Plan(steps=[PlanStep(...)])` ровно из одного шага. Multi‑step orchestration не реализована, хотя инфраструктура (`Plan`, `PlanStep`, цикл по шагам) уже есть. → нет цепочек типа `suggest_segments → build_campaign_from_first_segment`.

2. **Нет LLM provider fallback.** Один 429 от Gemini = «не удалось получить ответ». В коде каждый агент сам обрабатывает try/except, но **не пробует другой провайдер**. Это критично на free‑tier (наш Groq‑случай).

3. **Нет retry / exponential backoff.** Транзиентные 5xx / 429 не ретраятся. `httpx` вызовы без `tenacity`/собственного wrapper.

4. **Trace не различает «LLM ответил» vs «fallback».** Например в attention‑агенте при 429 fallback срабатывает молча, и пользователь видит механический текст без объяснения «использован детерминированный план, потому что Gemini не ответил». В chat_orchestrator интенс‑classifier тоже не помечает «llm_classify failed: использован default → documentation_qa».

5. **JSON parsing руками.** Везде используется `re.search(r"\{.*?\}", ..., DOTALL)` + `json.loads` + ручное снятие markdown‑fences. Это хрупко на маленьких моделях. Структурированный output (`with_structured_output` или native JSON mode у провайдеров) надёжнее.

6. **Дубль ответственности orchestrator vs supervisor.** `chat_orchestrator.py` живёт в `agents/`, делает только intent classification — а supervisor строит из этого однострочный план. По сути это один компонент, разнесённый на два файла без выгоды.

7. **Огромный объём dead code (см. 2.4).** Maintenance‑bias, тесты падают/не падают независимо от прода, легко перепутать что используется.

8. **Schemas.py — 528 LOC**, половина — legacy модели (`MonitorRequest`, `MonitorResponse`, `OptimizationRecommendation`, …) для несуществующих эндпоинтов. Перемешано с актуальными `CopilotRequest/Response`, `ChatAction`, `SegmentSuggest*`.

9. **Несогласованные пределы истории.** `qa_copilot` берёт всю `request.history`, `agent_docs` шлёт `history[-6:]`, `chat_orchestrator` — `history[-4:]`, `builder` — `history[-6:]`. Нет общего правила и токен‑бюджета.

10. **Нет LLM cache.** Повторные QA‑вопросы тратят токены каждый раз. На free‑tier это критично — semantic cache уменьшил бы RPD x3‑5.

11. **Нет regression evals.** Сменили модель → качество могло просесть, узнаем только из тикетов. На demo‑этапе ОК, но без evals переключаться между Gemini/Groq/GigaChat — гадание.

12. **Нет защиты от prompt injection.** В docs QA пользовательский ввод подаётся как HumanMessage без sanitization; в system‑prompt есть `{rag_context}` — RAG‑источники в теории могут содержать инструкции, перебивающие system prompt (мало вероятно, но возможно).

13. **Rate‑limit awareness отсутствует.** Builder с `bind_tools()` на тяжёлой модели за одну сессию может выкосить дневной лимит и блокировать всех остальных пользователей системы.

14. **Артефакты только в БД, без TTL.** `saved_artifacts` растёт без cleanup. Для прода нужен retention policy.

15. **Нет конкурентного выполнения шагов.** Текущий цикл по `plan.steps` — последовательный. Когда появится multi‑step, для независимых шагов хочется `asyncio.gather`.

16. **AdTarget API в `tools/adtarget.py` смешивает HTTP‑клиент, токен‑кэш, mock‑switch и бизнес‑методы.** Хорошо для прототипа, но 350 LOC в одном файле — повод разнести (`http.py` + `mocks.py` + `endpoints.py`).

17. **Tests мокают SQLAlchemy через monkeypatch.** Хрупко при апгрейде SQLAlchemy. Лучше testcontainers / inmemory sqlite per test.

18. **Документация архитектуры разбросана.** `AI_AGENTS_MASTERPLAN` (исключён из RAG) — это план, а не описание. `BUILDER_SIMPLIFICATION_PLAN` — тоже план. Описания текущей архитектуры в одном месте нет — отсюда этот файл.

## 4. Приоритизированный backlog улучшений

Размечено по **impact** × **усилию**.

### 4.1 Quick wins (≤ 1 день, высокий impact)

1. **Auto‑fallback между LLM провайдерами.** В `llm.py` добавить `LLM_FALLBACK_PROVIDERS=gemini,groq,gigachat` и обёртку, которая при `429 / 5xx` пробует следующий. Решает наш Groq‑случай радикально.
2. **Retry с backoff.** Обернуть `llm.ainvoke()` и `adtarget._get/_post/_put` в tenacity (3 попытки, exp backoff, retry только на 429/5xx/ConnectError).
3. **Trace: пометка fallback‑путей.** Когда attention/builder/refiner/segments идёт по deterministic ветке после ошибки LLM — emit `step_completed` с `status="warning"` и `metadata={"llm": false, "reason": "..."}`. Фронт сможет показать badge «генерировано без LLM».
4. **Удалить dead code.** `campaign_builder.py`, `campaign_monitor.py`, `campaign_optimizer.py`, `flow_composer.py`, `flow_optimizer.py`, `safety_review.py` + тесты на них. ~4 600 LOC. Если что‑то понадобится — лежит в git‑history.
5. **Согласовать history‑slicing.** Завести `agents/base.history_window(history, k)` с общим правилом + токен‑бюджет (через простой char‑count). Использовать во всех агентах.
6. **Чистка `schemas.py`.** Удалить `MonitorRequest/Response`, `OptimizationRecommendation` и связанные `_log_legacy_usage` вызовы. Сократится ~150 LOC.

### 4.2 Среднее (2–5 дней)

7. **Structured output вместо ручного JSON‑parsing.** В местах LLM→JSON (intent classifier, brief, planner, modify_llm, attention enrichment) использовать `llm.with_structured_output(PydanticModel)` или JSON mode у провайдера. Меньше хрупкости, меньше fallback‑срабатываний от bad parse.
8. **Multi‑step plans.** Реализовать `_build_plan()` который для `suggest_segments` + явного «и создай кампанию» возвращает 2 шага: `segments` → `builder` с `inputs.segment=<result>`. `supervisor` уже умеет передавать artifacts между шагами.
9. **Semantic LLM cache для docs QA.** In‑memory LRU (или Redis) с ключом по embedding(question) + cosine threshold 0.95. Экономит 60–80% RPD на повторных вопросах в demo.
10. **Объединить `chat_orchestrator.py` и `supervisor.py`.** Один модуль `agents/orchestrator/` с подмодулями `classifier.py` (rules+LLM), `planner.py`, `executor.py`. Сейчас супервизор — это `executor`, а classifier живёт отдельно — нелогично.
11. **Eval‑framework для QA.** Список из 30–50 эталонных Q→A‑пар по документации (отдельный JSON). Скрипт `python -m evals.docs_qa` гоняет их, считает precision/recall по упоминанию ключевых терминов из эталонного ответа, плюс LLM‑judge для содержательности. Раз в неделю + после смены модели.
12. **Разнести `tools/adtarget.py`.** В `tools/adtarget/`: `http.py` (httpx + token cache), `mock.py` (switching + import mock_data), `endpoints.py` (бизнес‑методы). Снижает когнитивную нагрузку.

### 4.3 Стратегические (>1 неделя)

13. **Перейти на LangGraph (или эквивалент).** Текущий `Plan/PlanStep/supervisor` — это собственный мини‑LangGraph. Если планы станут сложнее (ветвление, retry, human‑in‑the‑loop, parallel steps) — выгоднее уйти на готовый граф‑оркестратор с persistence из коробки. Сейчас overengineering, через пару месяцев — спасение.
14. **Streaming ответов.** Сейчас `/api/chat` ждёт полный ответ. Для QA это 2–8 секунд — хочется SSE/WebSocket. Все провайдеры в `llm.py` поддерживают streaming.
15. **RAG evaluation + улучшение чанкинга.** Для `.txt` файлов (продуктовая дока AdTarget) сейчас нет header‑splitting (только Markdown). Распарсить RST‑заголовки `[Раздел](#id)` и приклеить как metadata heading_path. Сделает Sources в UI понятнее (сейчас heading_path пустой для txt).
16. **Prompt versioning.** Хранить промпты не в коде, а в `prompts/` (yaml или md) с версионированием. Дать аналитикам менять без релиза. Особенно важно для qa_copilot и intent classifier.
17. **Rate‑limit middleware.** Перед `/api/chat` — лимит RPS per user (in‑memory token bucket). Защищает дневную квоту LLM от одного пользователя.
18. **Artifact retention + sessions cleanup.** Cron‑скрипт: `chat_sessions` старше 90 дней → archive→delete; `saved_artifacts` без incoming ref → delete. Сейчас БД растёт unbounded.
19. **Prompt injection guard для RAG.** На входе пользовательского сообщения — короткий LLM‑классификатор «это похоже на попытку перебить инструкции?» + санитайзер для извлечённого RAG‑контекста (escape тройные кавычки, фильтр строк начинающихся на `Ignore previous`).
20. **Подключить real AdTarget на стенде.** Сейчас `ADTARGET_MOCK=true` повсюду. Должен быть отдельный env‑profile `prod` с `ADTARGET_ENV_POLICY=production` (уже в коде — `_IS_PRODUCTION_POLICY`), при котором mock запрещён даже как auto‑fallback. CI/CD должен катить `prod` config на стенд.

## 5. Резюме

**Архитектурно проект здоровый:** SoC соблюдён, регистрация агентов простая, единый supervisor, observability через trace, fallback‑пути везде. Это аккуратная реализация классического MAS‑паттерна **«router → specialized agent → tool/persistence»** уровня хорошего production‑прототипа.

**Узкие места — операционные**, а не архитектурные:
1. Нет cross‑provider LLM‑fallback (#1 по impact, особенно после Groq‑инцидента).
2. ~45% backend кода — legacy, не используется в проде.
3. JSON‑парсинг руками вместо structured output.
4. Нет evals → переключение моделей вслепую.

Эти 4 пункта стоят сделать в первую очередь: первый — обязательно (демо‑риск), остальные — для устойчивости при росте.

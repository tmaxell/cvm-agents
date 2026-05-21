# План: добавление недостающего функционала под demo-сценарий

> Сопроводительный документ к `MVP Requirements _ Прототипы AI-фич.docx`, раздел 9.2 (Сборка новой кампании от вопроса до сохранения).
> Дата: 2026-05-21. Базируется на текущем состоянии backend (см. `BACKEND_AUDIT.md`).

## 1. Что уже работает (фундамент сценария 9.2)

| Шаг сценария | Реализовано в прототипе | Где живёт |
|---|---|---|
| 1. Q&A по таргет-группам | ✅ DocsAgent через `/api/chat` с RAG | `agents/agent_docs.py`, `agents/qa_copilot.py` |
| 2. «Собери сегмент …» | ✅ SegmentsAgent → 2–3 гипотезы | `agents/agent_segments.py`, `agents/segment_agent.py` |
| 3. Гипотезы с обоснованием + actions | ✅ артефакт `segment_draft`, quick-actions | `schemas.SegmentHypothesis` |
| 5. «Собери кампанию на основе сегмента» | ✅ action `build_campaign_from_segment` → BuilderAgent | `agents/supervisor.py:_ACTION_DISPATCH` |
| 6. Brief Analyzer задаёт уточняющие вопросы | ✅ `is_ready_to_build(brief)` → `_ask_clarifying` | `agents/builder/brief.py`, `agent_builder.py` |
| 9. Сборка draft_flow LLM-планировщиком | ✅ `plan_flow_with_llm` + `assemble_flow_from_plan` | `agents/builder/planner.py` |
| 10. «Добавь Wait…» | ✅ RefinerAgent → LLM-modify | `agents/builder/modify_llm.py`, `agent_refiner.py` |
| 12. Save → AdTarget mock | ✅ RuntimeAgent `save_campaign` | `agents/agent_runtime.py` |

## 2. Чего не хватает (4 gap-фичи)

| Gap | Шаги сценария | Сложность | Зависит от |
|---|---|---|---|
| **A. TargetGroupAssignment** — превратить segment_draft в назначенную target group | 4 | S | — |
| **B. ProductCatalogPicker** — выбор продукта из mock каталога | 7 | S | mock_data |
| **C. OfferAgent** — генерация 2–3 вариантов оффера + выбор | 7, 8 | M | B |
| **D. ChannelSplit** — сплит flow на параллельные ветки коммуникации | 11 | M | flow_builder schema |

Дальше — детальный план по каждой.

---

## A. TargetGroupAssignment

**Шаг сценария:** «выбираем сегмент и назначаем его таргет-группой».

**Проблема.** Сейчас `SegmentsAgent` сохраняет артефакт `segment_draft` и предлагает action `build_campaign_from_segment` (сразу в BuilderAgent). Но между «гипотеза сегмента» и «целевая группа для активации» концептуально должен быть шаг назначения: пользователь должен понимать, что выбранная гипотеза становится конкретной TargetGroup, на которую сошлётся `TargetGroupActivity`.

**Решение** (минимальное расширение, без новых агентов):

1. Новый action `assign_segment_as_target_group` в `RuntimeAgent._DISPATCH`. Логика:
   - читает `segment` из payload (или последний `segment_draft` из сессии);
   - либо берёт `matched_target_group.target_group_id` (если LLM нашёл существующую ЦГ),
   - либо вызывает `adtarget.create_target_group(name, criteria)` — **в mock возвращает синтетический id**, в реальном API — создаёт через `POST /TargetGroups` (если контракт допускает);
   - сохраняет артефакт `target_group_draft` со полями `{ target_group_id, name, source_segment_id }`;
   - устанавливает `ctx.store.set_target_group_id(session_id, target_group_id)` (новый метод в `ChatStore` — чтобы BuilderAgent потом подхватил).

2. `SegmentsAgent` дополняет actions: добавить третью кнопку **«Назначить таргет-группой»** между «Сохранить сегмент» и «Создать кампанию из сегмента». Сейчас в [`agent_segments.py:67-72`](../backend/agents/agent_segments.py#L67) две кнопки — добавить третью.

3. `BuilderAgent` в [`_finalize`](../backend/agents/agent_builder.py#L218) и `planner.assemble_flow_from_plan` должен брать `target_group_id` из артефакта `target_group_draft` или сессии, а не дефолтить в `1`.

4. Новый mock-эндпоинт `tools/adtarget.create_target_group(name, criteria, source_segment)` + соответствующая `MOCK_TARGET_GROUP_CREATE_RESULT` в `mock_data.py`.

**Acceptance:**
- После выбора гипотезы видна третья кнопка, клик создаёт `target_group_draft` (видно в trace).
- BuilderAgent в следующем шаге собирает flow с правильным `target_group_id` (а не `1`).
- В реестре сессии `target_group_id` сохранён.

**Файлы:** `agents/agent_segments.py`, `agents/agent_runtime.py`, `agents/builder/planner.py`, `tools/adtarget.py`, `tools/mock_data.py`, `db.py` (метод `set_target_group_id`).

**Оценка:** ~0.5 дня.

---

## B. ProductCatalogPicker

**Шаг сценария:** «выбираем … продукт из продуктового каталога».

**Проблема.** В `tools/adtarget.list_product_catalog()` в mock-ветке возвращается **пустой `[]`** — каталог не наполнен. BuilderAgent в Brief Analyzer спрашивает «какой продукт», но пользователю некуда тыкнуть.

**Решение.**

1. Наполнить `MOCK_PRODUCT_CATALOG` в `tools/mock_data.py` 5–8 правдоподобными продуктами с полями: `{id, name, category, description, recommended_channels, default_offer_template_id}`. Категории: тариф, пакет данных, опция, контент-подписка, страхование, доп. сервис.

2. `tools/adtarget.list_product_catalog()` и `get_product_actions(id)` в mock возвращают этот каталог. В реальном API оставить путь без изменений.

3. Новый агент-helper или метод в `BuilderAgent`, который при отсутствии `brief.product` подкладывает **quick-replies** не «свободный текст», а кнопки с топ-N продуктов из каталога. Сейчас в [`_ask_clarifying`](../backend/agents/agent_builder.py#L143) для отсутствующего product кнопок нет — добавить.

4. При выборе продукта (через `clarify_reply` payload) BuilderAgent сохраняет `brief.product` и при сборке flow через planner подкладывает `default_offer_template_id` в `BusinessTransactionActivity`.

**Acceptance:**
- При отсутствии product Builder показывает 3–5 product-кнопок из каталога.
- Клик по кнопке → `brief.product` заполнен → Builder продолжает.
- В artifact draft_flow `BusinessTransactionActivity.offer_template_id` ≠ null.

**Файлы:** `tools/mock_data.py`, `tools/adtarget.py`, `agents/agent_builder.py`, `agents/builder/brief.py`.

**Оценка:** ~0.5 дня.

---

## C. OfferAgent — генерация и выбор офферов

**Шаги сценария:** 7 («сгенерируй варианты оффера»), 8 («выбираем оффер»).

**Проблема.** Сейчас текст коммуникации генерируется внутри `plan_flow_with_llm` одним вызовом LLM и сразу попадает в `CommunicationActivity`. Нет шага «покажи 2–3 варианта → пользователь выбирает». Это была фича MVP 7 («Генерация контента») из docx, но в прототипе её нет.

**Решение — отдельный агент с зарегистрированным intent:**

### Архитектура

```
intent generate_offers (новый)
   └── OfferAgent (новый файл agents/agent_offer.py)
         ├── input: brief (продукт, канал, аудитория, тональность, повод)
         ├── LLM (Gemini): JSON {variants: [{id, text, tone, hook, length_chars, why_relevant}]}
         └── output:
              - assistant_message: 3 карточки оффера с обоснованием
              - artifacts: [offer_variants]
              - actions: [select_offer(variant_id) × 3]
```

### Intent классификация

В `agents/chat_orchestrator.py`:
- Новый intent `generate_offers`.
- Rules: `r"\b(сгенерируй|собери|подбери|дай|покажи)\s+(вариант\w*\s+)?оффер"`, `r"\boffer\s+variants?"`.
- LLM few-shot example: «Сгенерируй варианты оффера» → `generate_offers`.

### Activation pattern

OfferAgent активируется в двух режимах:
1. **Командный** — пользователь напрямую пишет «дай варианты оффера для тарифа Max».
2. **Inline в Builder** — BuilderAgent в `_finalize` или ещё до него вызывает OfferAgent как sub-step, если в `brief` уже есть product+channel+audience, но `brief.offer` пуст и пользователь явно попросил «сгенерируй оффер». Это требует **multi-step plan** в supervisor (пункт #8 из BACKEND_AUDIT, который ещё не сделан). Простой вариант: BuilderAgent в `_ask_clarifying` для missing «offer» предлагает quick-action **«Сгенерируй варианты оффера»** (kind=intent, payload={intent: "generate_offers"}). Frontend трансформирует action в новое сообщение.

### Select offer

Новый action `select_offer` в `_ACTION_DISPATCH`:
- Маршрутизация → RuntimeAgent или прямо в BuilderAgent.
- Payload: `{variant_id, variant_text, channel, product_id}`.
- Effects:
  - Сохранение артефакта `offer_choice` (text + metadata).
  - Установка `ctx.inputs["offer_text"] = variant_text` для следующего шага.
  - Если в сессии уже есть `draft_flow` — RefinerAgent через LLM-modify заменяет текст в существующей `CommunicationActivity`. Если нет — флаг «оффер выбран», BuilderAgent при сборке берёт текст из артефакта вместо генерации.

### Промпт OfferAgent

```
Ты — копирайтер CVM-кампаний для оператора связи. Сгенерируй 3 варианта оффера
под продукт {product.name} ({product.description}) для канала {channel} и аудитории
{audience.description}. Тональность: {tone or 'нейтральная промо'}. Сезонный повод:
{occasion or 'нет'}. Ограничения канала: SMS ≤ 160 chars, Push ≤ 90 chars, Email ≤ 300 chars.

Верни JSON: {"variants": [
  {"id": "v1", "text": "...", "tone": "...", "hook": "<главный приём — скидка/ограничение/выгода>",
   "why_relevant": "<1 предложение — почему сработает на этом сегменте>"}
]}.
3 варианта должны отличаться hook'ом, не просто переформулировкой.
```

### Acceptance

- Пользователь пишет «сгенерируй оффер» (или нажимает quick-action) → видит 3 карточки с разной длиной и hook'ом.
- Под каждым оффером кнопка «Выбрать»; клик сохраняет `offer_choice`.
- В последующем `draft_flow` `CommunicationActivity.text` содержит ровно выбранный текст.
- При недоступности LLM — fallback на 1 шаблонный оффер из `offer_templates` mock с пометкой «использован шаблон».

**Файлы:**
- Новый: `agents/agent_offer.py`, регистрация в `agents/registry.py`.
- Изменить: `agents/chat_orchestrator.py` (intent + rules), `agents/supervisor.py:_ACTION_DISPATCH` (action `select_offer`), `agents/agent_builder.py:_ask_clarifying` (quick-action для генерации оффера).
- Схемы: добавить `OfferVariant`, `OfferGenerateRequest`, `OfferGenerateResponse` в `schemas.py`.
- БД: добавить `offer_variants` и `offer_choice` в `_SUPPORTED_ARTIFACT_TYPES` в [`db.py:86`](../backend/db.py#L86).

**Оценка:** ~1.5 дня.

---

## D. ChannelSplit — сплит flow на параллельные каналы

**Шаг сценария:** 11 («сделай сплит на два канала: Push и SMS»).

**Проблема.** Текущий `flow_builder.assemble_flow` строит линейную цепочку через `nextActivityId`. Параллельных веток нет, RefinerAgent умеет только append/remove одной активности. AdTarget поддерживает разветвление: одна активность может ссылаться на несколько `nextActivityId` (или используется `OrJoinActivity` для слияния).

**Решение:**

### Минимальная схема сплита

```
... → TargetGroupActivity (id=tg1) ──┬── PushCommunicationActivity (id=push1)
                                     │
                                     └── SmsCommunicationActivity   (id=sms1)
```

Реализуется как массив `nextActivityIds: ["push1", "sms1"]` на `TargetGroupActivity` (вместо одиночного `nextActivityId`). После сплита можно либо завершить ветки (никакого OrJoin), либо слить через `OrJoinActivity` если дальше идут общие шаги.

### Новая операция в LLM-modify

В [`agents/builder/modify_llm.py`](../backend/agents/builder/modify_llm.py) расширить список операций. Сейчас поддерживается `add_activity` / `remove_activity` / `replace_text`. Добавить:

```python
{
  "op": "split_channels",
  "anchor_activity_id": "tg1",       # после какой ноды делать сплит
  "branches": [
    {"channel": "push", "content_type": "PushContent", "text": "<заберём из offer_choice>"},
    {"channel": "sms",  "content_type": "SmsContent",  "text": "<заберём из offer_choice>"}
  ],
  "merge": false                     # true → добавить OrJoinActivity после
}
```

### Низкоуровневая реализация

В `tools/flow_builder.py`:
- Новая функция `split_after(flow, anchor_id, branches, merge=False)`:
  1. Находит anchor activity.
  2. Создаёт по `make_push_communication_activity` / `make_pull_communication_activity` для каждой branch.
  3. Заменяет `anchor.nextActivityId: x` на `anchor.nextActivityIds: [new_id_1, new_id_2]` (схема расширяется).
  4. Если `merge=True` — создаёт `OrJoinActivity` и проставляет все ветки на неё.
  5. Возвращает новый flow + applied (для trace).

### LLM-планировщик

В `plan_modifications_with_llm` system prompt:
- Добавить `split_channels` в список разрешённых операций с описанием.
- Few-shot: «сделай сплит на push и sms» → `{"operations": [{"op": "split_channels", ...}]}`.

### Применение

`apply_modifications` диспатчит на `split_after` при `op == "split_channels"`. Если в сессии есть `offer_choice` или несколько вариантов — подкладывает соответствующие тексты в каждую branch (а не заставляет LLM их выдумывать).

### Frontend (если надо)

Текущий UI рисует flow как линейный список. Для отображения сплита нужно:
- Либо показать в trace «Сплит: push + sms» текстом + рендерить две CommunicationActivity подряд с тегом «branch: push» / «branch: sms».
- Либо адаптировать FlowEditor для древовидного отображения. **Из коробки прототипа сойдёт текстовый вариант.**

### Acceptance

- Пользователь пишет «сделай сплит на push и sms» → в trace видно `op=split_channels, branches=2`.
- В `draft_flow.activities` появляется по одной PushCommunicationActivity и SmsCommunicationActivity, оба с `previousActivityId=<anchor>`.
- Если выбран оффер ранее — текст в обеих ветках = выбранный оффер, адаптированный под длину канала (через мини-LLM call или просто truncate).
- `save_campaign` → AdTarget mock принимает структуру со множественными `nextActivityIds` (mock_data адаптировать).

**Файлы:**
- `tools/flow_builder.py` (новая `split_after`).
- `agents/builder/modify_llm.py` (новая op + промпт).
- `tools/mock_data.py` (структура mock validate / create принимает сплит).
- Опционально: `frontend/src/components/FlowView.tsx` — улучшить отображение.

**Оценка:** ~2 дня (большая часть — отладка структуры flow и AdTarget mock).

---

## 3. Сводный план поставки

| Этап | Срок | Содержимое | Demo-эффект |
|---|---|---|---|
| **Sprint 1** | 1 день | A (TargetGroupAssignment) + B (ProductCatalogPicker) | Сценарий до шага 7 работает чисто; нет «магического id=1». |
| **Sprint 2** | 1.5 дня | C (OfferAgent + select_offer) | Шаги 7–9 работают: пользователь видит варианты, выбирает, оффер в flow. |
| **Sprint 3** | 2 дня | D (ChannelSplit) | Шаги 10–12 работают, scenario passes end-to-end. |
| Итого | **~4.5 дня** | 1 новый агент, 1 новый intent, 3 новых action, расширение flow-схемы, ~5 mock-наполнений | Полное прохождение сценария 9.2 на demo-стенде. |

## 4. Открытые вопросы

1. **AdTarget mock vs real:** для реальной интеграции (после прототипа) нужно подтвердить, что эндпоинты `POST /TargetGroups` (для TargetGroupAssignment) и многоветочный `nextActivityIds` (для ChannelSplit) реально поддерживаются API. В mock-режиме оба ограничения снимаются.

2. **Multi-step plans в supervisor.** OfferAgent inline в Builder — это естественный 2-step plan (`brief_check → generate_offers → resume_builder`). Сейчас supervisor строит план только из 1 шага (см. [BACKEND_AUDIT § 3.2 пункт 1](BACKEND_AUDIT.md)). Простой workaround — вернуть `needs_input` с quick-action на новый intent. Чистое решение — реализовать пункт «multi-step plans» из аудита.

3. **Cross-provider fallback.** OfferAgent делает дополнительный LLM-call на free-tier Gemini — увеличит дневной расход RPD. Без пункта #1 из BACKEND_AUDIT (auto-fallback gemini → groq → gigachat) на демо может прилететь 429. Желательно сделать до OfferAgent.

4. **Длина текста по каналам.** В ChannelSplit для каждой ветки нужен текст подходящей длины. Варианты:
   - Re-prompt LLM с указанием канала и лимита (дорого по токенам).
   - Truncate выбранный оффер с многоточием (грубо).
   - Хранить в `offer_variants` сразу 3 длины (короткая/средняя/длинная) — лучший вариант, добавляет 1 поле в схему.

5. **UI feedback.** Сценарий длинный (12 шагов). Без явных trace-меток «использован LLM» / «fallback» (см. BACKEND_AUDIT § 4.1 пункт 3) demo будет выглядеть «магически». Желательно сделать раньше OfferAgent.

## 5. Что менять в demo-сценарии docx по мере поставки

После каждого спринта обновлять `MVP Requirements _ Прототипы AI-фич.docx`, раздел 9.2:
- Убирать курсивные note «в прототипе не реализовано» для соответствующих шагов.
- В разделе 7 («Генерация контента») 7.3 «Опора на готовое» — отметить, что появилась реализация (OfferAgent).
- В разделе 8 («Автоматическая сегментация») 8.4 «Границы MVP» — снять ограничение «без автоматического назначения таргет-группой».

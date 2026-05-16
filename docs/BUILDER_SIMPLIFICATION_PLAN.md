# План упрощения Campaign Builder и AI-виджета

## Зачем менять

Текущий Builder одновременно пытается быть формой параметров, многошаговым чатом, редактором flow, историей сессий, панелью результата и shortcut-панелью demo-сценариев. Из-за этого пользовательский путь неочевиден: один и тот же запрос может трактоваться как сборка новой кампании или как доработка уже существующего flow. В примере с роуминг-кампанией длинный prompt из заполненных параметров дважды получил ответ «не нашёл текущий flow», потому что слой интентов распознал запрос как follow-up edit, хотя пользователь ожидал initial draft.

Цель плана — сделать Builder лаконичным «оркестратором кампании»: сначала собрать минимально достаточный brief, затем показать draft flow для проверки, затем разрешить точечные правки и запуск. UI должен явно отражать состояние работы агента, а backend — иметь детерминированный контракт между intent, state и ответом.

## Дизайн-принципы

1. **Один главный пользовательский job-to-be-done на экран.** В Builder основной job — получить проверяемый черновик кампании. История, debug-метаданные и редкие параметры не должны конкурировать с ним.
2. **State machine вместо эвристического диалога.** Агент должен знать, находится ли он в режиме `collect_brief`, `draft_ready`, `editing_flow`, `ready_to_launch`, `launched`; intent без нужного state должен не падать, а переводиться в ближайшее безопасное действие.
3. **Сначала deterministic planner, потом LLM.** Продукт, цель, аудитория, каналы и ограничения извлекаются структурно; LLM используется для copy, обоснований и выбора между допустимыми инструментами, но не для базового маршрута.
4. **Draft-before-create.** По умолчанию Builder должен возвращать draft flow на canvas без автосоздания кампании. Создание в AdTarget — отдельное подтверждённое действие.
5. **Минимальная поверхность управления.** В виджете оставить один input, CTA и компактный summary; advanced-параметры, history и diagnostics скрыть за вторым уровнем.
6. **Typed handoffs между агентами.** Audience Builder, Campaign Builder и Monitor обмениваются не длинным русскоязычным prompt, а структурированным `campaign_brief` и `flow_patch`.
7. **Safety by construction.** Согласие, opt-out, контактная политика и риск сегмента становятся обязательными checklist-пунктами перед launch, а не текстом в длинном описании аудитории.

## Целевая структура виджета

### 1. Header

- Название текущего режима: `Builder · Draft` / `Builder · Review` / `Builder · Launch`.
- Один статусный chip: `Brief incomplete`, `Draft ready`, `Needs review`, `Created`, `Running`.
- Короткий campaign id только после создания.

### 2. Brief card вместо большой формы

Показать 5 строк, каждая редактируется inline:

| Поле | Обязательность | Поведение |
| --- | --- | --- |
| Цель | обязательно | короткий one-liner |
| Продукт / оффер | обязательно | с подсказкой из каталога |
| Аудитория | обязательно | ссылка на selected segment или manual text |
| Каналы | обязательно | chips `SMS`, `Push`, `Email`; если пусто — предложить default |
| Ограничения | опционально, но видимо | consent, opt-out, contact policy, tone |

Длинное описание selected segment сворачивать в одну строку: `Низкий ARPU · путешествующие · opt-out excluded`. Полный критерий доступен по раскрытию.

### 3. Single composer

Composer должен иметь один placeholder: `Что изменить или собрать?`. Над ним — один primary CTA:

- если brief неполный: `Заполнить недостающее`;
- если brief полный и draft отсутствует: `Собрать draft flow`;
- если draft есть: `Доработать flow`;
- если draft валиден: `Создать кампанию`.

Demo presets переместить в меню `Examples`, чтобы они не выглядели как отдельный UI-паттерн.

### 4. Result strip

После каждого ответа показывать только 3 метрики:

- `Flow`: нет / draft / valid / invalid;
- `Activities`: количество и список типов;
- `Checks`: validation errors + safety warnings.

Debug-поля вроде `preference_patch` и `draft_flow: yes` убрать из основного UI.

### 5. Canvas as source of truth

FlowCanvas должен показывать не только граф, но и selected node summary. Все follow-up команды должны применяться к текущему `draft_flow_id`/`draft_flow_version`, а не к последнему тексту в чате.

## Целевой backend-контракт

### Новые DTO

```ts
type BuilderMode = "collect_brief" | "build_draft" | "edit_draft" | "create_campaign" | "launch";

type CampaignBrief = {
  goal: string | null;
  product: string | null;
  audience: {
    source: "selected_segment" | "target_group" | "manual" | null;
    label: string | null;
    target_group_id?: number | null;
    criteria?: Record<string, unknown>;
    exclusions?: string[];
    risks?: string[];
  };
  channels: Array<"SMS" | "PUSH" | "EMAIL">;
  content_constraints: string[];
  offer_recommendations: string[];
};

type BuilderResponse = {
  mode: BuilderMode;
  brief: CampaignBrief;
  next_action: "ask_missing" | "show_draft" | "review" | "create" | "launch";
  draft_flow: CampaignFlow | null;
  safety_checks: SafetyCheck[];
  validation_errors: ValidationError[];
  message: string;
};
```

### Intent routing

1. `has_draft_flow = false` + пользователь говорит «собери flow», «создай черновик», «кампания по ...» → всегда `build_draft`, даже если есть слова `добавь`, `flow`, `активность`.
2. `has_draft_flow = false` + пользователь говорит «добавь активность» → не ошибка; ответ: `Сначала соберу базовый draft, затем добавлю активность. Подтвердите каналы/цель.`
3. `has_draft_flow = true` + пользователь говорит «добавь / удали / после / перед» → `edit_draft`.
4. `has_draft_flow = true` + пользователь говорит «создай / сохрани в AdTarget» → `create_campaign`.
5. `campaign_id != null` + пользователь говорит «запусти» → `launch`.

### Пример исправленного поведения

Для запроса из примера Builder должен вернуть:

```text
Draft flow готов к проверке.

Brief:
- Цель: подключение роуминг-пакета перед поездкой
- Продукт: Travel Roaming
- Аудитория: recommendation-only segment, Target Group не создана
- Каналы: SMS, Push
- Ограничения: проверить consent, opt-out, contact policy

Flow:
Start → AudienceFilter → ConsentCheck → Push/SMS → Wait → Response/ActivationCheck

Нужно проверить: нет привязанной Target Group; требуется подтверждение контактной политики.
```

Важно: отсутствие текущего flow не должно блокировать initial draft.

## Роли агентов в мультиагентной системе

| Агент | Зона ответственности | Не делает |
| --- | --- | --- |
| Audience Agent | сегмент, criteria, reach, риски, target group match | не строит flow |
| Campaign Builder Orchestrator | state machine, brief completeness, tool routing | не генерирует длинный UI-текст |
| Flow Composer | строит canonical flow JSON из typed brief | не создаёт кампанию без подтверждения |
| Compliance/Safety Checker | consent, opt-out, contact policy, fatigue, frequency | не меняет creative |
| Content Agent | короткие варианты copy под каналы | не выбирает аудиторию |
| Campaign Monitor | post-launch метрики и рекомендации | не редактирует draft напрямую; отдаёт patch suggestion |

Оркестратор должен принимать typed outputs от специализированных агентов и возвращать пользователю один лаконичный ответ: что собрано, что требует проверки, какой следующий шаг.

## Roadmap внедрения

### Фаза 0 — стабилизация текущего UX

- Переименовать кнопку `Собрать flow` в `Собрать draft flow`.
- Скрыть history и параметры в collapsed secondary section по умолчанию.
- Убрать debug-метрики `preference_patch` и `draft_flow` из result panel.
- В случае отсутствия `session_flow_json` не отвечать «не нашёл текущий flow» на initial-build запросы; переводить запрос в `build_draft`.
- Отключить автосоздание кампании после построения flow; создание вынести в отдельную кнопку `Создать в AdTarget`.

### Фаза 1 — typed brief

- Заменить `BuilderPreferences` на `CampaignBrief` с нормализованными полями.
- Передавать selected segment как объект, а не как длинную строку в prompt.
- Добавить серверную проверку completeness: `missing_fields`, `assumptions`, `safety_checks`.
- Сделать channels chips и default selection: если канал не указан, Builder предлагает `SMS + Push`, но помечает как assumption.

### Фаза 2 — deterministic flow composer

- Вынести построение базовых шаблонов в отдельный `Flow Composer` service с входом `CampaignBrief`.
- Использовать LLM только для выбора template variant и текста сообщений.
- Ввести `draft_flow_version`; все edit-команды применяются к конкретной версии.
- Добавить typed `FlowPatch` для follow-up правок.

### Фаза 3 — review-first launch

- Добавить review checklist: audience, consent, contact policy, offer, content, validation.
- Разделить статусы `draft_ready`, `created_in_adtarget`, `running`.
- Monitor открывать только после create/launch, но pre-launch рекомендации показывать прямо в Builder.

### Фаза 4 — cleanup legacy UI

- Удалить отдельный legacy `CampaignBuilder` screen или привести его к тем же компонентам, что и floating widget.
- Убрать конкурирующие suggestions и demo playbook из основного потока.
- Оставить один источник сохранения: backend session + local optimistic cache.

## Acceptance criteria

1. По запросу «Собери flow ...» без текущего flow Builder возвращает draft, а не ошибку about missing flow.
2. Пользователь может собрать draft из selected segment за один клик без ручного копирования длинного prompt.
3. В основном виджете одновременно видны только brief summary, chat/composer, result strip; history и advanced параметры скрыты.
4. Создание кампании в AdTarget происходит только после явного действия пользователя.
5. Любой launch требует зелёного или acknowledged safety checklist.
6. Follow-up «добавь RealTimeCheck после SMS» меняет существующий draft и увеличивает `draft_flow_version`.
7. Ответ агента не содержит raw tool output, debug-поля и противоречивые статусы.

## Метрики успеха

- Time-to-draft: меньше 30 секунд и не больше 2 пользовательских действий при заполненном selected segment.
- Ошибки intent routing: меньше 2% запросов в demo-сценариях.
- Доля ответов с `status=error` на initial-build запросах: 0%.
- Среднее количество видимых controls в Builder: не больше 7.
- Доля кампаний, созданных без review checklist: 0%.

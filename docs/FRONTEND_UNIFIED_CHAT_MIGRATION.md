# Frontend migration guide: Unified Chat rollout

## 1) Новый feature flag

В frontend добавлен флаг `unified_chat_enabled` (env: `VITE_UNIFIED_CHAT_ENABLED`).

Сопутствующие rollout-переменные:

- `VITE_UNIFIED_CHAT_ROLLOUT_ENVS` — список окружений через запятую (`dev,stage,prod`), где можно включать новый экран.
- `VITE_UNIFIED_CHAT_ROLLOUT_USERS` — список пользователей (lowercase, через запятую), для частичного включения.
- `VITE_UNIFIED_CHAT_DEFAULT_NAV` — переключатель default navigation на unified chat.

## 2) Этап 1: ограниченный rollout

Рекомендуемый старт:

- `VITE_UNIFIED_CHAT_ENABLED=true`
- `VITE_UNIFIED_CHAT_DEFAULT_NAV=false`
- Ограничение по env/user через `VITE_UNIFIED_CHAT_ROLLOUT_ENVS` и/или `VITE_UNIFIED_CHAT_ROLLOUT_USERS`.

Пользователь для rollout определяется так:

1. query-param `?user=<login>`
2. fallback: `localStorage['cvm.rollout.user']`

Legacy режим всегда можно принудительно включить:

- `?legacy=1`
- путь `/legacy...`

## 3) Fallback сохраняется

Старые экраны **builder / monitor / segments** остаются в составе legacy-виджета и продолжают быть доступными как fallback, пока не завершён rollout unified chat.

## 4) Deprecated компоненты (после миграции на unified chat)

Ниже компоненты legacy-навигации, которые считаются deprecated для нового UX и не должны использоваться для новых фич после полного переключения:

- `frontend/src/components/FloatingWidget.tsx`
- `frontend/src/components/CampaignBuilderChat.tsx`
- `frontend/src/components/MonitoringPanel.tsx`
- `frontend/src/components/SegmentPanel.tsx`

Дополнительно: сценарии e2e, которые покрывают legacy flow (например `frontend/e2e/builder-segments.spec.ts`), остаются до завершения переходного периода.

## 5) Этап 2: переключение default navigation

После стабилизации unified chat:

- установить `VITE_UNIFIED_CHAT_DEFAULT_NAV=true`
- оставить fallback доступным через `?legacy=1` на короткий период
- затем удалить legacy-навигацию и deprecated-компоненты отдельным cleanup PR

## 6) Рекомендуемая последовательность реализации (updated 2026-05-19)

1. **Data-layer и типы**
   - `chatApi` + модели payload'ов.
   - Единый `error handling` (retry/timeout/user-facing errors/telemetry).
   - Покрытие unit-тестами transport и normalization.

2. **Chat Workspace shell**
   - Поднять layout нового рабочего пространства: `Chat Workspace` + sidebar.
   - Включить маршруты `/chat/:sessionId` (+ sane redirect если `sessionId` не найден).
   - Сохранить legacy fallback по feature flag.

3. **Unified message thread + trace rendering**
   - Перевести рендер диалога на единый thread.
   - Добавить визуализацию trace/steps/tool events с деградацией при неполных данных.

4. **Action cards для сохранения артефактов**
   - Карточки действий на уровне assistant response.
   - UX для `save artifact` + optimistic state + обработка ошибок.

5. **Оптимизация, rollout и тестирование**
   - Оптимизация рендеринга (virtualization/memoization по необходимости).
   - Пошаговый rollout через `unified_chat_enabled` и rollout env/user gates.
   - Полный набор e2e + regression тестов (новый и legacy пути).

## 7) Final Cutover Gate

Переход в fully unified режим (без rollback на legacy по умолчанию) разрешён только при выполнении **всех** пунктов:

1. **Functional parity подтверждён**
   - Маршрутизация в новый chat workspace работает для всех целевых сценариев.
   - Создание чата, отправка сообщений, отображение thread/trace и save action card работают без регрессий относительно обязательного legacy-функционала.

2. **E2E suite проходит**
   - Полный набор e2e для unified flow — зелёный.
   - Критические regression-сценарии (включая переходные legacy checks до удаления fallback) — зелёные.

3. **Есть проверенный rollback-план**
   - Описан и протестирован быстрый возврат через env-конфиг (`VITE_UNIFIED_CHAT_DEFAULT_NAV=false`, при необходимости `VITE_UNIFIED_CHAT_ENABLED=false`).
   - Зафиксированы ответственные и SLA на rollback.

4. **Мониторинг после релиза подготовлен**
   - Настроены алерты по фронтенд-ошибкам, e2e smoke и ключевым продуктовым событиям (chat created, message sent, save action card success/fail).
   - Назначено окно усиленного наблюдения после cutover (минимум 24 часа).

## 8) Production env после final cutover

После прохождения Final Cutover Gate прод-конфигурация должна быть зафиксирована в следующем виде:

- `VITE_UNIFIED_CHAT_ENABLED=true`
- `VITE_UNIFIED_CHAT_DEFAULT_NAV=true`

Env-переменные, которые должны быть удалены из prod-конфигурации как переходные:

- `VITE_UNIFIED_CHAT_ROLLOUT_ENVS`
- `VITE_UNIFIED_CHAT_ROLLOUT_USERS`

Примечание: поддержка `?legacy=1` допускается только как временный rollback/fallback механизм до cleanup-даты (см. раздел ниже).

## 9) Post-release verification checklist

Сразу после релиза (и повторно после периода прогрева) выполнить smoke-проверку:

1. **Routing check**
   - Переход на основной маршрут чата открывает unified workspace по умолчанию.
   - URL вида `/chat/:sessionId` корректно открывает существующую сессию.

2. **Create chat**
   - Создание нового чата успешно.
   - Новый чат появляется в списке/side bar и открывается без ручного refresh.

3. **Send message**
   - Сообщение пользователя отправляется и отображается в thread.
   - Ответ ассистента приходит, отображается без визуальных артефактов/дубликатов.

4. **Save action card**
   - Действие сохранения из action card завершается успехом.
   - В случае искусственной ошибки корректно показывается error state и доступен retry.

## 10) Критерии удаления fallback-кода и дата удаления

Удаление legacy fallback (`?legacy=1`, legacy routes и deprecated components) выполняется только если одновременно выполнены условия:

- Не менее 14 календарных дней после final cutover без критических инцидентов P0/P1 по unified chat.
- 100% прод-трафика идёт через unified navigation (`VITE_UNIFIED_CHAT_DEFAULT_NAV=true`) без ручных override для целевых пользователей.
- Post-release verification и ежедневные smoke/e2e проверки стабильны весь период наблюдения.

**Плановая дата удаления fallback-кода: 2026-06-15** (отдельным cleanup PR с удалением legacy env/маршрутов/компонентов и обновлением e2e набора).

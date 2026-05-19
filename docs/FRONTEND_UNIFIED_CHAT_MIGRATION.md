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

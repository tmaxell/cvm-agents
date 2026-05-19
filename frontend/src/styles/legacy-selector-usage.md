# Legacy selector usage map

Аудит выполнен по `frontend/src`: сопоставление селекторов из бывшего реестра с `className` в JSX/TSX.

Статусы:
- `используется` — прямое совпадение селектора в JSX/TSX.
- `используется транзитивно` — прямого совпадения нет, но стиль может влиять через вложенные/комбинированные селекторы в живых классах.
- `не используется` — нет совпадений и нет живых узлов-носителей.

| Селектор | Статус | Комментарий |
|---|---|---|
| `.campaign-shell` | не используется | В `AdTargetMock.tsx` используется `adt-shell`. |
| `.topbar` | не используется | Заменено на `adt-topnav`. |
| `.brand-mark` | не используется | Заменено на `adt-topnav-logo`/SVG. |
| `.brand-name` | не используется | Заменено на `adt-logo-text`. |
| `.topbar-time` | не используется | Заменено на `adt-clock`. |
| `.workspace` | не используется | Заменено на `adt-body`. |
| `.left-panel` | не используется | Заменено на `adt-sidebar`. |
| `.right-panel` | не используется | Заменено на `adt-right-toolbar`. |
| `.search` | не используется | Заменено на `adt-search`. |
| `.group-title` | не используется | Заменено на `adt-sidebar-group-title`. |
| `.palette-row` | не используется | Заменено на `adt-sidebar-item`. |
| `.drag-dots` | не используется | Заменено на `adt-grip`. |
| `.canvas-area` | не используется | Заменено на `adt-canvas`. |
| `.node` | не используется | Заменено на `adt-node`. |
| `.node-error` | не используется | Заменено на `adt-node-warning`. |
| `.connector` | не используется | Заменено на SVG-коннекторы `adt-flow-svg`. |
| `.field` | не используется | Заменено на `adt-form-field`. |
| `.form-row` | не используется | Заменено на `adt-form-row`. |
| `.primary-action` | не используется | Заменено на `adt-btn-primary`. |
| `.secondary-action` | не используется | Заменено на `adt-btn-secondary`. |
| `.copilot-layer` | не используется | Старый floating chat удалён. |
| `.copilot-launcher` | не используется | Старый launcher удалён. |
| `.chat-window` | не используется | Новый layout: `chat-workspace-layout`. |
| `.chat-header` | не используется | Новый header: `chat-context-header`. |
| `.mode-switch` | не используется | В unified UX mode switch отсутствует. |
| `.suggestions` | не используется | Используется `suggestions-strip` в `ChatPanel`. |
| `.request-history` | не используется | История чатов через `chat-left-panel`. |
| `.messages` | не используется | Используется `chat-messages`/`message-feed`. |
| `.message` | используется | Применяется в `components/ChatPanel.tsx`. |
| `.message-mode` | не используется | Удалено с переходом на markdown-body/bubble. |
| `.loading` | используется | Индикатор loading в `components/ChatPanel.tsx`. |
| `.feedback` | не используется | Блок удалён. |
| `.composer` | используется | Используется в `components/ChatPanel.tsx`. |
| `.campaign-preview` | не используется | Удалено. |
| `.preview-metrics` | не используется | Удалено. |
| `.preview-flow` | не используется | Удалено. |
| `.preview-changes` | не используется | Удалено. |
| `.preview-validation` | не используется | Удалено. |
| `.clarifying-questions` | не используется | Удалено. |

Итог: legacy-файл больше не нужен; оставшиеся рабочие классы (`.message`, `.loading`, `.composer`) уже определены в модульных стилях (`chat-workspace.css`).

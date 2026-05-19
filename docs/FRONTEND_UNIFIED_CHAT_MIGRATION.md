# Frontend migration guide: Unified Chat rollout

**Дата ревизии:** 2026-05-19  
**Владелец документа:** Frontend Platform Team

## 1) Scope и цель

Этот документ разделяет:

- **Исторический план** — как migration/cutover планировались ранее.
- **Текущее состояние (as-is)** — что реально реализовано в **текущей ветке** и какие rollback-рычаги доступны сейчас.

---

## 2) Исторический план (архив)

Ниже — historical intent, который ранее использовался как план rollout:

- feature flag `unified_chat_enabled` (env: `VITE_UNIFIED_CHAT_ENABLED`);
- rollout-переменные `VITE_UNIFIED_CHAT_ROLLOUT_ENVS`, `VITE_UNIFIED_CHAT_ROLLOUT_USERS`;
- переключатель `VITE_UNIFIED_CHAT_DEFAULT_NAV`;
- частичный rollout по env/user;
- legacy override через `?legacy=1` и `/legacy`;
- staged cutover и последующий cleanup legacy-кода.

> Важно: этот раздел фиксирует **исторический план**, а не гарантию наличия этих механизмов в текущем коде.

---

## 3) Текущее состояние (as-is, текущая ветка)

Проверка по текущему дереву исходников (`frontend/src`, `frontend/e2e`) показывает, что упоминания rollout/legacy-механизмов отсутствуют в коде и встречаются только в этом документе.

### Статусы legacy-механизмов

| Механизм | Историческое назначение | Статус в текущей ветке | Комментарий |
|---|---|---|---|
| `?legacy=1` | Принудительный переход в legacy UI | **not implemented in current branch** | Поиск по `frontend/src`/`frontend/e2e` не нашёл обработку query-param. |
| `/legacy` route | Маршрут legacy-экрана как fallback | **not implemented in current branch** | Поиск по `frontend/src`/`frontend/e2e` не нашёл роут/redirect для `/legacy`. |
| Rollout env flags (`VITE_UNIFIED_CHAT_ROLLOUT_ENVS`) | Ограничение rollout по окружениям | **not implemented in current branch** | Нет чтения/использования env-переменной в runtime-коде. |
| Rollout user flags (`VITE_UNIFIED_CHAT_ROLLOUT_USERS`, `?user=`, `localStorage['cvm.rollout.user']`) | Ограничение rollout по пользователям | **not implemented in current branch** | Нет логики user-targeting в runtime-коде. |
| `VITE_UNIFIED_CHAT_ENABLED` / `VITE_UNIFIED_CHAT_DEFAULT_NAV` | Глобальный gate + default nav switch | **not implemented in current branch** | Нет использования в runtime-коде текущей ветки. |

> Примечание по статусам: в рамках текущей ревизии не найдено признаков состояния **removed** (т.е. удалённой ранее реализации с явными следами cleanup); механизмы квалифицированы как **not implemented in current branch**.

---

## 4) Rollback в текущем коде (реальные рычаги)

Ниже перечислены rollback-варианты именно для текущей ветки.

| Рычаг rollback | Статус | Что это значит practically |
|---|---|---|
| Runtime rollback через `?legacy=1` | **absent** | Нельзя переключить пользователя на legacy query-параметром, т.к. механизма нет в коде ветки. |
| Runtime rollback через `/legacy` route | **absent** | Нельзя сделать fallback на legacy route внутри текущего frontend runtime. |
| Runtime rollback через rollout env/user flags | **absent** | Нельзя оперативно откатывать/сужать rollout через эти флаги в текущем runtime-коде. |
| Runtime rollback через `VITE_UNIFIED_CHAT_ENABLED` / `VITE_UNIFIED_CHAT_DEFAULT_NAV` | **absent** | Нет подтверждённой реализации переключателей в коде ветки. |
| **Deploy rollback (предыдущий стабильный build/release)** | **active** | Единственный подтверждённый rollback-рычаг на текущий момент: откат деплоя на предыдущий релизный артефакт. |

### Вывод

На текущей ветке **оперативных in-app rollback механизмов не подтверждено**. Реально доступен только **инфраструктурный rollback через деплой**.

# cvm-agents

AI-ассистенты для платформы **AdTarget CVM**: RAG-копилот по документации, агент для сборки кампаний, мониторинг кампаний и web-прототип с плавающим виджетом поверх mock-интерфейса AdTarget.

## Текущее состояние проекта

Проект — рабочий MVP-прототип из двух приложений:

- **Backend**: FastAPI API на Python, LangChain/LangGraph-агенты, интеграция с AdTarget API, RAG по документации, SQL-хранилище истории Builder-сессий.
- **Frontend**: React + Vite + TypeScript UI. На фоне отображается mock-интерфейс AdTarget, поверх него — плавающий AI-виджет с вкладками Copilot, Campaign Builder и Monitoring.
- **Persistence**: по умолчанию локальная SQLite БД `backend/data/cvm_agents.sqlite3`; в Docker Compose используется PostgreSQL.
- **AdTarget режимы**: реальный API через Keycloak OAuth2 или принудительный/mock fallback через `ADTARGET_MOCK=true`.
- **LLM/Embeddings**: поддерживаются Anthropic Claude, GigaChat, Groq, Gemini и Ollama; embeddings — Anthropic/Voyage или локальные HuggingFace.

## Возможности

### F1 CVM Copilot

- Отвечает на вопросы по AdTarget CVM и документации проекта.
- Использует гибридный RAG: ChromaDB semantic retriever + BM25 keyword retriever.
- Подмешивает live-контекст текущей кампании: данные кампании, flow, статистику и ошибки валидации.
- Возвращает citations, которые frontend показывает как источники.

### F2 Campaign Builder

- LangGraph ReAct-агент, который собирает кампанию из бизнес-описания.
- Работает с диалоговой памятью и сохранёнными предпочтениями пользователя: продукт, цель, каналы, ЦГ, контент, рекомендации по офферам.
- Запрашивает справочники AdTarget: целевые группы, каналы, события, шаблоны офферов, типы/группы кампаний, продуктовый каталог.
- Генерирует `campaign flow` из поддерживаемых activity-блоков, валидирует его и создаёт/обновляет кампанию в AdTarget.
- Сохраняет историю сессий, сообщения, `campaign_id`, последний draft flow и runtime status в БД.
- Поддерживает старт и паузу кампании через backend endpoints.

### F3 Campaign Monitor

- Анализирует созданную кампанию и draft flow.
- Генерирует детерминированные mock-метрики по `campaign_id`, `refresh_seed` и структуре flow.
- Показывает доставки по каналам, open/click/conversion rates, сравнение тестовой и контрольной групп.
- Возвращает AI-рекомендации по структуре кампании, запуску и действиям по похожим кампаниям.

### Frontend UI

- Статичный mock AdTarget с canvas-представлением campaign flow.
- Плавающий AI-виджет с вкладками:
  - **CVM Copilot** — чат по документации и контексту кампании;
  - **Campaign Builder** — создание кампаний, история Builder-сессий, черновик flow;
  - **Monitoring** — метрики, score и рекомендации по текущей кампании.
- Переключение RU/EN в виджете.
- Normal/large размер панели.
- Кнопки запуска и паузы кампании в mock-интерфейсе после создания кампании.
- План упрощения перегруженного Campaign Builder и виджета: [`docs/BUILDER_SIMPLIFICATION_PLAN.md`](docs/BUILDER_SIMPLIFICATION_PLAN.md).

## Архитектура

```text
cvm-agents/
├── backend/
│   ├── app.py                     # FastAPI routes: health, copilot, builder, monitor, sessions, campaign actions
│   ├── db.py                      # Async SQLAlchemy engine/repository, SQLite fallback, PostgreSQL support
│   ├── models.py                  # sessions, messages, campaign_states
│   ├── schemas.py                 # Pydantic request/response models and shared DTOs
│   ├── llm.py                     # LLM provider factory: Anthropic, GigaChat, Groq, Gemini, Ollama
│   ├── gigachat_tools.py          # GigaChat tool-call compatibility adapter
│   ├── agents/
│   │   ├── qa_copilot.py          # F1 RAG Copilot
│   │   ├── campaign_builder.py    # F2 LangGraph Campaign Builder
│   │   └── campaign_monitor.py    # F3 Campaign Monitor
│   ├── tools/
│   │   ├── adtarget.py            # Single entry point to AdTarget API + mock fallback
│   │   ├── flow_builder.py        # Helpers for AdTarget flow/activity JSON
│   │   └── mock_data.py           # Mock dictionaries and runtime action responses
│   ├── rag/
│   │   ├── embeddings.py          # Anthropic/Voyage or local HuggingFace embeddings
│   │   ├── indexer.py             # Builds ChromaDB index from docs
│   │   └── retriever.py           # Hybrid retriever for Copilot
│   └── tests/                     # Backend unit tests for Builder and flow logic
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # Root state: flow, campaign status, runtime actions
│   │   ├── components/            # Mock AdTarget, widget, chats, monitoring, flow canvas
│   │   ├── hooks/useChat.ts       # Chat helper for Copilot/Builder requests
│   │   └── types/api.ts           # Shared frontend API types
│   └── package.json               # Vite/React scripts
├── docs/
│   ├── MVP_INITIATIVES_REQUIREMENTS.md
│   └── PLATFORM_UI.md
└── deploy/
    ├── Dockerfile.backend
    ├── Dockerfile.frontend
    └── docker-compose.yml         # PostgreSQL + backend + frontend
```

## Backend API

| Method | Path | Назначение |
|---|---|---|
| `GET` | `/api/health` | Health check backend-а |
| `POST` | `/api/copilot` | F1 Copilot: вопрос + контекст → ответ + citations |
| `POST` | `/api/builder` | F2 Builder: goal/session state → ответ, campaign_id, draft_flow |
| `POST` | `/api/monitor` | F3 Monitor: campaign_id + draft_flow_json → метрики и рекомендации |
| `GET` | `/api/sessions` | Список Builder-сессий |
| `GET` | `/api/sessions/{session_id}` | Полная история Builder-сессии |
| `POST` | `/api/sessions` | Создать или продолжить Builder-сессию |
| `POST` | `/api/sessions/{session_id}/messages` | Добавить сообщение в сессию без запуска агента |
| `POST` | `/api/campaigns/{campaign_id}/start` | Запустить кампанию в AdTarget |
| `POST` | `/api/campaigns/{campaign_id}/pause` | Поставить кампанию на паузу/остановить |

Backend также отдаёт документацию для UI-источников, если папки существуют:

- `docs/` → `/source-docs/`;
- соседний `../cvmCopilot/docs/` → `/cvmCopilot-docs/`.

## Поддерживаемые activity-типы Campaign Builder

Builder генерирует flow через `backend/tools/flow_builder.py`. Сейчас поддерживаются:

- `TargetGroupActivity`
- `PushCommunicationActivity`
- `PullCommunicationActivity`
- `EventActivity`
- `WaitActivity`
- `BusinessTransactionActivity`
- `RealTimeCheckActivity`
- `ResponseActivity`
- `InteractiveResponseActivity`
- `OrJoinActivity`

## Конфигурация

Создайте `backend/.env` из примера:

```bash
cd backend
cp .env.example .env
```

Основные параметры:

```env
# AdTarget
ADTARGET_API_BASE=http://192.168.15.102:4001
ADTARGET_TOKEN_URL=http://192.168.15.102:8117/auth/realms/mmp/protocol/openid-connect/token
ADTARGET_CLIENT_ID=adtarget
ADTARGET_USERNAME=your_login@company.com
ADTARGET_PASSWORD=your_password
ADTARGET_MOCK=true

# LLM provider: auto | anthropic | gigachat | groq | gemini | ollama
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MAX_TOKENS=2048

# Anthropic / Claude, если выбран anthropic или нужны Voyage embeddings
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5

# Embeddings: auto | anthropic | local
EMBEDDING_PROVIDER=local

# Persistence; если не задано — локальный SQLite файл backend/data/cvm_agents.sqlite3
DATABASE_URL=postgresql+asyncpg://cvm_agents:cvm_agents@localhost:5432/cvm_agents
```

Примечания:

- `LLM_PROVIDER` без явного значения выбирается автоматически по доступным ключам.
- `ADTARGET_MOCK=true` позволяет запускать прототип без VPN и живого стенда AdTarget.
- Для Groq можно уменьшать контекст Builder-а переменными `BUILDER_MESSAGE_TOKEN_BUDGET`, `BUILDER_MAX_TARGET_GROUPS`, `BUILDER_MAX_CHANNELS`, `BUILDER_MAX_EVENTS`, `BUILDER_MAX_OFFERS`.
- Для локальных embeddings первый запуск может скачать HuggingFace/SentenceTransformers модель.

## Локальный запуск

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env под нужный LLM и режим AdTarget
```

Построить RAG-индекс для Copilot:

```bash
python -m rag.indexer
```

Запустить API:

```bash
uvicorn app:app --reload --port 8000
```

Проверка:

```bash
curl http://localhost:8000/api/health
```

Заполнить demo-кампании (20–50 шт.) и диагностику `campaign_health`:

```bash
cd backend
python -m scripts.seed_demo_campaigns
```

Автосид при старте backend в dev-режиме:

```bash
SEED_DEMO_CAMPAIGNS_ON_STARTUP=true uvicorn app:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Unified chat rollout flags (frontend)

```env
VITE_UNIFIED_CHAT_ENABLED=false
VITE_UNIFIED_CHAT_ROLLOUT_ENVS=dev,stage
VITE_UNIFIED_CHAT_ROLLOUT_USERS=user1,user2
VITE_UNIFIED_CHAT_DEFAULT_NAV=false
```

Migration guide for frontend team: `docs/FRONTEND_UNIFIED_CHAT_MIGRATION.md`.

UI будет доступен на <http://localhost:5173>. В dev-режиме Vite проксирует `/api/*` на backend; при необходимости задайте:

```bash
VITE_API_PROXY=http://localhost:8000 npm run dev
```

Если frontend собирается отдельно и должен ходить на другой backend без Vite proxy, задайте `VITE_API_BASE`.

#### E2E smoke-тесты

Перед первым запуском Playwright установите Chromium:

```bash
npx playwright install chromium
```

Запуск e2e-тестов frontend:

```bash
npm run test:e2e
```

## Docker Compose

Production-like demo-стек поднимает PostgreSQL, backend и frontend:

```bash
cd deploy
docker compose up --build
```

Compose использует:

- `postgres` с volume `postgres_data`;
- `backend` с `DATABASE_URL=postgresql+asyncpg://...` и `ADTARGET_MOCK=${ADTARGET_MOCK:-true}`;
- `frontend` с `VITE_API_PROXY=http://backend:8000`.

Очистить историю demo-стека:

```bash
cd deploy
docker compose down -v
```

## Тесты и проверки

Backend unit tests:

```bash
cd backend
pytest
```

Frontend production build:

```bash
cd frontend
npm run build
```

## Документация

- [MVP initiatives requirements](docs/MVP_INITIATIVES_REQUIREMENTS.md) — требования к MVP-инициативам CVM Agents / AdTarget.
- [Platform UI notes](docs/PLATFORM_UI.md) — заметки по UI платформы.

## Ключевые принципы разработки

- Все вызовы AdTarget API проходят через `backend/tools/adtarget.py`.
- JSON flow собирается через helpers в `backend/tools/flow_builder.py`, а не вручную в UI.
- История Builder-а и runtime-состояние кампании сохраняются на backend-е, frontend держит только текущий UI state.
- Copilot должен ссылаться на RAG-источники и не выдумывать ответы, если документации/контекста недостаточно.
- Mock-режим должен оставаться пригодным для demo без VPN и внешнего AdTarget стенда.

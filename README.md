# CVM Agents

Агентская AI-платформа для **AdTarget CVM** — серверный сервис из специализированных LLM-агентов, которые помогают CVM-команде подбирать сегменты, проектировать и дорабатывать кампании, генерировать креативы и анализировать портфель прямо поверх AdTarget. В состав репозитория также входит web-прототип: плавающий AI-виджет поверх mock-интерфейса AdTarget.

> **Статус:** рабочий прототип (MVP). Интеграция с AdTarget по умолчанию работает в mock-режиме; целевые требования к промышленной реализации зафиксированы в БФТ (локальная папка `docs/`, см. ниже).

---

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Агенты](#агенты)
- [API](#api)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Docker](#docker)
- [Тесты](#тесты)
- [Структура репозитория](#структура-репозитория)

---

## Возможности

- **Единый чат-интерфейс.** Все запросы идут в `POST /api/chat`; оркестратор классифицирует намерение и маршрутизирует к нужному агенту.
- **Проектирование кампаний.** Сборка валидного flow AdTarget из бизнес-описания: бриф → план → детерминированный сборщик → валидация.
- **Доработка кампаний.** Модификация черновика или существующей кампании по текстовой команде (добавить шаг, сменить канал, разветвить на каналы и т. д.).
- **Сегментация.** Гипотезы аудитории под продукт/цель, сопоставление с существующими целевыми группами, назначение таргет-группой.
- **Генерация креативов.** Несколько вариантов текста коммуникации под продукт, канал и сегмент.
- **Мониторинг портфеля.** Анализ кампаний, ранжирование проблемных, диагноз и план действий.
- **Copilot по документации.** Ответы по документации AdTarget через гибридный RAG (ChromaDB + BM25) со ссылками на источники.
- **Трассировка и артефакты.** Каждый запрос пишет trace-события; результаты агентов сохраняются как артефакты в сессии.

---

## Архитектура

```
                ┌─────────────────────────────┐
   HTTP ─────►  │   FastAPI (backend/app.py)   │
                │        POST /api/chat        │
                └──────────────┬──────────────┘
                               │
                     supervisor.handle()
                ┌──────────────┴──────────────┐
                │  action? → dispatch          │
                │  message? → classify_intent  │
                └──────────────┬──────────────┘
                               │  registry.agent_for_intent
        ┌───────────┬──────────┼───────────┬───────────┐
        ▼           ▼          ▼           ▼           ▼
     docs       builder    segments     offer      attention ...
        │           │          │           │           │
        └───────────┴────┬─────┴───────────┴───────────┘
                         ▼
        ┌────────────┬───────────┬──────────────┐
        ▼            ▼           ▼              ▼
   tools/adtarget  llm.py      rag/         db.py (sessions,
   (REST + mock)  (LLM-провайдеры) (Chroma+BM25)  artifacts, trace)
```

- **Единая точка интеграции с AdTarget** — `backend/tools/adtarget.py` (REST + авто-fallback на mock).
- **Единая точка вызова LLM** — `backend/llm.py` (несколько провайдеров).
- **Сборка flow** — `backend/tools/flow_builder.py` (детерминированный сборщик из плана агента).
- **Хранилище** — SQLAlchemy async; локально SQLite, в Docker — PostgreSQL.

---

## Агенты

Все агенты регистрируются в `backend/agents/registry.py` и выбираются оркестратором по интенту.

| Агент | Интент | Назначение |
|---|---|---|
| `docs` | `documentation_qa` | RAG-ответы по документации AdTarget со ссылками на источники |
| `builder` | `build_campaign` | Сборка черновика flow из бизнес-описания |
| `refiner` | `refine_campaign` | Доработка черновика/кампании, рекомендации |
| `segments` | `suggest_segments` | Гипотезы сегментов, сопоставление с целевыми группами |
| `offer` | `generate_offers` | Варианты текста креатива под продукт/канал/аудиторию |
| `attention` | `campaign_attention` | Портфельный анализ кампаний и план действий |
| `runtime` | `runtime_action` | Сохранение, запуск, пауза кампании; создание ЦГ |

---

## API

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/api/health` | Проверка работоспособности |
| `POST` | `/api/chat` | Единая точка: сообщение или команда (action) + контекст → ответ агента |
| `GET` | `/api/sessions` | Список сессий |
| `POST` | `/api/sessions` | Создать/продолжить сессию |
| `GET` | `/api/sessions/{id}` | Сессия: сообщения, артефакты |
| `GET` | `/api/sessions/{id}/messages` | История сообщений сессии |
| `POST` | `/api/campaigns/{id}/start` | Запустить кампанию |
| `POST` | `/api/campaigns/{id}/pause` | Поставить кампанию на паузу |

---

## Быстрый старт

**Требования:** Python 3.13, Node.js 18+.

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # отредактируйте под нужный LLM и режим AdTarget

python -m rag.indexer         # построить RAG-индекс для Copilot
USE_SQLITE_FALLBACK=true uvicorn app:app --reload --port 8000
```

Проверка: `curl http://localhost:8000/api/health`

> Без `USE_SQLITE_FALLBACK=true` backend ожидает PostgreSQL (см. [Конфигурация](#конфигурация) и [Docker](#docker)).

Демо-данные для мониторинга (30 кампаний с диагностикой):

```bash
USE_SQLITE_FALLBACK=true python -m scripts.seed_demo_campaigns
# или автосид при старте:
SEED_DEMO_CAMPAIGNS_ON_STARTUP=true USE_SQLITE_FALLBACK=true uvicorn app:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                   # UI на http://localhost:5173, /api/* проксируется на :8000
```

Если backend на другом адресе: `VITE_API_PROXY=http://localhost:8000 npm run dev`.

---

## Конфигурация

Создайте `backend/.env` из `backend/.env.example`. Ключевые параметры:

```env
# LLM-провайдер: auto | anthropic | gigachat | groq | gemini | ollama
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# Anthropic / Claude (если выбран anthropic)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5

# Эмбеддинги: auto | anthropic | local
EMBEDDING_PROVIDER=local

# AdTarget
ADTARGET_API_BASE=http://192.168.15.102:4001
ADTARGET_TOKEN_URL=http://192.168.15.102:8117/auth/realms/mmp/protocol/openid-connect/token
ADTARGET_CLIENT_ID=adtarget
ADTARGET_USERNAME=your_login@company.com
ADTARGET_PASSWORD=your_password
ADTARGET_MOCK=true            # true — работать без VPN/живого стенда AdTarget

# Хранилище: PostgreSQL (по умолчанию) либо локальный SQLite через USE_SQLITE_FALLBACK=true
DATABASE_URL=postgresql+asyncpg://cvm_agents:cvm_agents@localhost:5432/cvm_agents
```

- `LLM_PROVIDER` без значения выбирается автоматически по доступным ключам.
- `ADTARGET_MOCK=true` — все вызовы AdTarget идут через mock (`backend/tools/mock_data.py`).
- Для локальных эмбеддингов первый запуск скачает модель HuggingFace.

---

## Docker

Production-like стек (PostgreSQL + backend + frontend):

```bash
cd deploy
docker compose up --build
```

Очистить данные стенда:

```bash
cd deploy && docker compose down -v
```

---

## Тесты

```bash
# Backend
cd backend && pytest

# Frontend
cd frontend
npm run test:unit             # vitest
npm run test:e2e              # playwright (сначала: npx playwright install chromium)
npm run build                 # production-сборка
```

---

## Структура репозитория

```
cvm-agents/
├── backend/
│   ├── app.py                 # FastAPI: routes (health, chat, sessions, campaign actions)
│   ├── agents/
│   │   ├── supervisor.py      # оркестрация: action-dispatch + intent routing
│   │   ├── chat_orchestrator.py  # классификация интента (rules + LLM)
│   │   ├── registry.py        # реестр агентов
│   │   ├── agent_*.py         # агенты: docs, builder, refiner, segments, offer, attention, runtime
│   │   └── builder/           # бриф, план, шаблоны, модификации flow
│   ├── tools/
│   │   ├── adtarget.py        # единый клиент AdTarget API + mock fallback
│   │   ├── flow_builder.py    # сборка flow/activity JSON
│   │   └── mock_data.py       # mock-справочники и runtime-ответы
│   ├── rag/                   # индекс (Chroma) + гибридный ретривер
│   ├── llm.py                 # фабрика LLM-провайдеров
│   ├── db.py / models.py / schemas.py
│   ├── scripts/               # seed демо-данных
│   └── tests/
├── frontend/                  # React + Vite + TS: плавающий виджет поверх mock AdTarget
│   └── src/
│       ├── components/        # AdTargetMock, FloatingWidget, MainLayout, flow
│       ├── chat-workspace/    # стор чата
│       └── api/chatApi.ts
├── examples/                  # референсные JSON flow для шаблонов/тестов
├── deploy/                    # Dockerfile.backend, Dockerfile.frontend, docker-compose.yml
└── docs/                      # ⚠️ локальная папка (в репозиторий не входит, см. .gitignore)
```

> **`docs/` и `backend/chroma_db/` не версионируются.** `docs/` хранится только локально (БФТ, аудиты, описания платформы), а индекс RAG (`chroma_db/`) пересобирается командой `python -m rag.indexer`.

---

## Ключевые принципы

- Все вызовы AdTarget API — только через `backend/tools/adtarget.py`.
- JSON flow собирается через `backend/tools/flow_builder.py`, а не вручную.
- История и runtime-состояние хранятся на backend; frontend держит только текущий UI-state.
- Copilot ссылается на источники RAG и не выдумывает ответ при недостатке данных.
- Mock-режим остаётся пригодным для demo без VPN и живого стенда AdTarget.

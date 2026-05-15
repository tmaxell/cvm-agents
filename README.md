# cvm-agents

AI-агенты для платформы AdTarget CVM.

## Агенты

| Агент | Файл | Описание |
|-------|------|---------|
| **F1 CVM Copilot** | `backend/agents/qa_copilot.py` | RAG + LLM. Отвечает на вопросы по платформе, объясняет ошибки, даёт контекстные подсказки |
| **F2 Campaign Builder** | `backend/agents/campaign_builder.py` | LangGraph ReAct. Автономно собирает и создаёт кампанию из описания цели |

## Структура

```
cvm-agents/
├── backend/
│   ├── app.py                   # FastAPI: POST /api/copilot, /api/builder
│   ├── db.py                    # Async SQLAlchemy engine + repository
│   ├── models.py                # Таблицы sessions, messages, campaign_states
│   ├── schemas.py               # Pydantic модели + AgentContext
│   ├── agents/
│   │   ├── qa_copilot.py        # F1: RAG chain (LangChain LCEL)
│   │   └── campaign_builder.py  # F2: agentic loop (LangGraph)
│   ├── tools/
│   │   └── adtarget.py          # AdTargetClient — единственная точка входа в API
│   └── rag/
│       ├── indexer.py           # Строит ChromaDB индекс из docs/
│       └── retriever.py         # Возвращает LangChain ретривер
├── frontend/
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── ChatPanel.tsx    # Универсальная панель чата (F1)
│       │   └── CampaignBuilder.tsx  # Интерфейс Campaign Builder (F2)
│       ├── hooks/useChat.ts
│       └── types/api.ts
└── docs/                        # Копия документации из cvmCopilot/docs/
```

## Документация MVP

- [Требования к MVP инициатив CVM Agents / AdTarget](docs/MVP_INITIATIVES_REQUIREMENTS.md) — MVP-требования по 7 инициативам на базе текущего прототипа и клиентских заметок.

## Запуск

### Backend

```bash
cd backend
cp .env.example .env
# заполните ADTARGET_USERNAME, ADTARGET_PASSWORD, ANTHROPIC_API_KEY

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Первый раз — построить RAG-индекс
python -m rag.indexer

# Запустить сервер
uvicorn app:app --reload --port 8000
```

По умолчанию локальный backend работает в demo-режиме с SQLite: файл истории создаётся в
`backend/data/cvm_agents.sqlite3`. Для подключения внешней БД задайте `DATABASE_URL`, например
`postgresql+asyncpg://cvm_agents:cvm_agents@localhost:5432/cvm_agents`. Таблицы создаются при
старте приложения (`sessions`, `messages`, `campaign_states`); зависимость Alembic добавлена для
перехода на управляемые миграции.

### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

## AdTarget API

- **API base:** `http://192.168.15.102:4001`
- **Auth:** Keycloak OAuth2, `http://192.168.15.102:8117/auth/realms/mmp`
- Документация: `../cvmCopilot/docs/PLATFORM_API.md`

## Ключевые принципы

- **Все вызовы AdTarget** идут только через `tools/adtarget.py` (AdTargetClient)
- **F1** использует LangChain LCEL chain: retriever → prompt → LLM → parser
- **F2** использует LangGraph ReAct loop: agent ↔ tools (AdTarget API)
- **LLM:** Claude (claude-sonnet-4-5) через `langchain-anthropic`


### Docker Compose

Production-like стек использует PostgreSQL:

```bash
cd deploy
docker compose up --build
```

Compose поднимает сервис `postgres`, затем `backend` с `DATABASE_URL=postgresql+asyncpg://...`,
и `frontend`. История Campaign Builder хранится в PostgreSQL volume `postgres_data`:

- `sessions` — сессии диалогов, `campaign_id`, текущий runtime `status`;
- `messages` — входящие user messages и assistant responses с metadata;
- `campaign_states` — последний `campaign_id`, `draft_flow_json` и runtime status кампании.

Чтобы удалить историю demo-стека, остановите compose и удалите volume:

```bash
cd deploy
docker compose down -v
```

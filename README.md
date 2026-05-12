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

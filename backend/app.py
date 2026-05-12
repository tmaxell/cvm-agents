"""
cvm-agents — FastAPI backend

Endpoints:
  POST /api/copilot    — F1 CVM Copilot (RAG + LLM)
  POST /api/builder    — F2 Campaign Builder (LangGraph agentic loop)
  GET  /api/health     — health check
"""

# Загружаем .env до импортов агентов (они читают os.getenv при инициализации)
from dotenv import load_dotenv
load_dotenv()

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from schemas import CopilotRequest, CopilotResponse, BuilderRequest, BuilderResponse, MonitorRequest, MonitorResponse
from agents.qa_copilot import answer as copilot_answer
from agents.campaign_builder import run as builder_run
from agents.campaign_monitor import run as monitor_run

app = FastAPI(title="CVM Agents API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Отдаём документы для просмотра источников из UI
# Маунтим папки docs/ из обоих проектов под /docs/
_backend_root = Path(__file__).parent
_project_root = _backend_root.parent
for _docs_dir, _mount_name in [
    (_project_root / "docs", "source-docs"),                     # label prefix: docs/ → served at /source-docs/
    (_project_root.parent / "cvmCopilot" / "docs", "cvmCopilot-docs"),  # label prefix: cvmCopilot-docs/
]:
    if _docs_dir.exists():
        app.mount(f"/{_mount_name}", StaticFiles(directory=str(_docs_dir), follow_symlink=True), name=_mount_name)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/copilot", response_model=CopilotResponse)
async def copilot(request: CopilotRequest) -> CopilotResponse:
    """F1 CVM Copilot — отвечает на вопросы по платформе и текущей кампании."""
    try:
        return await copilot_answer(request)
    except Exception as e:
        _handle_llm_error(e)


@app.post("/api/builder", response_model=BuilderResponse)
async def builder(request: BuilderRequest) -> BuilderResponse:
    """F2 Campaign Builder — автономно создаёт кампанию из описания цели."""
    try:
        return await builder_run(request)
    except Exception as e:
        _handle_llm_error(e)


@app.post("/api/monitor", response_model=MonitorResponse)
async def monitor(request: MonitorRequest) -> MonitorResponse:
    """F3 Campaign Monitor — анализ кампании и рекомендации по улучшению."""
    try:
        return await monitor_run(request)
    except Exception as e:
        _handle_llm_error(e)


def _handle_llm_error(e: Exception) -> None:
    """Преобразует ошибки LLM в читаемые HTTP-ответы."""
    import os
    err_str = str(e)
    provider = os.getenv("LLM_PROVIDER", "gigachat")
    if "402" in err_str or "Payment Required" in err_str:
        raise HTTPException(
            status_code=503,
            detail=(
                f"LLM API недоступен (402 Payment Required). "
                f"Текущий провайдер: {provider.upper()}. "
                "Пополните баланс аккаунта или добавьте ANTHROPIC_API_KEY в .env "
                "и установите LLM_PROVIDER=anthropic."
            ),
        )
    if "401" in err_str or "Unauthorized" in err_str:
        raise HTTPException(
            status_code=503,
            detail=f"LLM API: ошибка авторизации (401). Проверьте ключ для провайдера {provider.upper()}.",
        )
    if "429" in err_str or "Too Many Requests" in err_str:
        raise HTTPException(
            status_code=429,
            detail="LLM API: превышен лимит запросов (429). Подождите несколько секунд и повторите.",
        )
    if (
        "413" in err_str
        or "Request too large" in err_str
        or "too large for model" in err_str.lower()
    ):
        raise HTTPException(
            status_code=413,
            detail=(
                "Запрос к LLM слишком большой для текущей модели или тарифа Groq (413). "
                "Очистите историю чата Builder, уменьшите BUILDER_MESSAGE_TOKEN_BUDGET / справочники "
                "(BUILDER_MAX_*), или задайте другую модель, например GROQ_MODEL=llama-3.3-70b-versatile."
            ),
        )
    raise HTTPException(status_code=500, detail=f"Ошибка LLM: {err_str[:300]}")

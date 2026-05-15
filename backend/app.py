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

import json
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from schemas import (
    CopilotRequest,
    CopilotResponse,
    BuilderRequest,
    BuilderResponse,
    MonitorRequest,
    MonitorResponse,
    Session,
    SessionCreate,
    SessionDetail,
    Message,
    MessageCreate,
    CampaignActionRequest,
    CampaignActionResponse,
)
from agents.qa_copilot import answer as copilot_answer
from agents.campaign_builder import run as builder_run
from agents.campaign_monitor import run as monitor_run
from db import DatabaseSessionStore, init_db
from tools import adtarget

app = FastAPI(title="CVM Agents API", version="0.1.0")
session_store = DatabaseSessionStore()

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


@app.on_event("startup")
async def startup() -> None:
    await init_db()


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


@app.get("/api/sessions", response_model=list[Session])
async def list_sessions() -> list[Session]:
    """Список backend-сессий Campaign Builder."""
    return await session_store.list_sessions()


@app.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    """Полная история одного диалога Campaign Builder."""
    session = await session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.post("/api/sessions", response_model=Session)
async def create_or_continue_session(request: SessionCreate) -> Session:
    """Создаёт новую или возвращает существующую Builder-сессию."""
    return await session_store.ensure_session(
        session_id=request.session_id,
        title=request.title or "Новый диалог Builder",
        campaign_id=request.campaign_id,
        status=request.status,
    )


@app.post("/api/sessions/{session_id}/messages", response_model=Message)
async def add_session_message(session_id: str, request: MessageCreate) -> Message:
    """Добавляет сообщение в Builder-сессию без запуска агента."""
    try:
        return await session_store.add_message(
            session_id=session_id,
            role=request.role,
            content=request.content,
            metadata=request.metadata,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/api/builder", response_model=BuilderResponse)
async def builder(request: BuilderRequest) -> BuilderResponse:
    """F2 Campaign Builder — автономно создаёт кампанию из описания цели."""
    session = await session_store.ensure_session(
        session_id=request.session_id,
        title=_make_session_title(request.goal),
        campaign_id=request.session_campaign_id,
    )
    stored_session = await session_store.get_session(session.id)
    stored_history = [
        {"role": message.role, "content": message.content}
        for message in (stored_session.messages if stored_session else [])
        if message.role in {"user", "assistant"}
    ]
    effective_request = request.model_copy(update={"session_id": session.id, "history": stored_history})

    await session_store.add_message(
        session_id=session.id,
        role="user",
        content=request.goal,
        metadata={
            "builder_preferences": request.builder_preferences,
            "campaign_id": request.session_campaign_id,
            "draft_flow_json": _parse_flow_json(request.session_flow_json),
            "status": "in_progress",
        },
    )

    try:
        response = await builder_run(effective_request)
    except Exception as e:
        await session_store.update_session(session.id, status="error")
        await session_store.upsert_campaign_state(
            session_id=session.id,
            campaign_id=request.session_campaign_id,
            draft_flow_json=_parse_flow_json(request.session_flow_json),
            runtime_status="error",
        )
        _handle_llm_error(e)

    response.session_id = session.id
    persisted_campaign_id = response.campaign_id or request.session_campaign_id or session.campaign_id
    persisted_flow_json = response.draft_flow or _parse_flow_json(request.session_flow_json)
    response.campaign_id = persisted_campaign_id
    response.draft_flow = persisted_flow_json
    await session_store.add_message(
        session_id=session.id,
        role="assistant",
        content=response.message,
        metadata={
            "campaign_id": persisted_campaign_id,
            "status": response.status,
            "builder_preferences": response.builder_preferences,
            "preference_patch": response.preference_patch,
            "draft_flow_json": persisted_flow_json,
            "validation_errors": response.validation_errors,
        },
    )
    await session_store.upsert_campaign_state(
        session_id=session.id,
        campaign_id=persisted_campaign_id,
        draft_flow_json=persisted_flow_json,
        runtime_status=response.status,
    )
    return response


@app.post("/api/campaigns/{campaign_id}/start", response_model=CampaignActionResponse)
async def start_campaign(
    campaign_id: int,
    request: CampaignActionRequest | None = Body(default=None),
) -> CampaignActionResponse:
    """Запускает кампанию в AdTarget и возвращает результат runtime-действия."""
    _validate_campaign_action_request(campaign_id, request)
    try:
        result = await adtarget.start_campaign(campaign_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AdTarget start failed: {str(e)[:300]}")
    _raise_for_failed_campaign_action(result, "start")
    return CampaignActionResponse(campaign_id=campaign_id, status="active", result=result)


@app.post("/api/campaigns/{campaign_id}/pause", response_model=CampaignActionResponse)
async def pause_campaign(
    campaign_id: int,
    request: CampaignActionRequest | None = Body(default=None),
) -> CampaignActionResponse:
    """Ставит кампанию на паузу/останавливает её в AdTarget."""
    _validate_campaign_action_request(campaign_id, request)
    try:
        result = await adtarget.pause_campaign(campaign_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AdTarget pause failed: {str(e)[:300]}")
    _raise_for_failed_campaign_action(result, "pause")
    return CampaignActionResponse(campaign_id=campaign_id, status="paused", result=result)


def _raise_for_failed_campaign_action(result: Any, action: str) -> None:
    """Return an HTTP error if AdTarget reports a failed runtime action item."""
    if not isinstance(result, list):
        return

    failed_items: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        errors = item.get("errors")
        has_errors = bool(errors)
        if item.get("isSuccess") is False or has_errors:
            failed_items.append(item)

    if failed_items:
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"AdTarget {action} failed",
                "errors": failed_items,
            },
        )

def _validate_campaign_action_request(
    campaign_id: int,
    request: CampaignActionRequest | None,
) -> None:
    if request is not None and request.campaign_id != campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id in path and body must match")


@app.post("/api/monitor", response_model=MonitorResponse)
async def monitor(request: MonitorRequest) -> MonitorResponse:
    """F3 Campaign Monitor — анализ кампании и рекомендации по улучшению."""
    try:
        return await monitor_run(request)
    except Exception as e:
        _handle_llm_error(e)


def _parse_flow_json(flow_json: str | None) -> dict[str, Any] | None:
    """Parse a stored frontend flow JSON string for campaign state persistence."""
    if not flow_json:
        return None
    try:
        parsed = json.loads(flow_json)
    except json.JSONDecodeError:
        return {"raw": flow_json}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _make_session_title(goal: str) -> str:
    """Builds a compact title from the first user prompt."""
    title = " ".join(goal.strip().split())
    if not title:
        return "Новый диалог Builder"
    return title[:77] + "..." if len(title) > 80 else title


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

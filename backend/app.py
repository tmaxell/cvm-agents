"""
cvm-agents — FastAPI backend

Endpoints:
  POST /api/copilot    — F1 CVM Copilot (RAG + LLM)
  POST /api/builder    — F2 Campaign Builder (LangGraph agentic loop)
  POST /api/builder/create — explicit Builder create action
  POST /api/segments/suggest — segment hypotheses for Builder
  POST /api/monitor    — F3 Monitoring UI entry point with optimization recommendations
  GET  /api/health     — health check
"""

# Загружаем .env до импортов агентов (они читают os.getenv при инициализации)
from dotenv import load_dotenv
load_dotenv()

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from schemas import (
    CopilotRequest,
    CopilotResponse,
    ChatRequest,
    ChatResponse,
    ChatTraceEvent,
    ChatAction,
    BuilderRequest,
    BuilderResponse,
    BuilderOptimizeRequest,
    BuilderCreateRequest,
    SegmentSuggestRequest,
    SegmentSuggestResponse,
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
from agents.adapters.copilot_adapter import CopilotAdapter, to_unified_payload as copilot_to_unified, from_unified_payload as copilot_from_unified
from agents.adapters.builder_adapter import BuilderAdapter, to_unified_payload as builder_to_unified, from_unified_payload as builder_from_unified
from agents.flow_optimizer import optimize_draft_flow
from agents.adapters.segment_adapter import SegmentAdapter, to_unified_payload as segment_to_unified, from_unified_payload as segment_from_unified
from agents.adapters.monitor_adapter import MonitorAdapter, to_unified_payload as monitor_to_unified, from_unified_payload as monitor_from_unified
from agents.orchestrator import AgentTask, Orchestrator
from db import DatabaseSessionStore, init_db
from scripts.seed_demo_campaigns import seed_demo_campaigns
from tools import adtarget
from agents.safety_review import build_review_checklist, is_review_allowed_for_runtime

logger = logging.getLogger(__name__)

app = FastAPI(title="CVM Agents API", version="0.1.0")
session_store = DatabaseSessionStore()

agent_orchestrator = Orchestrator({
    "copilot": CopilotAdapter(),
    "builder": BuilderAdapter(),
    "segment": SegmentAdapter(),
    "monitor": MonitorAdapter(),
})

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
    if os.getenv("SEED_DEMO_CAMPAIGNS_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}:
        await seed_demo_campaigns()


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/copilot", response_model=CopilotResponse)
async def copilot(request: CopilotRequest) -> CopilotResponse:
    """F1 CVM Copilot — отвечает на вопросы по платформе и текущей кампании."""
    try:
        result = await agent_orchestrator.execute(AgentTask(agent="copilot", payload=copilot_to_unified(request)))
        return copilot_from_unified(result.payload)
    except Exception as e:
        _handle_llm_error(e)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Unified chat endpoint for frontend chat widgets."""
    await session_store.ensure_chat_session(session_id=request.session_id)
    await session_store.add_chat_message(session_id=request.session_id, role="user", content=request.message)
    run_id = await session_store.create_chat_run(session_id=request.session_id, user_message=request.message)
    try:
        await session_store.add_chat_run_event(
            run_id=run_id,
            event="route_selected",
            detail="Request accepted by /api/chat compatibility endpoint.",
            metadata={"route": "/api/chat"},
        )
        await session_store.add_chat_run_event(run_id=run_id, event="plan_created", detail="Compatibility responder plan created.")
        await session_store.add_chat_run_event(run_id=run_id, event="step_started", detail="Building navigation action.")

        tool_input = {"session_id": request.session_id, "artifact_id": request.artifact_id, "action": _model_dump(request.action)}
        started = time.perf_counter()
        await session_store.add_chat_run_event(
            run_id=run_id,
            event="tool_called",
            detail="compose_builder_navigation_action",
            metadata={"tool_name": "compose_builder_navigation_action", "tool_input": _redact_tool_payload(tool_input)},
        )
        action_payload = {"route": "/builder", "session_id": request.session_id}
        artifacts = []
        next_actions = [ChatAction(id="builder", label="Открыть Builder", kind="navigate", payload=action_payload)]
        if request.action and request.action.id in {"save_campaign", "save_segment"}:
            artifact_type = "campaign_draft" if request.action.id == "save_campaign" else "segment_draft"
            artifact_content = request.action.payload.get("content_json") if request.action.payload else {}
            artifact_metadata = request.action.payload.get("metadata_json") if request.action.payload else {}
            artifact_id = await session_store.save_artifact(
                session_id=request.session_id,
                artifact_type=artifact_type,
                schema_version=int(request.action.payload.get("schema_version", 1)) if request.action.payload else 1,
                content_json=artifact_content if isinstance(artifact_content, dict) else {},
                metadata_json=artifact_metadata if isinstance(artifact_metadata, dict) else {},
                source_run_id=run_id,
            )
            artifacts.append({"id": artifact_id, "type": artifact_type, "content": artifact_content, "metadata": artifact_metadata})
            next_actions = [
                ChatAction(id="open_artifact", label="Открыть артефакт", kind="artifact", payload={"artifact_id": artifact_id}),
                ChatAction(id="builder", label="Открыть Builder", kind="navigate", payload=action_payload),
            ]
        latency_ms = int((time.perf_counter() - started) * 1000)
        await session_store.add_chat_run_event(
            run_id=run_id,
            event="tool_result",
            detail="compose_builder_navigation_action",
            metadata={"tool_name": "compose_builder_navigation_action", "status": "success", "latency_ms": latency_ms},
        )
        await session_store.add_chat_run_event(run_id=run_id, event="step_completed", detail="Navigation action is ready.")
        await session_store.add_chat_run_event(run_id=run_id, event="run_completed", detail="Chat run completed.")
        await session_store.complete_chat_run(run_id=run_id, status="completed")

        trace = _safe_chat_trace(await session_store.list_chat_run_events(run_id=run_id))
        response = ChatResponse(
            assistant_message=request.message,
            trace=trace,
            artifacts=artifacts,
            actions_available=next_actions,
            session_id=request.session_id,
        )
        await session_store.add_chat_message(
            session_id=request.session_id,
            role="assistant",
            content=response.assistant_message,
            metadata={"run_id": run_id},
        )
        return response
    except Exception as exc:
        await session_store.add_chat_run_event(run_id=run_id, event="run_failed", status="error", detail=str(exc)[:300])
        await session_store.complete_chat_run(run_id=run_id, status="failed")
        raise


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
    stored_version = _stored_draft_flow_version(stored_session)
    request_flow_json = _parse_flow_json(request.session_flow_json)
    canonical_flow_json = stored_session.draft_flow if stored_session and stored_session.draft_flow else request_flow_json
    effective_version = stored_version or request.draft_flow_version
    effective_brief = request.campaign_brief or (stored_session.campaign_brief if stored_session else None)
    effective_request = request.model_copy(update={
        "session_id": session.id,
        "history": stored_history,
        "session_flow_json": json.dumps(canonical_flow_json, ensure_ascii=False) if canonical_flow_json else None,
        "draft_flow_version": effective_version,
        "campaign_brief": effective_brief,
        "builder_preferences": (
            effective_brief.to_builder_preferences()
            if effective_brief is not None
            else request.builder_preferences
        ),
    })

    await session_store.add_message(
        session_id=session.id,
        role="user",
        content=request.goal,
        metadata={
            "builder_preferences": effective_request.builder_preferences,
            "campaign_brief": _model_dump(effective_request.campaign_brief),
            "campaign_id": request.session_campaign_id,
            "draft_flow_json": canonical_flow_json,
            "draft_flow_version": effective_version,
            "status": "collect_brief",
        },
    )

    try:
        result = await agent_orchestrator.execute(AgentTask(agent="builder", payload=builder_to_unified(effective_request)))
        response = builder_from_unified(result.payload)
    except Exception as e:
        await session_store.update_session(session.id, status="error")
        await session_store.upsert_campaign_state(
            session_id=session.id,
            campaign_id=request.session_campaign_id,
            draft_flow_json=canonical_flow_json,
            runtime_status="editing",
            draft_flow_version=effective_version,
            campaign_brief_json=_model_dump(effective_request.campaign_brief),
        )
        _handle_llm_error(e)

    response.session_id = session.id
    persisted_campaign_id = response.campaign_id or request.session_campaign_id or session.campaign_id
    persisted_flow_json = response.draft_flow or canonical_flow_json
    persisted_draft_flow_version = _resolve_response_draft_flow_version(
        response=response,
        request_flow_json=canonical_flow_json,
        current_version=effective_version,
    )
    response.campaign_id = persisted_campaign_id
    response.draft_flow = persisted_flow_json
    response.draft_flow_version = persisted_draft_flow_version
    await session_store.add_message(
        session_id=session.id,
        role="assistant",
        content=response.message,
        metadata={
            "campaign_id": persisted_campaign_id,
            "status": response.status,
            "builder_preferences": response.builder_preferences,
            "preference_patch": response.preference_patch,
            "campaign_brief": _model_dump(effective_request.campaign_brief),
            "draft_flow_json": persisted_flow_json,
            "draft_flow_version": persisted_draft_flow_version,
            "validation_errors": response.validation_errors,
            "brief_completeness": (
                response.brief_completeness.model_dump()
                if response.brief_completeness
                else None
            ),
            "review_checklist": (
                response.review_checklist.model_dump()
                if response.review_checklist
                else None
            ),
            "review_status": response.review_status,
            "review_checklist_acknowledged": response.review_checklist_acknowledged,
        },
    )
    await session_store.upsert_campaign_state(
        session_id=session.id,
        campaign_id=persisted_campaign_id,
        draft_flow_json=persisted_flow_json,
        runtime_status="editing",
        draft_flow_version=persisted_draft_flow_version,
        campaign_brief_json=_model_dump(effective_request.campaign_brief),
        brief_completeness_json=_model_dump(response.brief_completeness),
        review_checklist_json=_model_dump(response.review_checklist),
        review_status=response.review_status,
        review_checklist_acknowledged=response.review_checklist_acknowledged,
    )
    return response


@app.post("/api/builder/optimize", response_model=BuilderResponse)
async def builder_optimize(request: BuilderOptimizeRequest) -> BuilderResponse:
    """Deterministically improves a canonical Builder draft flow before launch."""
    session = await session_store.get_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    stored_version = _stored_draft_flow_version(session)
    if stored_version is not None and request.draft_flow_version != stored_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Draft flow version is stale",
                "expected_draft_flow_version": stored_version,
                "received_draft_flow_version": request.draft_flow_version,
            },
        )

    canonical_flow = session.draft_flow or request.draft_flow
    if not canonical_flow:
        raise HTTPException(status_code=400, detail="Draft flow is required for optimization")
    canonical_brief = session.campaign_brief or request.campaign_brief
    current_version = stored_version or request.draft_flow_version

    try:
        optimized_flow, optimized_version, checklist, additions, remaining = await optimize_draft_flow(
            draft_flow=canonical_flow,
            campaign_brief=canonical_brief,
            draft_flow_version=current_version,
            validation_errors=request.validation_errors,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected /api/builder/optimize failure")
        raise HTTPException(status_code=502, detail=f"Builder optimize failed: {str(e)[:300]}")

    if optimized_flow == canonical_flow:
        optimized_version = current_version
    added_text = "; ".join(additions) if additions else "ничего — не хватило безопасных данных для автодоработки"
    remaining_text = "; ".join(remaining[:5]) if remaining else "критичных рекомендаций не осталось"
    message = (
        "Доработал draft flow агентом. "
        f"Добавлено: {added_text}. "
        f"Осталось проверить: {remaining_text}."
    )
    response = BuilderResponse(
        message=message,
        builder_preferences=canonical_brief.to_builder_preferences() if canonical_brief is not None else None,
        session_id=session.id,
        campaign_id=session.campaign_id,
        draft_flow=optimized_flow,
        draft_flow_version=optimized_version,
        validation_errors=request.validation_errors,
        brief_completeness=check_campaign_brief_completeness(BuilderRequest(
            goal=canonical_brief.goal if canonical_brief and canonical_brief.goal else "Оптимизация draft flow",
            session_id=session.id,
            campaign_brief=canonical_brief,
            draft_flow_version=optimized_version,
            review_checklist_acknowledged=request.review_checklist_acknowledged,
        )),
        review_checklist=checklist,
        review_status=checklist.status,
        review_checklist_acknowledged=request.review_checklist_acknowledged,
        status=_status_for_flow_context(session.campaign_id, optimized_flow),
    )

    await session_store.add_message(
        session_id=session.id,
        role="user",
        content="Доработать флоу агентом",
        metadata={
            "campaign_brief": _model_dump(canonical_brief),
            "draft_flow_json": canonical_flow,
            "draft_flow_version": current_version,
            "status": "draft_ready",
        },
    )
    await session_store.add_message(
        session_id=session.id,
        role="assistant",
        content=response.message,
        metadata={
            "campaign_id": response.campaign_id,
            "status": response.status,
            "builder_preferences": response.builder_preferences,
            "campaign_brief": _model_dump(canonical_brief),
            "draft_flow_json": optimized_flow,
            "draft_flow_version": optimized_version,
            "validation_errors": response.validation_errors,
            "brief_completeness": _model_dump(response.brief_completeness),
            "review_checklist": checklist.model_dump(),
            "review_status": checklist.status,
            "review_checklist_acknowledged": request.review_checklist_acknowledged,
            "optimizer_added": additions,
            "optimizer_remaining": remaining,
        },
    )
    await session_store.upsert_campaign_state(
        session_id=session.id,
        campaign_id=response.campaign_id,
        draft_flow_json=optimized_flow,
        runtime_status="editing",
        draft_flow_version=optimized_version,
        campaign_brief_json=_model_dump(canonical_brief),
        brief_completeness_json=_model_dump(response.brief_completeness),
        review_checklist_json=checklist.model_dump(),
        review_status=checklist.status,
        review_checklist_acknowledged=request.review_checklist_acknowledged,
    )
    return response


@app.post("/api/builder/create", response_model=BuilderResponse)
async def builder_create(request: BuilderCreateRequest) -> BuilderResponse:
    """Explicit Builder create action — the only path that persists a draft in AdTarget."""
    session = await session_store.get_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    stored_version = _stored_draft_flow_version(session)
    if stored_version is not None and request.draft_flow_version != stored_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Draft flow version is stale",
                "expected_draft_flow_version": stored_version,
                "received_draft_flow_version": request.draft_flow_version,
            },
        )

    canonical_create_flow = session.draft_flow or request.draft_flow
    canonical_create_brief = session.campaign_brief or request.campaign_brief
    checklist = build_review_checklist(
        canonical_create_brief,
        canonical_create_flow,
        request.validation_errors,
    )

    try:
        result = await adtarget.create_campaign(canonical_create_flow)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AdTarget create failed: {str(e)[:300]}")

    campaign_id = _extract_created_campaign_id(result)
    if campaign_id is None:
        raise HTTPException(
            status_code=502,
            detail={"message": "AdTarget create response did not include campaignId", "result": result},
        )

    await session_store.add_message(
        session_id=session.id,
        role="user",
        content="Создать кампанию в AdTarget",
        metadata={
            "campaign_brief": _model_dump(canonical_create_brief),
            "draft_flow_json": canonical_create_flow,
            "draft_flow_version": request.draft_flow_version,
            "review_status": checklist.status,
            "review_checklist_acknowledged": request.review_checklist_acknowledged,
            "status": "draft_ready",
        },
    )
    response = BuilderResponse(
        message=f"Кампания создана в AdTarget. ID: **{campaign_id}**",
        session_id=session.id,
        campaign_id=campaign_id,
        draft_flow=canonical_create_flow,
        draft_flow_version=request.draft_flow_version,
        validation_errors=request.validation_errors,
        review_checklist=checklist,
        review_status=checklist.status,
        review_checklist_acknowledged=request.review_checklist_acknowledged,
        status="created_in_adtarget",
    )
    await session_store.add_message(
        session_id=session.id,
        role="assistant",
        content=response.message,
        metadata={
            "campaign_id": campaign_id,
            "status": response.status,
            "campaign_brief": _model_dump(canonical_create_brief),
            "draft_flow_json": canonical_create_flow,
            "draft_flow_version": request.draft_flow_version,
            "validation_errors": request.validation_errors,
            "review_checklist": checklist.model_dump(),
            "review_status": checklist.status,
            "review_checklist_acknowledged": request.review_checklist_acknowledged,
        },
    )
    await session_store.upsert_campaign_state(
        session_id=session.id,
        campaign_id=campaign_id,
        draft_flow_json=canonical_create_flow,
        runtime_status="editing",
        draft_flow_version=request.draft_flow_version,
        campaign_brief_json=_model_dump(canonical_create_brief),
        review_checklist_json=checklist.model_dump(),
        review_status=checklist.status,
        review_checklist_acknowledged=request.review_checklist_acknowledged,
    )
    return response


@app.post("/api/segments/suggest", response_model=SegmentSuggestResponse)
async def suggest_segments(request: SegmentSuggestRequest) -> SegmentSuggestResponse:
    """Suggests 2-3 structured audience segment hypotheses for a campaign."""
    try:
        result = await agent_orchestrator.execute(AgentTask(agent="segment", payload=segment_to_unified(request)))
        return segment_from_unified(result.payload)
    except HTTPException:
        raise
    except ValidationError as e:
        raise _segment_suggestion_validation_error(e)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        if _is_likely_llm_error(e):
            _handle_llm_error(e)
        raise _segment_suggestion_validation_error(e)
    except Exception as e:
        if _is_likely_llm_error(e):
            _handle_llm_error(e)
        logger.exception("Unexpected /api/segments/suggest failure")
        raise HTTPException(
            status_code=500,
            detail="Segment suggestion failed due to an unexpected backend error. Please try again later.",
        )


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
    _validate_campaign_action_request(campaign_id, request, enforce_review=False)
    try:
        result = await adtarget.pause_campaign(campaign_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AdTarget pause failed: {str(e)[:300]}")
    _raise_for_failed_campaign_action(result, "pause")
    return CampaignActionResponse(campaign_id=campaign_id, status="paused", result=result)


def _extract_created_campaign_id(result: Any) -> int | None:
    if isinstance(result, dict):
        value = result.get("campaignId") or result.get("campaign_id") or result.get("id")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


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
    *,
    enforce_review: bool = True,
) -> None:
    if request is not None and request.campaign_id != campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id in path and body must match")
    if not enforce_review:
        return
    review_status = request.review_status if request is not None else "blocked"
    acknowledged = bool(request.review_checklist_acknowledged) if request is not None else False
    if not is_review_allowed_for_runtime(review_status, acknowledged):
        raise HTTPException(
            status_code=400,
            detail="Campaign action blocked until review checklist is green or warnings are explicitly acknowledged",
        )


@app.post("/api/monitor", response_model=MonitorResponse)
async def monitor(request: MonitorRequest) -> MonitorResponse:
    """F3 Campaign Monitor — анализ кампании и рекомендации по улучшению."""
    try:
        result = await agent_orchestrator.execute(AgentTask(agent="monitor", payload=monitor_to_unified(request)))
        return monitor_from_unified(result.payload)
    except Exception as e:
        _handle_llm_error(e)


def _model_dump(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return None


_SAFE_TRACE_METADATA_KEYS = {"tool_name", "status", "latency_ms", "route"}
_REDACT_KEYS = {"token", "authorization", "password", "secret", "api_key", "key"}


def _safe_chat_trace(events: list[ChatTraceEvent]) -> list[ChatTraceEvent]:
    safe_events: list[ChatTraceEvent] = []
    for event in events:
        safe_metadata = {key: value for key, value in event.metadata.items() if key in _SAFE_TRACE_METADATA_KEYS}
        safe_events.append(
            ChatTraceEvent(
                event=event.event,
                status=event.status,
                detail=event.detail,
                ts=event.ts,
                metadata=safe_metadata,
            )
        )
    return safe_events


def _redact_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if any(sensitive in key.lower() for sensitive in _REDACT_KEYS):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted

def _parse_flow_json(flow_json: str | None) -> dict[str, Any] | None:
    """Parse a stored frontend flow JSON string for campaign state persistence."""
    if not flow_json:
        return None
    try:
        parsed = json.loads(flow_json)
    except json.JSONDecodeError:
        return {"raw": flow_json}
    return parsed if isinstance(parsed, dict) else {"value": parsed}



def _metadata_draft_flow_version(metadata: dict[str, Any] | None) -> int | None:
    value = (metadata or {}).get("draft_flow_version")
    return value if isinstance(value, int) and value > 0 else None


def _stored_draft_flow_version(session: SessionDetail | None) -> int | None:
    if session is None:
        return None
    if session.draft_flow_version is not None and session.draft_flow_version > 0:
        return session.draft_flow_version
    for message in reversed(session.messages):
        version = _metadata_draft_flow_version(message.metadata)
        if version is not None:
            return version
    return None


def _resolve_response_draft_flow_version(
    *,
    response: BuilderResponse,
    request_flow_json: dict[str, Any] | None,
    current_version: int | None,
) -> int | None:
    if response.draft_flow_version is not None and response.draft_flow_version > 0:
        return response.draft_flow_version
    if response.draft_flow is None and request_flow_json is None:
        return None
    if response.draft_flow is not None and response.draft_flow != request_flow_json:
        return (current_version or 0) + 1
    return current_version or (1 if response.draft_flow is not None else None)

def _make_session_title(goal: str) -> str:
    """Builds a compact title from the first user prompt."""
    title = " ".join(goal.strip().split())
    if not title:
        return "Новый диалог Builder"
    return title[:77] + "..." if len(title) > 80 else title



def _segment_suggestion_validation_error(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={
            "message": "Segment suggestion validation failed",
            "error": str(e)[:500],
        },
    )


def _is_likely_llm_error(e: Exception) -> bool:
    err_str = str(e)
    err_lower = err_str.lower()
    llm_markers = (
        "llm",
        "gigachat",
        "groq",
        "anthropic",
        "gemini",
        "ollama",
        "openai",
        "api key",
        "unauthorized",
        "too many requests",
        "payment required",
        "request too large",
    )
    return (
        any(marker in err_lower for marker in llm_markers)
        or any(status in err_str for status in ("401", "402", "413", "429"))
    )


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

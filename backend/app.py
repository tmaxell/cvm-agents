"""cvm-agents — FastAPI backend для unified chat виджета.

Endpoints:
  GET  /api/health
  GET  /api/sessions                       — список сессий чата
  POST /api/sessions                       — создать новую сессию
  GET  /api/sessions/{id}                  — сессия + сообщения + артефакты
  GET  /api/sessions/{id}/messages         — только сообщения
  POST /api/chat                           — единая точка входа в мультиагентную систему
  POST /api/campaigns/{id}/start           — запустить кампанию в AdTarget
  POST /api/campaigns/{id}/pause           — поставить кампанию на паузу

Внутри /api/chat работает supervisor (agents/supervisor.py):
  action → RuntimeAgent / RefinerAgent / BuilderAgent
  message → intent classifier → план → один из специализированных агентов.
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import ChatStore, init_db
from schemas import ChatAction, ChatArtifact, ChatTraceEvent
from agents.base import AgentContext
from agents.supervisor import handle as supervisor_handle
from tools import adtarget

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

store = ChatStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if os.getenv("SEED_DEMO_CAMPAIGNS_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}:
        try:
            from scripts.seed_demo_campaigns import seed_demo_campaigns
            await seed_demo_campaigns()
            logger.info("demo campaigns seeded")
        except Exception as exc:
            logger.warning("demo seed failed: %s", exc)
    yield


app = FastAPI(title="CVM Agents API", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_backend_root = Path(__file__).parent
_project_root = _backend_root.parent
for _docs_dir, _mount_name in [
    (_project_root / "docs", "source-docs"),
    (_project_root.parent / "cvmCopilot" / "docs", "cvmCopilot-docs"),
]:
    if _docs_dir.exists():
        app.mount(f"/{_mount_name}", StaticFiles(directory=str(_docs_dir), follow_symlink=True), name=_mount_name)


# ── Request / response schemas ────────────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    title: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = ""
    action: ChatAction | None = None


class ChatResponse(BaseModel):
    assistant_message: str
    trace: list[ChatTraceEvent] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    actions_available: list[ChatAction] = Field(default_factory=list)
    session_id: str


class CampaignActionRequest(BaseModel):
    campaign_id: int


class CampaignActionResponse(BaseModel):
    campaign_id: int
    status: str
    result: Any = None


# ── Health & sessions ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/sessions")
async def list_sessions():
    return await store.list_sessions()


@app.post("/api/sessions")
async def create_session(request: SessionCreateRequest):
    return await store.ensure_session(title=request.title)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    detail = await store.get_session_with_messages(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@app.get("/api/sessions/{session_id}/messages")
async def list_session_messages(session_id: str):
    detail = await store.get_session(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await store.list_messages(session_id)
    return {"messages": messages, "next_cursor": None, "has_more": False}


# ── Unified chat — supervisor entrypoint ──────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session = await store.ensure_session(session_id=request.session_id or None)
    session_id = session["id"]
    if request.message:
        await store.add_message(session_id=session_id, role="user", content=request.message)
    run_id = await store.create_run(session_id=session_id, user_message=request.message or f"action:{request.action.id if request.action else 'noop'}")

    await store.add_event(run_id=run_id, event="route_selected", detail="POST /api/chat")

    history = await store.list_messages(session_id)
    artifacts = await store.list_artifacts(session_id=session_id)

    ctx = AgentContext(
        session_id=session_id,
        run_id=run_id,
        store=store,
        message=request.message or "",
        history=history,
        action=request.action,
        artifacts=artifacts,
    )

    try:
        result = await supervisor_handle(ctx)
        intent_name = ctx.inputs.get("action_id") or "auto"
        await store.add_event(run_id=run_id, event="run_completed", status="info" if result.status == "ok" else "warning",
                               detail=f"status={result.status}")
        await store.complete_run(run_id=run_id, status="completed" if result.status == "ok" else "failed", intent=str(intent_name))
    except Exception as exc:
        logger.exception("/api/chat supervisor crashed")
        await store.add_event(run_id=run_id, event="run_failed", status="error", detail=str(exc)[:300])
        await store.complete_run(run_id=run_id, status="failed", intent="crash")
        result_message = f"Не удалось обработать запрос: {str(exc)[:200]}"
        await store.add_message(session_id=session_id, role="assistant", content=result_message, metadata={"run_id": run_id, "error": True})
        trace = await store.list_events(run_id=run_id)
        return ChatResponse(
            assistant_message=result_message,
            trace=trace,
            artifacts=[],
            actions_available=[],
            session_id=session_id,
        )

    trace = await store.list_events(run_id=run_id)
    await store.add_message(
        session_id=session_id,
        role="assistant",
        content=result.assistant_message,
        metadata={
            "run_id": run_id,
            "actions": [a.model_dump() for a in result.actions],
            "artifact_ids": [a.get("id") for a in result.artifacts if isinstance(a, dict)],
        },
    )

    return ChatResponse(
        assistant_message=result.assistant_message,
        trace=trace,
        artifacts=[ChatArtifact(**_artifact_response(a)) for a in result.artifacts if isinstance(a, dict)],
        actions_available=result.actions,
        session_id=session_id,
    )


# ── Runtime campaign actions (HTTP-level convenience) ─────────────────────────

@app.post("/api/campaigns/{campaign_id}/start", response_model=CampaignActionResponse)
async def start_campaign(campaign_id: int, request: CampaignActionRequest | None = Body(default=None)):
    if request is not None and request.campaign_id != campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id mismatch")
    try:
        result = await adtarget.start_campaign(campaign_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AdTarget start failed: {str(exc)[:300]}")
    return CampaignActionResponse(campaign_id=campaign_id, status="active", result=result)


@app.post("/api/campaigns/{campaign_id}/pause", response_model=CampaignActionResponse)
async def pause_campaign(campaign_id: int, request: CampaignActionRequest | None = Body(default=None)):
    if request is not None and request.campaign_id != campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id mismatch")
    try:
        result = await adtarget.pause_campaign(campaign_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AdTarget pause failed: {str(exc)[:300]}")
    return CampaignActionResponse(campaign_id=campaign_id, status="paused", result=result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _artifact_response(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": artifact["id"],
        "type": artifact.get("type", "unknown"),
        "title": None,
        "content": artifact.get("content"),
        "url": None,
        "metadata": artifact.get("metadata") or {},
    }

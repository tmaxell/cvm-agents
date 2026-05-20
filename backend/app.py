"""cvm-agents — единый FastAPI backend для unified chat виджета.

Endpoints:
  GET  /api/health
  GET  /api/sessions                       — список сессий чата
  POST /api/sessions                       — создать новую сессию
  GET  /api/sessions/{id}                  — сессия + сообщения + артефакты
  GET  /api/sessions/{id}/messages         — только сообщения
  POST /api/chat                           — отправить сообщение, маршрутизировать к агенту
  POST /api/campaigns/{id}/start           — запустить кампанию в AdTarget
  POST /api/campaigns/{id}/pause           — поставить кампанию на паузу

Внутри /api/chat работает intent classifier → запускает один из агентов:
  campaign_attention, build_campaign, suggest_segments, refine_campaign, documentation_qa.
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import ChatStore, init_db
from schemas import ChatAction, ChatArtifact, ChatTraceEvent
from agents.chat_orchestrator import classify_intent
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


app = FastAPI(title="CVM Agents API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Документация для просмотра источников из UI (если папки существуют).
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
    session_id: str
    message: str
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
    return {"status": "ok", "version": "0.2.0"}


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


# ── Unified chat ──────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Единая точка входа: маршрутизирует к агентам, возвращает trace + artifacts + actions."""
    session = await store.ensure_session(session_id=request.session_id or None)
    session_id = session["id"]
    await store.add_message(session_id=session_id, role="user", content=request.message)
    run_id = await store.create_run(session_id=session_id, user_message=request.message)

    artifacts: list[dict[str, Any]] = []
    next_actions: list[ChatAction] = []
    assistant_message = ""
    intent_name: str = "documentation_qa"

    try:
        await store.add_event(run_id=run_id, event="route_selected", detail="POST /api/chat")

        # 1) Explicit action — сохранения / runtime.
        if request.action is not None:
            assistant_message, artifacts, next_actions = await _handle_action(
                session_id=session_id, run_id=run_id, action=request.action,
            )
            intent_name = f"action:{request.action.id}"
        else:
            # 2) Classify intent.
            history = await store.list_messages(session_id)
            decision = await classify_intent(request.message)
            intent_name = decision.intent
            await store.add_event(
                run_id=run_id, event="plan_created",
                detail=f"intent={decision.intent} confidence={decision.confidence:.2f} ({decision.reason})",
                metadata={"intent": decision.intent, "confidence": decision.confidence},
            )

            # 3) Dispatch.
            if decision.intent == "campaign_attention":
                assistant_message, artifacts, next_actions = await _run_attention(session_id, run_id)
            elif decision.intent == "build_campaign":
                assistant_message, artifacts, next_actions = await _run_builder(
                    session_id=session_id, run_id=run_id, goal=request.message, history=history,
                )
            elif decision.intent == "suggest_segments":
                assistant_message, artifacts, next_actions = await _run_segments(
                    session_id=session_id, run_id=run_id, message=request.message,
                )
            elif decision.intent == "refine_campaign":
                assistant_message, artifacts, next_actions = await _run_refine(
                    session_id=session_id, run_id=run_id, message=request.message,
                )
            else:
                assistant_message, artifacts, next_actions = await _run_qa(
                    session_id=session_id, run_id=run_id, message=request.message, history=history,
                )

        await store.add_event(run_id=run_id, event="run_completed", detail="ok")
        await store.complete_run(run_id=run_id, status="completed", intent=intent_name)
    except Exception as exc:
        logger.exception("/api/chat failed")
        await store.add_event(run_id=run_id, event="run_failed", status="error", detail=str(exc)[:300])
        await store.complete_run(run_id=run_id, status="failed", intent=intent_name)
        assistant_message = f"Не удалось обработать запрос: {str(exc)[:200]}"

    trace = await store.list_events(run_id=run_id)
    await store.add_message(
        session_id=session_id,
        role="assistant",
        content=assistant_message,
        metadata={"run_id": run_id, "intent": intent_name, "actions": [a.model_dump() for a in next_actions]},
    )

    return ChatResponse(
        assistant_message=assistant_message,
        trace=trace,
        artifacts=[ChatArtifact(**_artifact_to_response(a)) for a in artifacts],
        actions_available=next_actions,
        session_id=session_id,
    )


# ── Intent handlers ───────────────────────────────────────────────────────────

async def _run_attention(session_id: str, run_id: str) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    """Анализ кампаний, требующих внимания."""
    from agents.campaign_attention import build_campaign_attention_report

    await store.add_event(run_id=run_id, event="step_started", detail="Анализ demo_campaigns + campaign_health")
    started = time.perf_counter()
    report = await build_campaign_attention_report()
    latency = int((time.perf_counter() - started) * 1000)
    await store.add_event(
        run_id=run_id, event="step_completed", detail=f"Получено кампаний: {len(report.get('campaigns', []))}",
        metadata={"latency_ms": latency},
    )

    campaigns = report.get("campaigns") or []
    if not campaigns:
        reason = report.get("reason", "")
        hints = report.get("suggested_next_steps") or []
        message = "Кампаний, требующих внимания, не найдено."
        if reason:
            message += f"\n\n_{reason}_"
        if hints:
            message += "\n\nЧто можно сделать:\n" + "\n".join(f"- {h}" for h in hints)
        return message, [], []

    lines = [f"**Топ кампаний, требующих внимания** ({len(campaigns)}):", ""]
    top = campaigns[:5]
    for item in top:
        lines.append(f"### {item['campaign_name']} (id {item['campaign_id']})")
        lines.append(f"- ⚠️ {item['what_is_wrong']}")
        lines.append(f"- 💰 {item['why_it_matters']}")
        lines.append(f"- 🔧 {item['suggested_fix']}")
        lines.append("")
    message = "\n".join(lines).strip()

    # Сохраняем артефакт-отчёт.
    artifact_id = await store.save_artifact(
        session_id=session_id,
        artifact_type="attention_report",
        content_json={"campaigns": campaigns, "formula": report.get("ranking_formula")},
        metadata_json={"top_n": len(top)},
        source_run_id=run_id,
    )
    artifact = await store.get_artifact(artifact_id)

    # Готовим actions — открыть конкретные кампании.
    actions: list[ChatAction] = []
    for item in top[:3]:
        actions.append(ChatAction(
            id="refine_campaign",
            label=f"Доработать «{item['campaign_name'][:30]}»",
            kind="refine",
            payload={"campaign_id": item["campaign_id"], "campaign_name": item["campaign_name"]},
        ))

    return message, [artifact] if artifact else [], actions


async def _run_builder(
    *, session_id: str, run_id: str, goal: str, history: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    """Создание кампании через campaign_builder.run()."""
    from agents.campaign_builder import run as builder_run
    from schemas import BuilderRequest

    await store.add_event(run_id=run_id, event="step_started", detail="Campaign Builder: сборка флоу")
    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in history[-10:]
        if m["role"] in {"user", "assistant"}
    ]
    request = BuilderRequest(goal=goal, history=history_pairs)
    started = time.perf_counter()
    response = await builder_run(request)
    latency = int((time.perf_counter() - started) * 1000)
    await store.add_event(
        run_id=run_id, event="step_completed",
        detail=f"Builder статус: {response.status}", metadata={"latency_ms": latency},
    )

    artifacts: list[dict[str, Any]] = []
    actions: list[ChatAction] = []

    if response.draft_flow:
        artifact_id = await store.save_artifact(
            session_id=session_id,
            artifact_type="draft_flow",
            content_json=response.draft_flow,
            metadata_json={
                "draft_flow_version": response.draft_flow_version,
                "campaign_id": response.campaign_id,
                "review_status": response.review_status,
            },
            source_run_id=run_id,
        )
        a = await store.get_artifact(artifact_id)
        if a:
            artifacts.append(a)

        actions.append(ChatAction(
            id="save_campaign",
            label="Сохранить кампанию в AdTarget",
            kind="save",
            payload={
                "draft_flow": response.draft_flow,
                "draft_flow_version": response.draft_flow_version,
                "campaign_brief": response.builder_preferences,
            },
        ))
        actions.append(ChatAction(
            id="refine_campaign",
            label="Доработать флоу",
            kind="refine",
            payload={"draft_flow": response.draft_flow, "draft_flow_version": response.draft_flow_version},
        ))

    if response.campaign_id:
        await store.set_campaign_id(session_id=session_id, campaign_id=response.campaign_id)

    return response.message, artifacts, actions


async def _run_segments(
    *, session_id: str, run_id: str, message: str,
) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    """Гипотезы сегментов через segment_agent.suggest_segments()."""
    from agents.segment_agent import suggest_segments
    from schemas import SegmentSuggestRequest

    await store.add_event(run_id=run_id, event="step_started", detail="Segment Agent: генерация гипотез")
    request = SegmentSuggestRequest(
        product=_extract_marker(message, ("продукт", "product")) or "general",
        campaign_goal=message,
    )
    started = time.perf_counter()
    response = await suggest_segments(request)
    latency = int((time.perf_counter() - started) * 1000)
    await store.add_event(
        run_id=run_id, event="step_completed",
        detail=f"Гипотез: {len(response.hypotheses)}", metadata={"latency_ms": latency},
    )

    lines = [response.summary or "Сформировал гипотезы сегментов:", ""]
    for h in response.hypotheses[:3]:
        name = h.name or h.title or "Без названия"
        desc = h.audience_description or h.description or ""
        reason = h.relevance_reason or h.rationale or ""
        lines.append(f"### {name}")
        if desc:
            lines.append(f"- 👥 {desc}")
        if reason:
            lines.append(f"- 🎯 {reason}")
        if h.risk_or_limitation:
            lines.append(f"- ⚠️ {h.risk_or_limitation}")
        lines.append("")
    text = "\n".join(lines).strip()

    artifacts: list[dict[str, Any]] = []
    actions: list[ChatAction] = []
    if response.hypotheses:
        primary = response.hypotheses[0]
        artifact_id = await store.save_artifact(
            session_id=session_id,
            artifact_type="segment_draft",
            content_json=primary.model_dump(),
            metadata_json={"hypotheses_count": len(response.hypotheses)},
            source_run_id=run_id,
        )
        a = await store.get_artifact(artifact_id)
        if a:
            artifacts.append(a)
        actions.append(ChatAction(
            id="save_segment",
            label="Сохранить сегмент",
            kind="save",
            payload={"segment": primary.model_dump()},
        ))
        actions.append(ChatAction(
            id="build_campaign_from_segment",
            label="Создать кампанию из сегмента",
            kind="navigate",
            payload={"segment": primary.model_dump()},
        ))

    return text, artifacts, actions


async def _run_refine(
    *, session_id: str, run_id: str, message: str,
) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    """Доработка существующей кампании / черновика."""
    await store.add_event(run_id=run_id, event="step_started", detail="Поиск последнего draft_flow в артефактах")
    artifacts = await store.list_artifacts(session_id=session_id)
    draft_flows = [a for a in artifacts if a["type"] in ("draft_flow", "campaign_draft")]
    if not draft_flows:
        await store.add_event(run_id=run_id, event="step_completed", detail="Нет draft flow в сессии")
        return (
            "В этой сессии ещё нет draft flow для доработки. Сначала попросите создать кампанию.",
            [],
            [ChatAction(id="build_campaign", label="Создать кампанию", kind="navigate", payload={"message": message})],
        )

    latest = draft_flows[-1]
    flow_content = latest["content"] or {}
    activities_count = len((flow_content or {}).get("activities", []))

    # Простой эвристический разбор: предложим, что улучшить.
    recommendations = []
    if activities_count < 3:
        recommendations.append("Добавить EventActivity для срабатывания по триггеру или WaitActivity для контроля частоты.")
    if not any(act.get("type") == "ResponseActivity" for act in (flow_content.get("activities") or [])):
        recommendations.append("Добавить ResponseActivity для измерения целевого действия.")
    if not recommendations:
        recommendations.append("Флоу выглядит сбалансированно. Рассмотрите A/B-тест с альтернативным каналом.")

    text = (
        f"**Анализ текущего draft flow** (activities: {activities_count})\n\n"
        + "Рекомендации:\n" + "\n".join(f"- {r}" for r in recommendations)
    )
    await store.add_event(run_id=run_id, event="step_completed", detail=f"Рекомендаций: {len(recommendations)}")

    actions = [
        ChatAction(id="save_campaign", label="Сохранить кампанию в AdTarget", kind="save",
                   payload={"draft_flow": flow_content, "draft_flow_version": (latest.get("metadata") or {}).get("draft_flow_version", 1)}),
    ]
    return text, [latest], actions


async def _run_qa(
    *, session_id: str, run_id: str, message: str, history: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    """Документация / общий вопрос через qa_copilot."""
    from agents.qa_copilot import answer as copilot_answer
    from schemas import CopilotRequest

    await store.add_event(run_id=run_id, event="step_started", detail="QA Copilot: RAG + LLM")
    started = time.perf_counter()
    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in history[-6:]
        if m["role"] in {"user", "assistant"}
    ]
    try:
        response = await copilot_answer(CopilotRequest(question=message, history=history_pairs))
    except Exception as exc:
        await store.add_event(run_id=run_id, event="step_completed", status="error", detail=str(exc)[:200])
        return f"Ошибка copilot: {str(exc)[:200]}", [], []

    latency = int((time.perf_counter() - started) * 1000)
    await store.add_event(
        run_id=run_id, event="step_completed",
        detail=f"Источников: {len(response.citations)}", metadata={"latency_ms": latency},
    )

    text = response.answer
    if response.citations:
        text += "\n\n**Источники:**\n" + "\n".join(
            f"- {c.title or c.source}" for c in response.citations[:5]
        )
    return text, [], []


# ── Action handlers (save_campaign / save_segment / refine_campaign) ──────────

async def _handle_action(
    *, session_id: str, run_id: str, action: ChatAction,
) -> tuple[str, list[dict[str, Any]], list[ChatAction]]:
    await store.add_event(run_id=run_id, event="step_started", detail=f"action:{action.id}")
    payload = action.payload or {}

    if action.id == "save_campaign":
        draft_flow = payload.get("draft_flow")
        if not isinstance(draft_flow, dict):
            return "Не получил draft_flow для сохранения.", [], []
        try:
            result = await adtarget.create_campaign(draft_flow)
        except Exception as exc:
            await store.add_event(run_id=run_id, event="step_completed", status="error", detail=str(exc)[:200])
            return f"Не удалось создать кампанию в AdTarget: {str(exc)[:200]}", [], []
        campaign_id = _extract_campaign_id(result)
        if campaign_id:
            await store.set_campaign_id(session_id=session_id, campaign_id=campaign_id)
        artifact_id = await store.save_artifact(
            session_id=session_id,
            artifact_type="campaign_draft",
            content_json=draft_flow,
            metadata_json={"campaign_id": campaign_id, "adtarget_result": result if isinstance(result, dict) else {"raw": str(result)}},
            source_run_id=run_id,
        )
        artifact = await store.get_artifact(artifact_id)
        await store.add_event(run_id=run_id, event="step_completed", detail=f"campaign_id={campaign_id}")
        actions = []
        if campaign_id:
            actions.append(ChatAction(id="start_campaign", label="Запустить", kind="runtime", payload={"campaign_id": campaign_id}))
        return (
            f"✅ Кампания создана в AdTarget" + (f". ID: **{campaign_id}**" if campaign_id else "."),
            [artifact] if artifact else [],
            actions,
        )

    if action.id == "save_segment":
        segment = payload.get("segment") or payload.get("content_json") or payload
        artifact_id = await store.save_artifact(
            session_id=session_id,
            artifact_type="segment_draft",
            content_json=segment if isinstance(segment, dict) else {"value": segment},
            metadata_json={},
            source_run_id=run_id,
        )
        artifact = await store.get_artifact(artifact_id)
        await store.add_event(run_id=run_id, event="step_completed", detail=f"segment saved")
        return "✅ Сегмент сохранён.", [artifact] if artifact else [], []

    if action.id == "save_target_group":
        artifact_id = await store.save_artifact(
            session_id=session_id,
            artifact_type="target_group_draft",
            content_json=payload if isinstance(payload, dict) else {"value": payload},
            metadata_json={},
            source_run_id=run_id,
        )
        artifact = await store.get_artifact(artifact_id)
        await store.add_event(run_id=run_id, event="step_completed", detail=f"target group saved")
        return "✅ Таргет-группа сохранена.", [artifact] if artifact else [], []

    if action.id == "start_campaign":
        campaign_id = payload.get("campaign_id")
        if not campaign_id:
            return "Не указан campaign_id для запуска.", [], []
        try:
            await adtarget.start_campaign(int(campaign_id))
        except Exception as exc:
            return f"Не удалось запустить кампанию: {str(exc)[:200]}", [], []
        await store.add_event(run_id=run_id, event="step_completed", detail=f"started {campaign_id}")
        return f"▶ Кампания **{campaign_id}** запущена.", [], []

    await store.add_event(run_id=run_id, event="step_completed", detail=f"unknown action: {action.id}")
    return f"Неизвестное действие: {action.id}", [], []


# ── Runtime campaign actions ──────────────────────────────────────────────────

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

def _extract_campaign_id(result: Any) -> int | None:
    if isinstance(result, dict):
        for key in ("campaignId", "campaign_id", "id"):
            value = result.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None


def _extract_marker(message: str, markers: tuple[str, ...]) -> str | None:
    lower = message.lower()
    for marker in markers:
        if marker in lower:
            idx = lower.index(marker) + len(marker)
            tail = message[idx:].lstrip(": ").strip()
            value = tail.split(".")[0].split(",")[0].strip()
            if value:
                return value
    return None


def _artifact_to_response(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": artifact["id"],
        "type": artifact["type"],
        "title": None,
        "content": artifact.get("content"),
        "url": None,
        "metadata": artifact.get("metadata") or {},
    }

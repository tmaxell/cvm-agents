"""BuilderAgent — собирает campaign flow из бизнес-цели.

Сначала пытается полноценный LangGraph builder (campaign_builder.run).
Если он падает / возвращает status=error из-за tool calling в LLM —
делаем deterministic fallback: минимальный flow (Common → TargetGroup → PushCommunication).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.campaign_builder import run as builder_run
from schemas import BuilderRequest, ChatAction
from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_target_group_activity,
    make_push_communication_activity,
)

logger = logging.getLogger(__name__)

NAME = "builder"
DESCRIPTION = "Собирает draft_flow кампании из бизнес-цели."
SUPPORTED_INTENTS = ("build_campaign",)


async def execute(ctx: AgentContext) -> AgentResult:
    goal = ctx.inputs.get("goal") or ctx.message
    seed_segment = ctx.inputs.get("segment")  # из «Создать кампанию из сегмента»

    await ctx.emit("step_started", detail=f"BuilderAgent: цель — «{_truncate(goal, 80)}»")
    started = time.perf_counter()

    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx.history[-10:]
        if m.get("role") in {"user", "assistant"}
    ]

    request = BuilderRequest(goal=goal, history=history_pairs)
    response_message = ""
    response_flow: dict[str, Any] | None = None
    response_status = "error"
    builder_failed = False

    try:
        response = await builder_run(request)
        response_message = response.message or ""
        response_flow = response.draft_flow if isinstance(response.draft_flow, dict) else None
        response_status = response.status or ("draft_ready" if response_flow else "error")
        if response_status in {"error", "needs_review"} and not response_flow:
            builder_failed = True
    except Exception as exc:
        logger.warning("campaign_builder.run raised: %s", exc)
        await ctx.emit("step_started", status="warning", detail=f"Builder error: {str(exc)[:200]}")
        builder_failed = True

    if builder_failed or not response_flow:
        await ctx.emit("step_started", detail="BuilderAgent: deterministic fallback flow")
        response_flow = _build_fallback_flow(goal, seed_segment)
        response_message = (
            "LLM-builder не смог собрать флоу автоматически. "
            "Собрал базовый шаблон: **Common → TargetGroup → Push**.\n\n"
            "Откорректируйте параметры (целевая аудитория, оффер, частота) или попросите доработать."
        )
        response_status = "draft_ready"
    elif response_status == "error" and response_flow:
        # LangGraph builder упал на одном из тулов, но частичный flow собрал.
        activities = response_flow.get("activities") or []
        types = ", ".join({a.get("type") for a in activities if isinstance(a, dict)}) or "—"
        response_message = (
            f"Собрал черновик флоу ({len(activities)} активностей: {types}). "
            "Часть шагов через LLM не дошла до конца — проверьте структуру и подправьте через «Доработать флоу»."
        )
        response_status = "draft_ready"

    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit(
        "step_completed",
        detail=f"BuilderAgent статус: {response_status}",
        metadata={"latency_ms": latency, "fallback": builder_failed},
    )

    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="draft_flow",
        content_json=response_flow,
        metadata_json={
            "status": response_status,
            "fallback": builder_failed,
            "goal": _truncate(goal, 200),
        },
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    actions = [
        ChatAction(
            id="save_campaign",
            label="Сохранить кампанию в AdTarget",
            kind="save",
            payload={"draft_flow": response_flow},
        ),
        ChatAction(
            id="refine_campaign",
            label="Доработать флоу",
            kind="refine",
            payload={"draft_flow": response_flow},
        ),
    ]

    return AgentResult(
        assistant_message=response_message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        status="ok",
        metadata={"fallback": builder_failed, "activities": len(response_flow.get("activities", []))},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _build_fallback_flow(goal: str, seed_segment: dict[str, Any] | None) -> dict[str, Any]:
    """Минимальный детерминистский flow, если LLM-builder упал."""
    campaign_name = _truncate(goal, 60) or "Новая кампания"
    audience_hint = ""
    if isinstance(seed_segment, dict):
        audience_hint = (
            seed_segment.get("audience_description")
            or seed_segment.get("description")
            or seed_segment.get("name")
            or ""
        )

    common = make_common_activity(campaign_name)
    target = make_target_group_activity(target_group_id=1)
    if audience_hint:
        target["name"] = f"Аудитория — {_truncate(audience_hint, 40)}"
    push = make_push_communication_activity(
        channel_id=1,
        content_type="SmsContent",
        message_text=f"Привет! Специальное предложение по «{campaign_name}». Подробности уточняйте.",
    )
    flow = assemble_flow([common, target, push])
    return flow

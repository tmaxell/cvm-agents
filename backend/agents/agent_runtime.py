"""RuntimeAgent — выполняет действия в AdTarget: save_campaign / start / pause / save artifact."""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.base import AgentContext, AgentResult
from schemas import ChatAction
from tools import adtarget

logger = logging.getLogger(__name__)

NAME = "runtime"
DESCRIPTION = "Сохраняет/запускает/паузит кампанию в AdTarget; сохраняет сегмент и таргет-группу как артефакты."
SUPPORTED_INTENTS = ("runtime_action",)


async def execute(ctx: AgentContext) -> AgentResult:
    action = ctx.action
    if action is None:
        return AgentResult(assistant_message="Не передано действие.", status="error")

    handler = _DISPATCH.get(action.id)
    if handler is None:
        await ctx.emit("step_completed", status="warning", detail=f"unknown action: {action.id}")
        return AgentResult(assistant_message=f"Неизвестное действие: {action.id}", status="error")

    return await handler(ctx, action.payload or {})


async def _save_campaign(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    draft_flow = payload.get("draft_flow")
    if not isinstance(draft_flow, dict):
        latest = ctx.latest_artifact("draft_flow", "campaign_draft")
        draft_flow = (latest or {}).get("content") if latest else None
    if not isinstance(draft_flow, dict):
        return AgentResult(assistant_message="Нет draft_flow для сохранения. Создайте черновик кампании.", status="needs_input")

    await ctx.emit("step_started", detail="RuntimeAgent: POST AdTarget create_campaign")
    started = time.perf_counter()
    try:
        result = await adtarget.create_campaign(draft_flow)
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=str(exc)[:200])
        return AgentResult(assistant_message=f"Не удалось создать кампанию в AdTarget: {str(exc)[:200]}", status="error")
    latency = int((time.perf_counter() - started) * 1000)
    campaign_id = _extract_campaign_id(result)
    await ctx.emit(
        "step_completed",
        detail=f"AdTarget OK, campaign_id={campaign_id}",
        metadata={"latency_ms": latency, "campaign_id": campaign_id},
    )

    if campaign_id:
        await ctx.store.set_campaign_id(session_id=ctx.session_id, campaign_id=campaign_id)
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="campaign_draft",
        content_json=draft_flow,
        metadata_json={"campaign_id": campaign_id, "adtarget_result": result if isinstance(result, dict) else {"raw": str(result)}},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    actions = []
    if campaign_id:
        actions.append(ChatAction(id="start_campaign", label="Запустить кампанию", kind="runtime", payload={"campaign_id": campaign_id}))
    return AgentResult(
        assistant_message=f"✅ Кампания создана в AdTarget" + (f". ID: **{campaign_id}**" if campaign_id else "."),
        artifacts=[artifact] if artifact else [],
        actions=actions,
    )


async def _save_segment(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    segment = payload.get("segment") or payload.get("content_json") or payload
    content = segment if isinstance(segment, dict) else {"value": segment}
    await ctx.emit("step_started", detail="RuntimeAgent: сохраняю сегмент")
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id, artifact_type="segment_draft",
        content_json=content, metadata_json={}, source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    await ctx.emit("step_completed", detail="segment saved")
    return AgentResult(
        assistant_message="✅ Сегмент сохранён.",
        artifacts=[artifact] if artifact else [],
        actions=[ChatAction(id="build_campaign_from_segment", label="Создать кампанию из сегмента", kind="build", payload={"segment": content})],
    )


async def _save_target_group(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    content = payload if isinstance(payload, dict) else {"value": payload}
    await ctx.emit("step_started", detail="RuntimeAgent: сохраняю таргет-группу")
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id, artifact_type="target_group_draft",
        content_json=content, metadata_json={}, source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    await ctx.emit("step_completed", detail="target group saved")
    return AgentResult(assistant_message="✅ Таргет-группа сохранена.", artifacts=[artifact] if artifact else [])


async def _start_campaign(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    campaign_id = payload.get("campaign_id")
    if not campaign_id:
        return AgentResult(assistant_message="Не указан campaign_id.", status="error")
    await ctx.emit("step_started", detail=f"AdTarget start campaign {campaign_id}")
    try:
        await adtarget.start_campaign(int(campaign_id))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=str(exc)[:200])
        return AgentResult(assistant_message=f"Не удалось запустить кампанию: {str(exc)[:200]}", status="error")
    await ctx.emit("step_completed", detail=f"campaign {campaign_id} started")
    return AgentResult(assistant_message=f"▶ Кампания **{campaign_id}** запущена.")


async def _pause_campaign(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    campaign_id = payload.get("campaign_id")
    if not campaign_id:
        return AgentResult(assistant_message="Не указан campaign_id.", status="error")
    await ctx.emit("step_started", detail=f"AdTarget pause campaign {campaign_id}")
    try:
        await adtarget.pause_campaign(int(campaign_id))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=str(exc)[:200])
        return AgentResult(assistant_message=f"Не удалось поставить на паузу: {str(exc)[:200]}", status="error")
    await ctx.emit("step_completed", detail=f"campaign {campaign_id} paused")
    return AgentResult(assistant_message=f"⏸ Кампания **{campaign_id}** на паузе.")


_DISPATCH = {
    "save_campaign": _save_campaign,
    "save_segment": _save_segment,
    "save_target_group": _save_target_group,
    "start_campaign": _start_campaign,
    "pause_campaign": _pause_campaign,
}


def _extract_campaign_id(result: Any) -> int | None:
    if isinstance(result, dict):
        for key in ("campaignId", "campaign_id", "id"):
            value = result.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return None

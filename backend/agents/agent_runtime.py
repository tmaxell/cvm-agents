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
        assistant_message="Кампания создана в AdTarget" + (f". ID: **{campaign_id}**" if campaign_id else "."),
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
        assistant_message=(
            "Сегмент сохранён. Чтобы использовать его в сборке кампании, "
            "сначала закрепите его как таргет-группу."
        ),
        artifacts=[artifact] if artifact else [],
        actions=[
            ChatAction(
                id="assign_segment_as_target_group",
                label="Назначить таргет-группой",
                kind="runtime",
                payload={"segment": content},
            ),
            ChatAction(
                id="build_campaign_from_segment",
                label="Собрать кампанию для таргет-группы",
                kind="build",
                payload={"segment": content},
            ),
        ],
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
    return AgentResult(assistant_message="Таргет-группа сохранена.", artifacts=[artifact] if artifact else [])


async def _assign_segment_as_target_group(ctx: AgentContext, payload: dict[str, Any]) -> AgentResult:
    """Превращает сгенерированный сегмент в полноценную таргет-группу.

    Если у сегмента уже есть `matched_target_group.target_group_id` (LLM нашёл
    существующую ЦГ) — берём её. Иначе создаём новую через AdTarget mock и
    сохраняем как артефакт `target_group_draft`, чтобы BuilderAgent
    подхватил её при сборке кампании в этой же сессии.
    """
    segment = payload.get("segment")
    if not isinstance(segment, dict):
        segment = ctx.latest_artifact("segment_draft")
        segment = (segment or {}).get("content") if segment else None
    if not isinstance(segment, dict):
        return AgentResult(
            assistant_message="Не нашёл выбранный сегмент. Сначала выберите гипотезу из списка предложенных сегментов.",
            status="needs_input",
        )

    name = (
        segment.get("name")
        or segment.get("title")
        or segment.get("audience_description")
        or "Таргет-группа из сегмента"
    )
    matched = segment.get("matched_target_group") or {}
    existing_tg_id = matched.get("target_group_id") if isinstance(matched, dict) else None
    clients_count = matched.get("clients_count") if isinstance(matched, dict) else None
    if not clients_count:
        clients_count = segment.get("clients_count")

    if existing_tg_id and segment.get("is_existing_target_group"):
        # LLM однозначно сопоставил сегмент с существующей ЦГ — используем её, без записи новой.
        tg_id = int(existing_tg_id)
        tg_name = matched.get("name") or name
        source = "matched_existing"
        await ctx.emit(
            "step_completed",
            detail=f"назначена существующая таргет-группа #{tg_id}",
            metadata={"target_group_id": tg_id, "source": source},
        )
    else:
        # Создаём новую таргет-группу в AdTarget (или mock).
        await ctx.emit("step_started", detail=f"RuntimeAgent: создаю таргет-группу «{str(name)[:60]}»")
        try:
            result = await adtarget.create_target_group(
                name=str(name)[:120],
                criteria=segment.get("selection_criteria") or {},
                clients_count=int(clients_count) if isinstance(clients_count, (int, str)) and str(clients_count).isdigit() else None,
                source_segment_id=str(segment.get("id") or segment.get("name") or "")[:64] or None,
            )
        except Exception as exc:
            await ctx.emit("step_completed", status="error", detail=str(exc)[:200])
            return AgentResult(
                assistant_message=f"Не удалось создать таргет-группу в AdTarget: {str(exc)[:200]}",
                status="error",
            )
        tg_id = int(result.get("id"))
        tg_name = result.get("name") or name
        clients_count = result.get("clientsCount") or clients_count
        source = "created_from_segment"
        await ctx.emit(
            "step_completed",
            detail=f"создана таргет-группа #{tg_id} ({tg_name})",
            metadata={"target_group_id": tg_id, "clients_count": clients_count, "source": source},
        )

    artifact_content = {
        "target_group_id": tg_id,
        "name": str(tg_name),
        "clients_count": int(clients_count) if isinstance(clients_count, (int, float)) and clients_count else None,
        "source": source,
        "source_segment": segment,
    }
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="target_group_draft",
        content_json=artifact_content,
        metadata_json={"source": source, "target_group_id": tg_id},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    reach_text = ""
    if isinstance(clients_count, (int, float)) and clients_count:
        reach_text = f" Размер аудитории: ≈{int(clients_count):,}".replace(",", " ") + " клиентов."
    message = (
        f"Сегмент закреплён как **таргет-группа** «{tg_name}» (id {tg_id}).{reach_text}\n\n"
        "Теперь её можно использовать в сборке кампании — следующая собранная "
        "кампания в этой сессии будет ссылаться на эту таргет-группу автоматически."
    )

    actions = [
        ChatAction(
            id="build_campaign_from_segment",
            label=f"Собрать кампанию для таргет-группы «{str(tg_name)[:32]}»",
            kind="build",
            payload={"segment": segment, "target_group_id": tg_id, "target_group_name": str(tg_name)},
        ),
    ]

    return AgentResult(
        assistant_message=message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"target_group_id": tg_id, "source": source},
    )


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
    return AgentResult(assistant_message=f"Кампания **{campaign_id}** запущена.")


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
    return AgentResult(assistant_message=f"Кампания **{campaign_id}** на паузе.")


_DISPATCH = {
    "save_campaign": _save_campaign,
    "save_segment": _save_segment,
    "save_target_group": _save_target_group,
    "assign_segment_as_target_group": _assign_segment_as_target_group,
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

"""BuilderAgent — сборка кампании.

Стратегия:
1. Template-first: если сообщение совпадает с одним из заготовленных сценариев
   (data_package / gift / demo), возвращаем эталонный flow из examples/.
2. LLM-планировщик: LLM возвращает короткий план шагов (CommonActivity → ... → BT).
   Детерминистический сборщик строит JSON-flow по плану через tools/flow_builder.
3. Фолбэк: минимальный flow (Common → TargetGroup → Push) если и план не получился.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.builder.planner import assemble_flow_from_plan, plan_flow_with_llm
from agents.builder.templates import (
    derive_campaign_name,
    find_template,
    load_template_flow,
)
from schemas import ChatAction
from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_push_communication_activity,
    make_target_group_activity,
)

logger = logging.getLogger(__name__)

NAME = "builder"
DESCRIPTION = "Собирает draft_flow кампании: template-first, иначе LLM-план + детерминистический сборщик."
SUPPORTED_INTENTS = ("build_campaign",)


async def execute(ctx: AgentContext) -> AgentResult:
    goal = ctx.inputs.get("goal") or ctx.message
    seed_segment = ctx.inputs.get("segment")

    await ctx.emit("step_started", detail=f"BuilderAgent: цель — «{_truncate(goal, 80)}»")
    started = time.perf_counter()

    # 1. Template-first.
    template = find_template(goal)
    if template is not None:
        await ctx.emit("step_completed", detail=f"Template: {template.key} ({template.title})", metadata={"template": template.key})
        campaign_name = derive_campaign_name(goal, template.title)
        flow = load_template_flow(template, campaign_name=campaign_name)
        message = (
            f"Подобрал готовый шаблон под этот сценарий: **{template.title}**.\n\n"
            f"{template.description}\n\n"
            f"Кампания: **{campaign_name}**. {len(flow.get('activities') or [])} активностей в потоке. "
            "Откорректируйте параметры или попросите доработать."
        )
        return await _finalize(ctx, flow=flow, message=message, mode="template")

    # 2. LLM-planner + детерминистический сборщик.
    await ctx.emit("step_started", detail="BuilderAgent: LLM-планировщик")
    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx.history[-6:]
        if m.get("role") in {"user", "assistant"}
    ]
    if seed_segment:
        history_pairs.append({"role": "user", "content": f"СЕГМЕНТ: {seed_segment.get('audience_description') or seed_segment.get('name') or seed_segment}"})

    plan = await plan_flow_with_llm(goal, history=history_pairs)
    if plan is not None:
        await ctx.emit("step_completed", detail=f"План: {len(plan['steps'])} шагов", metadata={"steps": len(plan["steps"])})
        try:
            flow = assemble_flow_from_plan(plan)
            campaign_name = plan.get("campaign_name") or "Новая кампания"
            steps_summary = ", ".join(s["type"].replace("Activity", "") for s in plan["steps"][:6])
            message = (
                f"Собрал кампанию **{campaign_name}** по плану из {len(plan['steps'])} шагов: {steps_summary}. "
                "Можно сохранить в AdTarget или продолжить доработку."
            )
            return await _finalize(ctx, flow=flow, message=message, mode="llm_plan")
        except Exception as exc:
            logger.warning("assemble_flow_from_plan failed: %s", exc)
            await ctx.emit("step_started", status="warning", detail=f"Сборка плана упала: {str(exc)[:120]}")
    else:
        await ctx.emit("step_completed", status="warning", detail="LLM план не получился")

    # 3. Fallback: deterministic minimal flow.
    await ctx.emit("step_started", detail="BuilderAgent: detereminist fallback")
    flow = _build_fallback_flow(goal, seed_segment)
    message = (
        "Не получилось вытащить структуру кампании из запроса. Собрал базовый шаблон "
        "**Common → TargetGroup → SMS push**. Уточните аудиторию, оффер и каналы — пересоберу подробнее."
    )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Fallback готов ({latency} ms)")
    return await _finalize(ctx, flow=flow, message=message, mode="fallback")


# ── Финализация / persistence ─────────────────────────────────────────────────

async def _finalize(ctx: AgentContext, *, flow: dict[str, Any], message: str, mode: str) -> AgentResult:
    activities_count = len(flow.get("activities") or [])
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="draft_flow",
        content_json=flow,
        metadata_json={"mode": mode, "activities_count": activities_count},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    actions = [
        ChatAction(
            id="save_campaign",
            label="Сохранить кампанию в AdTarget",
            kind="save",
            payload={"draft_flow": flow},
        ),
        ChatAction(
            id="refine_campaign",
            label="Доработать флоу",
            kind="refine",
            payload={"draft_flow": flow},
        ),
    ]
    return AgentResult(
        assistant_message=message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"mode": mode, "activities_count": activities_count},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _build_fallback_flow(goal: str, seed_segment: dict[str, Any] | None) -> dict[str, Any]:
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
    return assemble_flow([common, target, push])

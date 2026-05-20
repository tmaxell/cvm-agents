"""RefinerAgent — доработка кампании.

Три режима:
1. inputs.campaign_id  → загружает demo_campaigns + health, формирует план фикса.
2. «добавь активность X» (SMS / БТ / Wait / Response / Event / ...) — модифицирует
   последний draft_flow в сессии, добавляя ноду нужного типа в конец цепочки.
3. inputs.draft_flow OR последний draft_flow артефакт сессии → анализ структуры
   и рекомендации по доработке (без модификации).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agents.base import AgentContext, AgentResult
from agents.builder.modify import append_activity_to_flow, detect_add_intent
from agents.builder.modify_llm import apply_modifications, plan_modifications_with_llm
from db import AsyncSessionLocal
from models import CampaignHealthModel, DemoCampaignModel
from schemas import ChatAction

logger = logging.getLogger(__name__)

NAME = "refiner"
DESCRIPTION = "Анализирует кампанию (по id или draft_flow в сессии) и предлагает конкретные доработки."
SUPPORTED_INTENTS = ("refine_campaign",)


async def execute(ctx: AgentContext) -> AgentResult:
    campaign_id = ctx.inputs.get("campaign_id") or _extract_campaign_id(ctx.message)
    draft_flow = ctx.inputs.get("draft_flow")

    await ctx.emit("step_started", detail=f"RefinerAgent: campaign_id={campaign_id}, draft_flow={'yes' if draft_flow else 'no'}")
    started = time.perf_counter()

    if campaign_id:
        result = await _refine_existing(ctx, int(campaign_id))
    else:
        latest = ctx.latest_artifact("draft_flow", "campaign_draft") if not draft_flow else None
        if not draft_flow and latest:
            draft_flow = latest.get("content")

        if not draft_flow:
            await ctx.emit("step_completed", status="warning", detail="Нет campaign_id и draft_flow в сессии")
            return AgentResult(
                assistant_message=(
                    "Не нашёл, что доработать. Скажите id кампании "
                    "(например, «доработай кампанию 24») или сначала создайте черновик."
                ),
                actions=[ChatAction(id="build_campaign", label="Создать новую кампанию", kind="navigate", payload={})],
                status="needs_input",
            )

        # Стратегия (LLM-as-planner + deterministic fallback):
        # 1) LLM-модификатор пытается разобрать запрос и построить план операций.
        # 2) Если LLM вернул ≥1 валидную операцию — применяем и возвращаем updated flow.
        # 3) Иначе пробуем простой deterministic detect_add_intent (на случай «добавь SMS»).
        # 4) В крайнем случае — выдаём аналитические рекомендации без модификации.
        result = await _try_llm_modify(ctx, draft_flow)
        if result is None:
            add_step = detect_add_intent(ctx.message)
            if add_step:
                result = await _append_activity(ctx, draft_flow, add_step)
            else:
                result = await _refine_draft(ctx, draft_flow)

    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"RefinerAgent готов", metadata={"latency_ms": latency})
    return result


# ── Режим 1: доработка существующей кампании по id ────────────────────────────

async def _refine_existing(ctx: AgentContext, campaign_id: int) -> AgentResult:
    async with AsyncSessionLocal() as db:
        result = await db.scalars(
            select(DemoCampaignModel)
            .where(DemoCampaignModel.id == campaign_id)
            .options(selectinload(DemoCampaignModel.health))
        )
        campaign = result.first()

    if campaign is None:
        return AgentResult(
            assistant_message=f"Кампания **{campaign_id}** не найдена в demo_campaigns.",
            status="error",
        )

    health = campaign.health
    spent_ratio = campaign.spent / campaign.budget if campaign.budget else 0
    fixes: list[str] = []
    issues_text: list[str] = []

    if health and health.issues_json:
        for issue in health.issues_json:
            label = issue.get("message") or issue.get("title") or issue.get("code") or str(issue)
            issues_text.append(str(label))
    if health and health.recommended_actions_json:
        for action in health.recommended_actions_json:
            fix = action.get("action") or action.get("title") or str(action)
            fixes.append(str(fix))

    if campaign.open_rate < 12:
        fixes.append("A/B-тест двух заголовков с разной длиной и эмодзи в начале.")
    if campaign.conversion_rate < 3:
        fixes.append("Усилить оффер: добавить персональную скидку или ограничение по времени.")
    if spent_ratio > 0.9:
        fixes.append("Снизить дневной лимит на 20% или перераспределить бюджет на эффективные креативы.")
    if campaign.click_rate < 5 and campaign.open_rate > 15:
        fixes.append("Переписать CTA: явный глагол + выгода в первых 30 символах.")
    if not fixes:
        fixes.append("KPI в норме. Можно расширить аудиторию на 20% и проверить uplift через 7 дней.")

    lines = [f"### Доработка кампании «{campaign.name}» (id {campaign.id})", ""]
    lines.append(f"**Текущее состояние:** канал {campaign.channel}, бюджет {campaign.spent:,}/{campaign.budget:,}, "
                 f"open {campaign.open_rate}%, click {campaign.click_rate}%, CR {campaign.conversion_rate}%.")
    if issues_text:
        lines.append("")
        lines.append("**Проблемы:**")
        for issue in issues_text[:3]:
            lines.append(f"- {issue}")
    lines.append("")
    lines.append("**Рекомендации:**")
    for i, fix in enumerate(fixes[:5], start=1):
        lines.append(f"{i}. {fix}")

    actions = [
        ChatAction(
            id="start_campaign",
            label=f"Запустить «{campaign.name[:24]}» после правок",
            kind="runtime",
            payload={"campaign_id": campaign.id},
        ),
    ]
    return AgentResult(
        assistant_message="\n".join(lines),
        actions=actions,
        metadata={"campaign_id": campaign.id, "fixes": len(fixes)},
    )


# ── Режим 2: доработка draft_flow ─────────────────────────────────────────────

async def _refine_draft(ctx: AgentContext, draft_flow: dict[str, Any]) -> AgentResult:
    activities = (draft_flow or {}).get("activities") or []
    types = [act.get("type") for act in activities]
    suggestions: list[str] = []

    if "EventActivity" not in types and "WaitActivity" not in types:
        suggestions.append("Добавить **EventActivity** для срабатывания по триггеру (например, `package_lifecycle_event`) — поднимет релевантность и снизит спам.")
    if "ResponseActivity" not in types and "InteractiveResponseActivity" not in types:
        suggestions.append("Добавить **ResponseActivity** — иначе нечем измерить целевое действие и uplift.")
    if "PullCommunicationActivity" not in types and any(t == "PushCommunicationActivity" for t in types):
        push_activities = [a for a in activities if a.get("type") == "PushCommunicationActivity"]
        text = ""
        for activity in push_activities:
            for p in (activity.get("content", {}).get("parameters") or []):
                if p.get("name") == "Text":
                    text = str(p.get("value") or "")
                    break
            if text:
                break
        if text and len(text) > 110:
            suggestions.append("Сократить SMS-текст до 110 символов и убрать дублирование, чтобы CTR не падал.")
    if len(activities) < 3:
        suggestions.append("Флоу слишком короткий — рассмотрите многошаговый сценарий с WaitActivity между касаниями.")
    if not suggestions:
        suggestions.append("Флоу выглядит сбалансированно — запустите A/B тест с альтернативным каналом для валидации.")

    lines = [
        f"### Анализ текущего draft flow",
        "",
        f"**Activities ({len(activities)}):** {', '.join(types) or '—'}.",
        "",
        "**Рекомендации:**",
    ]
    for i, s in enumerate(suggestions, start=1):
        lines.append(f"{i}. {s}")

    actions = [
        ChatAction(
            id="save_campaign",
            label="Сохранить кампанию с текущим флоу",
            kind="save",
            payload={"draft_flow": draft_flow},
        ),
    ]
    return AgentResult(
        assistant_message="\n".join(lines),
        actions=actions,
        metadata={"activities_count": len(activities), "suggestions": len(suggestions)},
    )


# ── Режим 3a: LLM-driven модификация (многошаговая, контекстная) ──────────────

async def _try_llm_modify(ctx: AgentContext, draft_flow: dict[str, Any]) -> AgentResult | None:
    """Пытается через LLM построить план операций и применить его к flow.

    Возвращает AgentResult при успехе или None если LLM не дал валидных операций —
    тогда вызывающий код упадёт на deterministic-фолбэк.
    """
    await ctx.emit("step_started", detail="RefinerAgent: LLM-modify planner")
    plan = await plan_modifications_with_llm(
        message=ctx.message,
        flow=draft_flow,
        history=ctx.history,
    )
    if not plan or not plan.get("operations"):
        await ctx.emit("step_completed", status="info", detail="LLM-modify: операций не предложено")
        return None

    new_flow, applied = apply_modifications(draft_flow, plan)
    if new_flow is None or not applied:
        await ctx.emit("step_completed", status="warning", detail="LLM-modify: не удалось применить ни одну операцию")
        return None

    activities_count = len(new_flow.get("activities") or [])
    await ctx.emit(
        "step_completed",
        detail=f"LLM-modify: применено {len(applied)} операций",
        metadata={"ops_count": len(applied), "activities_after": activities_count},
    )

    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="draft_flow",
        content_json=new_flow,
        metadata_json={"mode": "llm_modify", "ops": applied, "activities_count": activities_count},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    summary = (plan.get("summary") or "").strip()
    lines: list[str] = []
    if summary:
        lines.append(summary)
        lines.append("")
    lines.append(f"**Изменения** ({len(applied)}):")
    for descr in applied:
        lines.append(f"- {descr}")
    lines.append("")
    lines.append(f"_Активностей в потоке: {activities_count}._")

    actions = [
        ChatAction(id="save_campaign", label="Сохранить кампанию в AdTarget", kind="save", payload={"draft_flow": new_flow}),
        ChatAction(id="refine_campaign", label="Доработать дальше", kind="refine", payload={"draft_flow": new_flow}),
    ]
    return AgentResult(
        assistant_message="\n".join(lines),
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"mode": "llm_modify", "ops_count": len(applied), "activities_count": activities_count},
    )


# ── Режим 3b: добавление активности (deterministic fallback) ──────────────────

async def _append_activity(ctx: AgentContext, draft_flow: dict[str, Any], step: dict[str, Any]) -> AgentResult:
    await ctx.emit("step_started", detail=f"RefinerAgent: добавляю {step.get('type')}")
    updated = append_activity_to_flow(draft_flow, step)
    if updated is None:
        return AgentResult(
            assistant_message="Не удалось добавить активность — проверьте текущий черновик кампании.",
            status="error",
        )
    activities_count = len(updated.get("activities") or [])
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="draft_flow",
        content_json=updated,
        metadata_json={"mode": "append", "added_type": step.get("type"), "activities_count": activities_count},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    new_node_name = step.get("name") or step.get("type")
    actions = [
        ChatAction(id="save_campaign", label="Сохранить кампанию в AdTarget", kind="save", payload={"draft_flow": updated}),
        ChatAction(id="refine_campaign", label="Доработать дальше", kind="refine", payload={"draft_flow": updated}),
    ]
    return AgentResult(
        assistant_message=(
            f"Добавил **{new_node_name}** в конец цепочки. Активностей теперь: {activities_count}."
        ),
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"activities_count": activities_count, "added_type": step.get("type")},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

import re


def _extract_campaign_id(message: str) -> int | None:
    """Достаёт первое число из сообщения, если оно похоже на campaign_id."""
    if not message:
        return None
    for pattern in (r"кампани[яюейи]?\s*[№#]?\s*(\d+)", r"campaign\s*#?(\d+)", r"\bid\s*[:=]?\s*(\d+)"):
        m = re.search(pattern, message, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None

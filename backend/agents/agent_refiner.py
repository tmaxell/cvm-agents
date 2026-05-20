"""RefinerAgent — доработка кампании.

Два режима:
1. inputs.campaign_id  → загружает demo_campaigns + health, формирует план фикса.
2. inputs.draft_flow OR последний draft_flow артефакт сессии → анализирует структуру
   и предлагает изменения через flow_optimizer.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agents.base import AgentContext, AgentResult
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

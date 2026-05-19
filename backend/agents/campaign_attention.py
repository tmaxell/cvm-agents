"""Campaign attention ranking for monitoring report intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import AsyncSessionLocal
from models import CampaignHealthModel, DemoCampaignModel

_SEVERITY_WEIGHT = {"critical": 100, "high": 70, "medium": 40, "low": 10}


@dataclass(slots=True)
class CampaignAttentionItem:
    campaign_id: int
    campaign_name: str
    what_is_wrong: str
    why_it_matters: str
    suggested_fix: str
    priority_score: int


async def build_campaign_attention_report() -> dict[str, Any]:
    """Return ranked campaigns requiring attention with explicit scoring rationale.

    Formula (higher means more urgent):
    priority_score = severity_weight + (100 - attention_score) + business_impact_score
    where business_impact_score combines spend inefficiency and funnel weakness.
    """

    async with AsyncSessionLocal() as db:
        result = await db.scalars(
            select(DemoCampaignModel)
            .options(selectinload(DemoCampaignModel.health))
            .order_by(DemoCampaignModel.updated_at.desc())
        )
        campaigns = list(result)

    if not campaigns:
        return _fallback_response(
            "Нет кампаний для анализа.",
            [
                "Добавьте кампании в demo_campaigns (id, name, budget, spent, open_rate, click_rate, conversion_rate).",
                "Добавьте health-снимки в campaign_health (attention_score, severity, issues_json, recommended_actions_json).",
            ],
        )

    ranked: list[CampaignAttentionItem] = []
    for campaign in campaigns:
        health = campaign.health
        if health is None:
            continue
        if health.attention_score is None or not health.severity:
            continue

        severity_weight = _SEVERITY_WEIGHT.get(health.severity.lower(), 20)
        attention_penalty = max(0, 100 - int(health.attention_score))
        business_impact_score = _business_impact_score(campaign)
        priority_score = severity_weight + attention_penalty + business_impact_score

        issues = health.issues_json or []
        recommendations = health.recommended_actions_json or []

        ranked.append(
            CampaignAttentionItem(
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                what_is_wrong=_compose_what_is_wrong(health, issues),
                why_it_matters=_compose_why_it_matters(campaign, business_impact_score),
                suggested_fix=_compose_suggested_fix(recommendations),
                priority_score=priority_score,
            )
        )

    if not ranked:
        return _fallback_response(
            "Недостаточно диагностических данных для ранжирования.",
            [
                "Для каждой кампании заполните campaign_health.severity и campaign_health.attention_score.",
                "Добавьте хотя бы одну запись в issues_json и recommended_actions_json.",
            ],
        )

    ranked.sort(key=lambda item: item.priority_score, reverse=True)
    return {
        "status": "ok",
        "ranking_formula": "priority_score = severity_weight + (100 - attention_score) + business_impact_score",
        "severity_weights": _SEVERITY_WEIGHT,
        "campaigns": [
            {
                "campaign_id": item.campaign_id,
                "campaign_name": item.campaign_name,
                "what_is_wrong": item.what_is_wrong,
                "why_it_matters": item.why_it_matters,
                "suggested_fix": item.suggested_fix,
            }
            for item in ranked
        ],
    }


def _business_impact_score(campaign: DemoCampaignModel) -> int:
    spend_ratio = campaign.spent / campaign.budget if campaign.budget else 0
    overspend_risk = max(0, round((spend_ratio - 0.9) * 100))

    low_funnel_penalty = max(0, 20 - campaign.conversion_rate)
    low_engagement_penalty = max(0, 20 - campaign.click_rate)
    return overspend_risk + low_funnel_penalty + low_engagement_penalty


def _compose_what_is_wrong(health: CampaignHealthModel, issues: list[dict[str, Any]]) -> str:
    if issues:
        labels = [str(issue.get("issue") or issue.get("title") or issue) for issue in issues[:2]]
        return f"Severity={health.severity}, attention_score={health.attention_score}. Проблемы: {'; '.join(labels)}"
    return f"Severity={health.severity}, attention_score={health.attention_score}. Обнаружено ухудшение KPI."


def _compose_why_it_matters(campaign: DemoCampaignModel, impact_score: int) -> str:
    return (
        f"Business impact score={impact_score}: spent={campaign.spent}/{campaign.budget}, "
        f"CTR={campaign.click_rate}%, CR={campaign.conversion_rate}%. "
        "Риск потери бюджета и недобора конверсий."
    )


def _compose_suggested_fix(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return "Провести ревизию аудиторий, креативов и лимитов; затем перезапустить A/B тест."
    values = [str(item.get("action") or item.get("title") or item) for item in recommendations[:2]]
    return "; ".join(values)


def _fallback_response(reason: str, missing_data_hints: list[str]) -> dict[str, Any]:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "campaigns": [],
        "suggested_next_steps": missing_data_hints,
    }

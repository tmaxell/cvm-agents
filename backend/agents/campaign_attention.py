"""Анализ кампаний для intent campaign_attention.

Возвращает структурированный отчёт:
- общая статистика портфеля (counts по severity, бюджет, KPI),
- ранжированный список кампаний с человечным описанием проблем,
- разрез по категориям проблем (low_open_rate, budget_burn, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import AsyncSessionLocal
from models import CampaignHealthModel, DemoCampaignModel

_SEVERITY_WEIGHT = {"critical": 100, "high": 70, "medium": 40, "low": 10}
_SEVERITY_LABEL = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}

# Человечные тайтлы и эталонные KPI для каждой известной категории проблем.
_ISSUE_CATALOG: dict[str, dict[str, str]] = {
    "low_open_rate":   {"title": "Низкий open rate",         "target": "open rate ≥ 12%"},
    "budget_burn":     {"title": "Перерасход бюджета",        "target": "burn ≤ план + 10%"},
    "high_unsubscribe":{"title": "Высокий unsubscribe rate",  "target": "unsubscribe ≤ 1.0% / 24h"},
    "low_ctr":         {"title": "Низкий CTR",                "target": "CTR ≥ 5%"},
    "low_conversion":  {"title": "Низкая конверсия",          "target": "CR ≥ 3%"},
}


@dataclass(slots=True)
class CampaignAttentionItem:
    campaign_id: int
    campaign_name: str
    channel: str
    audience_size: int
    budget: int
    spent: int
    open_rate: int
    click_rate: int
    conversion_rate: int
    severity: str
    attention_score: int
    burn_ratio: float
    priority_score: int
    issues: list[dict[str, str]] = field(default_factory=list)
    recommendations: list[dict[str, str]] = field(default_factory=list)


# ── Main entry point ──────────────────────────────────────────────────────────

async def build_campaign_attention_report() -> dict[str, Any]:
    """Возвращает агрегированный отчёт по кампаниям, требующим внимания."""
    async with AsyncSessionLocal() as db:
        result = await db.scalars(
            select(DemoCampaignModel)
            .options(selectinload(DemoCampaignModel.health))
            .order_by(DemoCampaignModel.updated_at.desc())
        )
        campaigns_db = list(result)

    if not campaigns_db:
        return _fallback_response(
            "Нет кампаний для анализа.",
            [
                "Заполните demo_campaigns (name, channel, audience_size, budget, spent, open/click/conversion rate).",
                "Добавьте health-снимки в campaign_health (attention_score, severity, issues_json, recommended_actions_json).",
            ],
        )

    ranked: list[CampaignAttentionItem] = []
    skipped_no_health = 0
    for campaign in campaigns_db:
        health = campaign.health
        if health is None or health.attention_score is None or not health.severity:
            skipped_no_health += 1
            continue
        burn_ratio = (campaign.spent / campaign.budget) if campaign.budget else 0.0
        priority = _SEVERITY_WEIGHT.get(health.severity.lower(), 20) \
            + max(0, 100 - int(health.attention_score)) \
            + _business_impact_score(campaign)
        ranked.append(CampaignAttentionItem(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            channel=campaign.channel,
            audience_size=campaign.audience_size,
            budget=campaign.budget,
            spent=campaign.spent,
            open_rate=campaign.open_rate,
            click_rate=campaign.click_rate,
            conversion_rate=campaign.conversion_rate,
            severity=health.severity.lower(),
            attention_score=int(health.attention_score),
            burn_ratio=burn_ratio,
            priority_score=priority,
            issues=list(health.issues_json or []),
            recommendations=list(health.recommended_actions_json or []),
        ))

    if not ranked:
        return _fallback_response(
            "Недостаточно диагностических данных для ранжирования.",
            [
                "Заполните campaign_health.severity и campaign_health.attention_score.",
                "Добавьте записи в issues_json и recommended_actions_json.",
            ],
        )

    ranked.sort(key=lambda item: item.priority_score, reverse=True)

    return {
        "status": "ok",
        "summary": _portfolio_summary(ranked, skipped_no_health=skipped_no_health),
        "issue_categories": _issue_breakdown(ranked),
        "campaigns": [_serialize_item(item) for item in ranked],
        "ranking_formula": "priority = severity_weight + (100 - attention_score) + business_impact_score",
    }


# ── Aggregates ────────────────────────────────────────────────────────────────

def _portfolio_summary(items: list[CampaignAttentionItem], *, skipped_no_health: int) -> dict[str, Any]:
    total = len(items)
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in items:
        counts[item.severity] = counts.get(item.severity, 0) + 1

    needs_attention = [it for it in items if it.severity in {"critical", "high"}]
    healthy = [it for it in items if it.severity in {"low", "medium"}]

    budget_total = sum(it.budget for it in items)
    spent_total = sum(it.spent for it in items)
    burn_ratio = spent_total / budget_total if budget_total else 0.0

    return {
        "total": total,
        "skipped_no_health": skipped_no_health,
        "by_severity": counts,
        "needs_attention_count": len(needs_attention),
        "healthy_count": len(healthy),
        "budget_total": budget_total,
        "spent_total": spent_total,
        "burn_ratio": round(burn_ratio, 3),
        "kpi_all":     _avg_kpi(items),
        "kpi_problem": _avg_kpi(needs_attention),
        "kpi_healthy": _avg_kpi(healthy),
    }


def _avg_kpi(items: list[CampaignAttentionItem]) -> dict[str, float]:
    if not items:
        return {"open_rate": 0.0, "click_rate": 0.0, "conversion_rate": 0.0}
    n = len(items)
    return {
        "open_rate":       round(sum(it.open_rate for it in items) / n, 1),
        "click_rate":      round(sum(it.click_rate for it in items) / n, 1),
        "conversion_rate": round(sum(it.conversion_rate for it in items) / n, 1),
    }


def _issue_breakdown(items: list[CampaignAttentionItem]) -> list[dict[str, Any]]:
    """Группирует проблемы по коду, считает покрытие и связанные кампании."""
    buckets: dict[str, dict[str, Any]] = {}
    for item in items:
        for issue in item.issues:
            code = str(issue.get("code") or "other")
            bucket = buckets.setdefault(code, {
                "code": code,
                "title": _ISSUE_CATALOG.get(code, {}).get("title", code),
                "target": _ISSUE_CATALOG.get(code, {}).get("target", ""),
                "messages": set(),
                "affected_count": 0,
                "affected_ids": [],
                "channels": {},
            })
            msg = issue.get("message")
            if msg:
                bucket["messages"].add(msg)
            bucket["affected_count"] += 1
            bucket["affected_ids"].append(item.campaign_id)
            bucket["channels"][item.channel] = bucket["channels"].get(item.channel, 0) + 1

    breakdown: list[dict[str, Any]] = []
    for bucket in buckets.values():
        breakdown.append({
            "code": bucket["code"],
            "title": bucket["title"],
            "target": bucket["target"],
            "affected_count": bucket["affected_count"],
            "affected_ids": bucket["affected_ids"][:10],
            "channels": bucket["channels"],
            "messages": sorted(bucket["messages"])[:3],
        })
    breakdown.sort(key=lambda b: b["affected_count"], reverse=True)
    return breakdown


def _business_impact_score(campaign: DemoCampaignModel) -> int:
    burn = (campaign.spent / campaign.budget) if campaign.budget else 0.0
    overspend_risk = max(0, round((burn - 0.9) * 100))
    low_funnel = max(0, 20 - campaign.conversion_rate)
    low_engagement = max(0, 20 - campaign.click_rate)
    return overspend_risk + low_funnel + low_engagement


def _serialize_item(item: CampaignAttentionItem) -> dict[str, Any]:
    return {
        "campaign_id": item.campaign_id,
        "campaign_name": item.campaign_name,
        "channel": item.channel,
        "audience_size": item.audience_size,
        "budget": item.budget,
        "spent": item.spent,
        "burn_ratio": round(item.burn_ratio, 3),
        "open_rate": item.open_rate,
        "click_rate": item.click_rate,
        "conversion_rate": item.conversion_rate,
        "severity": item.severity,
        "severity_label": _SEVERITY_LABEL.get(item.severity, item.severity),
        "attention_score": item.attention_score,
        "priority_score": item.priority_score,
        "issues": [
            {
                "code": str(i.get("code") or ""),
                "title": _ISSUE_CATALOG.get(str(i.get("code") or ""), {}).get("title", str(i.get("code") or "issue")),
                "message": str(i.get("message") or ""),
            }
            for i in item.issues
        ],
        "recommendations": [
            {
                "action": str(r.get("action") or ""),
                "details": str(r.get("details") or ""),
            }
            for r in item.recommendations
        ],
    }


def _fallback_response(reason: str, hints: list[str]) -> dict[str, Any]:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "summary": None,
        "issue_categories": [],
        "campaigns": [],
        "suggested_next_steps": hints,
    }

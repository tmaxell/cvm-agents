"""Анализ кампаний по операционным метрикам платформы AdTarget.

Метрики берём такие же, какие показаны на дашбордах платформы (см.
examples/01-06 - Dashboards*): доставка сообщений, задержки, тайм-ауты
событий/откликов, блокировки, очереди обработки. Никаких маркетинговых
KPI (open/CTR/CR) — их в платформе не видно, агент не должен на них
опираться.

Отчёт содержит:
- сводку портфеля (по статусам и каналам, доставка/задержки в среднем);
- разрез по типам операционных проблем;
- ранжированный список кампаний с конкретными показателями и
  рекомендованными техническими действиями.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import AsyncSessionLocal
from models import CampaignHealthModel, DemoCampaignModel


# Severity → веса приоритета. Чем критичнее, тем выше в топе.
_SEVERITY_WEIGHT = {"critical": 100, "high": 70, "medium": 40, "low": 10}

# Каталог операционных проблем с человекочитаемыми названиями и базовыми SLA.
_ISSUE_CATALOG: dict[str, dict[str, str]] = {
    "delivery_failure_high": {
        "title": "Высокий процент недоставки",
        "target": "failure rate ≤ 5% для SMS / ≤ 10% для push",
    },
    "delivery_latency_high": {
        "title": "Высокие задержки доставки",
        "target": "≤ 5% сообщений с задержкой > 300с, p95 латентности < 60с",
    },
    "low_delivery_rate": {
        "title": "Низкий уровень доставки",
        "target": "delivery rate ≥ 90% за 24 ч",
    },
    "event_timeout": {
        "title": "Тайм-аут событий",
        "target": "интервал между событиями ≤ 60 мин",
    },
    "response_timeout": {
        "title": "Тайм-аут обработки откликов",
        "target": "ответ обработан в течение 60 мин",
    },
    "queue_lag": {
        "title": "Отставание очереди обработки",
        "target": "lag consumer'а ≤ 15 мин",
    },
    "blocked_by_system": {
        "title": "Кампания заблокирована",
        "target": "статус running без блокировок",
    },
    "no_traffic": {
        "title": "Нет трафика за 24 часа",
        "target": "хотя бы 1 отправка за сутки для running-кампании",
    },
}


@dataclass(slots=True)
class CampaignAttentionItem:
    campaign_id: int
    campaign_name: str
    status: str
    channel: str
    campaign_kind: str
    audience_size: int
    severity: str
    attention_score: int
    priority_score: int
    # Операционные метрики (соответствуют полям CampaignHealthModel).
    messages_sent_24h: int
    delivery_rate_pct: float
    delivery_failure_rate_pct: float
    slow_delivery_share_pct: float
    p95_delivery_latency_sec: int
    event_lag_minutes: int | None
    response_lag_minutes: int | None
    queue_lag_minutes: int | None
    last_traffic_at: str | None
    blocked_reason: str | None
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
                "Засеять demo_campaigns + campaign_health "
                "(см. scripts/seed_demo_campaigns.py).",
            ],
        )

    ranked: list[CampaignAttentionItem] = []
    skipped_no_health = 0
    for campaign in campaigns_db:
        health = campaign.health
        if health is None or not health.severity:
            skipped_no_health += 1
            continue
        priority = _priority_score(campaign, health)
        ranked.append(CampaignAttentionItem(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            status=campaign.status,
            channel=campaign.channel,
            campaign_kind=campaign.campaign_kind,
            audience_size=campaign.audience_size,
            severity=health.severity.lower(),
            attention_score=int(health.attention_score),
            priority_score=priority,
            messages_sent_24h=int(health.messages_sent_24h or 0),
            delivery_rate_pct=float(health.delivery_rate_pct or 0.0),
            delivery_failure_rate_pct=float(health.delivery_failure_rate_pct or 0.0),
            slow_delivery_share_pct=float(health.slow_delivery_share_pct or 0.0),
            p95_delivery_latency_sec=int(health.p95_delivery_latency_sec or 0),
            event_lag_minutes=health.event_lag_minutes,
            response_lag_minutes=health.response_lag_minutes,
            queue_lag_minutes=health.queue_lag_minutes,
            last_traffic_at=_iso(health.last_traffic_at),
            blocked_reason=health.blocked_reason,
            issues=list(health.issues_json or []),
            recommendations=list(health.recommended_actions_json or []),
        ))

    if not ranked:
        return _fallback_response(
            "Недостаточно диагностических данных для ранжирования.",
            ["Заполните campaign_health.severity и операционные метрики."],
        )

    ranked.sort(key=lambda item: item.priority_score, reverse=True)

    return {
        "status": "ok",
        "summary": _portfolio_summary(ranked, skipped_no_health=skipped_no_health),
        "issue_categories": _issue_breakdown(ranked),
        "campaigns": [_serialize_item(item) for item in ranked],
        "ranking_formula": (
            "priority = severity_weight + (100 - attention_score) + operational_pressure"
        ),
    }


# ── Aggregates ────────────────────────────────────────────────────────────────

def _portfolio_summary(items: list[CampaignAttentionItem], *, skipped_no_health: int) -> dict[str, Any]:
    """Сводка портфеля: статусы, каналы, средние операционные показатели."""
    total = len(items)
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    by_status: dict[str, int] = {}
    by_channel: dict[str, dict[str, Any]] = {}

    for item in items:
        by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
        by_status[item.status] = by_status.get(item.status, 0) + 1
        ch = by_channel.setdefault(
            item.channel,
            {"count": 0, "delivery_rate_sum": 0.0, "p95_latency_sum": 0, "sent_sum": 0},
        )
        ch["count"] += 1
        ch["delivery_rate_sum"] += item.delivery_rate_pct
        ch["p95_latency_sum"] += item.p95_delivery_latency_sec
        ch["sent_sum"] += item.messages_sent_24h

    channels = []
    for name, agg in sorted(by_channel.items(), key=lambda kv: -kv[1]["count"]):
        n = max(1, agg["count"])
        channels.append({
            "channel": name,
            "count": agg["count"],
            "avg_delivery_rate_pct": round(agg["delivery_rate_sum"] / n, 1),
            "avg_p95_latency_sec": round(agg["p95_latency_sum"] / n, 1),
            "messages_sent_24h": agg["sent_sum"],
        })

    needs_attention = [it for it in items if it.severity in {"critical", "high"}]
    healthy = [it for it in items if it.severity == "low"]
    running = [it for it in items if it.status == "running"]
    blocked = [it for it in items if it.status == "blocked"]
    sent_total = sum(it.messages_sent_24h for it in items)
    sent_by_running = sum(it.messages_sent_24h for it in running)
    if running:
        avg_delivery_rate = round(sum(it.delivery_rate_pct for it in running) / len(running), 1)
    else:
        avg_delivery_rate = 0.0

    return {
        "total": total,
        "skipped_no_health": skipped_no_health,
        "by_severity": by_severity,
        "by_status": by_status,
        "needs_attention_count": len(needs_attention),
        "healthy_count": len(healthy),
        "running_count": len(running),
        "blocked_count": len(blocked),
        "messages_sent_24h_total": sent_total,
        "messages_sent_24h_running": sent_by_running,
        "avg_delivery_rate_running_pct": avg_delivery_rate,
        "channels": channels,
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


def _priority_score(campaign: DemoCampaignModel, health: CampaignHealthModel) -> int:
    """Чем больше — тем выше кампания в топе.

    severity_weight + (100 - attention_score) даёт грубое ранжирование;
    operational_pressure добавляет «срочность» из конкретных метрик:
    blocked / no_traffic с длинным простоем / большой queue_lag.
    """
    base = _SEVERITY_WEIGHT.get((health.severity or "low").lower(), 10)
    score_gap = max(0, 100 - int(health.attention_score or 0))

    pressure = 0
    if campaign.status == "blocked":
        pressure += 50
    if (health.messages_sent_24h or 0) == 0 and campaign.status == "running":
        pressure += 35
    failure = float(health.delivery_failure_rate_pct or 0.0)
    pressure += min(40, int(failure * 2))
    if (health.queue_lag_minutes or 0) > 15:
        pressure += min(30, int(health.queue_lag_minutes - 15))
    if (health.event_lag_minutes or 0) > 60:
        pressure += min(40, int((health.event_lag_minutes - 60) / 3))
    if (health.response_lag_minutes or 0) > 60:
        pressure += min(40, int((health.response_lag_minutes - 60) / 3))
    return base + score_gap + pressure


# ── Serialization ─────────────────────────────────────────────────────────────

def _serialize_item(item: CampaignAttentionItem) -> dict[str, Any]:
    return {
        "campaign_id": item.campaign_id,
        "campaign_name": item.campaign_name,
        "status": item.status,
        "channel": item.channel,
        "campaign_kind": item.campaign_kind,
        "audience_size": item.audience_size,
        "severity": item.severity,
        "attention_score": item.attention_score,
        "priority_score": item.priority_score,
        "messages_sent_24h": item.messages_sent_24h,
        "delivery_rate_pct": round(item.delivery_rate_pct, 2),
        "delivery_failure_rate_pct": round(item.delivery_failure_rate_pct, 2),
        "slow_delivery_share_pct": round(item.slow_delivery_share_pct, 2),
        "p95_delivery_latency_sec": item.p95_delivery_latency_sec,
        "event_lag_minutes": item.event_lag_minutes,
        "response_lag_minutes": item.response_lag_minutes,
        "queue_lag_minutes": item.queue_lag_minutes,
        "last_traffic_at": item.last_traffic_at,
        "blocked_reason": item.blocked_reason,
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


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _fallback_response(reason: str, hints: list[str]) -> dict[str, Any]:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "summary": None,
        "issue_categories": [],
        "campaigns": [],
        "suggested_next_steps": hints,
    }

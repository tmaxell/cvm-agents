"""AttentionAgent — портфельный обзор кампаний и план фикса топ-N.

Метрики и категории проблем — операционные: доставка, задержки,
тайм-ауты событий/откликов, очереди обработки, блокировки. Это те же
показатели, что видны на дашбордах платформы AdTarget (examples/01-06).
Маркетинговые KPI (open/CTR/CR) намеренно не используются — их в
платформенных дашбордах нет.

Структура ответа:
1. Сводка портфеля (по статусам и каналам, доставка/задержки).
2. Топ-N кампаний — конкретные операционные показатели + диагноз и
   технический план фикса (LLM, если доступен, иначе detereminist).
3. Разрез по категориям операционных проблем.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentContext, AgentResult
from agents.campaign_attention import build_campaign_attention_report
from llm import get_llm
from schemas import ChatAction

logger = logging.getLogger(__name__)

NAME = "attention"
DESCRIPTION = "Анализирует портфель кампаний, считает агрегаты по severity и проблемам, формирует план фикса топ-5."
SUPPORTED_INTENTS = ("campaign_attention",)

_TOP_N = 5
_CHANNEL_LABEL = {
    "sms_push": "SMS push", "push": "Push", "email_push": "Email push",
    "ussd_push": "USSD push", "text_push": "Text push", "json_push": "Json push",
    "ussd_pull": "USSD pull", "json_pull": "Json pull", "text_pull": "Text pull",
}
_STATUS_LABEL = {
    "running": "running", "paused": "paused", "blocked": "blocked",
    "draft": "draft", "completed": "completed",
}


async def execute(ctx: AgentContext) -> AgentResult:
    await ctx.emit("step_started", detail="AttentionAgent: загружаю demo_campaigns + campaign_health")
    started = time.perf_counter()
    report = await build_campaign_attention_report()
    latency_db = int((time.perf_counter() - started) * 1000)

    if report.get("status") != "ok":
        reason = report.get("reason", "")
        hints = report.get("suggested_next_steps") or []
        msg = "Нет данных для анализа портфеля кампаний."
        if reason:
            msg += f"\n\n{reason}"
        if hints:
            msg += "\n\nНужные шаги:\n" + "\n".join(f"- {h}" for h in hints)
        await ctx.emit("step_completed", status="warning", detail="insufficient_data")
        return AgentResult(assistant_message=msg, status="ok")

    summary = report["summary"]
    categories = report["issue_categories"]
    campaigns = report["campaigns"]
    top = campaigns[:_TOP_N]

    await ctx.emit(
        "step_completed",
        detail=f"Получено кампаний: {summary['total']} (внимания: {summary['needs_attention_count']})",
        metadata={"latency_ms": latency_db, "total": summary["total"], "needs_attention": summary["needs_attention_count"]},
    )

    # LLM-обогащённый план для топ-N. Если LLM упал — fallback на детерминированный синтез.
    await ctx.emit("step_started", detail=f"AttentionAgent: формулирую план фикса топ-{len(top)}")
    started_llm = time.perf_counter()
    enriched = await _llm_enriched_plans(top, summary)
    latency_llm = int((time.perf_counter() - started_llm) * 1000)
    await ctx.emit(
        "step_completed",
        detail=f"План фикса собран ({'LLM' if enriched.get('llm') else 'detereminist'})",
        metadata={"latency_ms": latency_llm, "llm": bool(enriched.get('llm'))},
    )

    message = _render_markdown(summary=summary, top=top, categories=categories, plans=enriched["plans"], all_count=len(campaigns))

    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="attention_report",
        content_json={"summary": summary, "issue_categories": categories, "campaigns": campaigns},
        metadata_json={"top_n": len(top), "llm_used": bool(enriched.get("llm"))},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    actions: list[ChatAction] = [
        ChatAction(
            id="refine_campaign",
            label=f"Доработать «{_short(item['campaign_name'], 26)}»",
            kind="refine",
            payload={"campaign_id": item["campaign_id"], "campaign_name": item["campaign_name"]},
        )
        for item in top[:3]
    ]

    return AgentResult(
        assistant_message=message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"total": summary["total"], "needs_attention": summary["needs_attention_count"]},
    )


# ── Markdown rendering ────────────────────────────────────────────────────────

def _render_markdown(*, summary: dict[str, Any], top: list[dict[str, Any]], categories: list[dict[str, Any]],
                    plans: dict[int, dict[str, Any]], all_count: int) -> str:
    out: list[str] = []

    # Сводка портфеля
    by_sev = summary["by_severity"]
    by_status = summary.get("by_status", {})
    out.append("## Обзор портфеля")
    out.append("")
    out.append(
        f"Всего кампаний: **{summary['total']}** · "
        f"running: **{summary.get('running_count', 0)}** · "
        f"требуют внимания: **{summary['needs_attention_count']}** · "
        f"в норме: **{summary['healthy_count']}** · "
        f"заблокировано: **{summary.get('blocked_count', 0)}**."
    )
    out.append("")
    out.append("**По критичности:**")
    for sev_key in ("critical", "high", "medium", "low"):
        out.append(f"- {sev_key}: **{by_sev.get(sev_key, 0)}**")
    out.append("")
    if by_status:
        status_line = " · ".join(
            f"{_STATUS_LABEL.get(s, s)}: **{n}**" for s, n in sorted(by_status.items(), key=lambda x: -x[1])
        )
        out.append(f"**По статусу:** {status_line}.")
        out.append("")

    # Доставка по портфелю
    out.append(
        f"**Доставка за 24ч:** отправлено **{_count(summary['messages_sent_24h_total'])}** сообщений "
        f"(running — **{_count(summary['messages_sent_24h_running'])}**); "
        f"средний delivery rate на running: **{summary['avg_delivery_rate_running_pct']}%**."
    )
    out.append("")

    # Каналы — таблицей коротко
    if summary.get("channels"):
        out.append("**По каналам:**")
        for ch in summary["channels"][:6]:
            out.append(
                f"- {_channel(ch['channel'])}: кампаний **{ch['count']}**, "
                f"delivery rate ≈ **{ch['avg_delivery_rate_pct']}%**, "
                f"p95 латентности ≈ **{ch['avg_p95_latency_sec']}с**, "
                f"отправлено **{_count(ch['messages_sent_24h'])}**"
            )
        out.append("")

    # Топ-N
    out.append(f"## Топ-{len(top)} требуют действий сейчас")
    out.append("")
    for idx, item in enumerate(top, start=1):
        plan = plans.get(item["campaign_id"], {})
        out.append(
            f"### {idx}. {item['campaign_name']} · id {item['campaign_id']}  ·  "
            f"{item['severity']}  ·  {_STATUS_LABEL.get(item['status'], item['status'])}"
        )
        out.append("")
        out.append(_metrics_line(item))
        out.append("")
        if item["issues"]:
            out.append("**Что не так:**")
            for issue in item["issues"][:3]:
                title = issue.get("title") or issue.get("code") or "issue"
                message = issue.get("message") or ""
                if message:
                    out.append(f"- **{title}** — {message}")
                else:
                    out.append(f"- **{title}**")
            out.append("")
        if item.get("blocked_reason"):
            out.append(f"**Причина блокировки:** {item['blocked_reason']}")
            out.append("")
        analysis = plan.get("analysis") or _fallback_analysis(item)
        actions = plan.get("actions") or _fallback_actions(item)
        if analysis:
            out.append(f"**Диагноз:** {analysis}")
            out.append("")
        if actions:
            out.append("**План фикса:**")
            for action in actions[:5]:
                out.append(f"- {action}")
            out.append("")

    # Разрез по проблемам
    if categories:
        out.append("## Разрез по проблемам")
        out.append("")
        for cat in categories[:8]:
            channels_str = ", ".join(
                f"{_channel(ch)} ×{n}" for ch, n in sorted(cat["channels"].items(), key=lambda x: -x[1])
            )
            line = f"- **{cat['title']}** · затронуто кампаний: **{cat['affected_count']}**"
            if cat.get("target"):
                line += f" · ожидаем: {cat['target']}"
            if channels_str:
                line += f" · по каналам: {channels_str}"
            out.append(line)
            if cat.get("affected_ids"):
                preview_ids = ", ".join(f"#{cid}" for cid in cat["affected_ids"][:6])
                out.append(f"  - id: {preview_ids}")
        out.append("")

    out.append(
        f"Проанализировано {all_count} кампаний с health-снимком. "
        "Доступны действия «Доработать» по топу — откроется технический разбор."
    )
    return "\n".join(out).strip()


def _metrics_line(item: dict[str, Any]) -> str:
    """Компактная строка операционных метрик кампании."""
    parts = [
        f"канал {_channel(item['channel'])}",
        f"тип {item.get('campaign_kind', '?')}",
        f"аудитория {_audience(item['audience_size'])}",
        f"отправлено за 24ч {_count(item['messages_sent_24h'])}",
        f"delivery rate {item['delivery_rate_pct']}%",
        f"failure rate {item['delivery_failure_rate_pct']}%",
    ]
    if item.get("slow_delivery_share_pct", 0):
        parts.append(f"slow >300с: {item['slow_delivery_share_pct']}%")
    if item.get("p95_delivery_latency_sec", 0):
        parts.append(f"p95 латентности {item['p95_delivery_latency_sec']}с")
    if item.get("event_lag_minutes") is not None:
        parts.append(f"event lag {item['event_lag_minutes']} мин")
    if item.get("response_lag_minutes") is not None:
        parts.append(f"response lag {item['response_lag_minutes']} мин")
    if item.get("queue_lag_minutes") is not None and item["queue_lag_minutes"] > 0:
        parts.append(f"queue lag {item['queue_lag_minutes']} мин")
    parts.append(f"attention {item['attention_score']}/100")
    return ", ".join(parts) + "."


# ── LLM enrichment ────────────────────────────────────────────────────────────

_LLM_SYSTEM = """Ты — инженер сопровождения CVM-платформы (AdTarget). По JSON
с операционным состоянием топ-кампаний выдай для каждой технический разбор.
Маркетинговых терминов (CTR, конверсия, бюджет) НЕ используй — их нет
в платформенных дашбордах. Опирайся только на операционные сигналы:
- статус (running / blocked / paused),
- delivery_rate_pct, delivery_failure_rate_pct, slow_delivery_share_pct,
  p95_delivery_latency_sec,
- event_lag_minutes, response_lag_minutes, queue_lag_minutes,
- messages_sent_24h, blocked_reason, issues[].code.

Верни JSON ровно такого вида:
{
  "plans": {
    "<campaign_id>": {
      "analysis": "<2-3 предложения: корневая операционная причина и почему срочно>",
      "actions": ["<техническое действие в повелительном наклонении>", ...]
    }
  }
}

Требования:
- analysis опирается на конкретные цифры (доставка/задержки/lag/статус).
- actions — 3 пункта, конкретные технические действия (проверить consumer,
  снять блокировку, поднять параллелизм, проверить канал и т.п.), без воды.
- НЕ повторяй сухой текст из stored_recommendations — переформулируй
  и углубляй с учётом цифр.
- Только JSON, никакого markdown."""


async def _llm_enriched_plans(top: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    if not top:
        return {"plans": {}, "llm": False}
    try:
        llm = get_llm(temperature=0.2)
        compact_top = [
            {
                "campaign_id": item["campaign_id"],
                "name": item["campaign_name"],
                "status": item["status"],
                "channel": item["channel"],
                "campaign_kind": item.get("campaign_kind"),
                "severity": item["severity"],
                "attention_score": item["attention_score"],
                "messages_sent_24h": item["messages_sent_24h"],
                "delivery_rate_pct": item["delivery_rate_pct"],
                "delivery_failure_rate_pct": item["delivery_failure_rate_pct"],
                "slow_delivery_share_pct": item["slow_delivery_share_pct"],
                "p95_delivery_latency_sec": item["p95_delivery_latency_sec"],
                "event_lag_minutes": item.get("event_lag_minutes"),
                "response_lag_minutes": item.get("response_lag_minutes"),
                "queue_lag_minutes": item.get("queue_lag_minutes"),
                "blocked_reason": item.get("blocked_reason"),
                "issues": [
                    {"code": i["code"], "title": i.get("title", ""), "message": i.get("message", "")}
                    for i in item["issues"]
                ],
                "stored_recommendations": [r["action"] for r in item["recommendations"] if r.get("action")],
            }
            for item in top
        ]
        prompt = {
            "portfolio_summary": {
                "total": summary["total"],
                "by_severity": summary["by_severity"],
                "by_status": summary.get("by_status"),
                "running_count": summary.get("running_count"),
                "blocked_count": summary.get("blocked_count"),
                "avg_delivery_rate_running_pct": summary.get("avg_delivery_rate_running_pct"),
                "channels_overview": summary.get("channels", [])[:6],
            },
            "top_campaigns": compact_top,
        }
        result = await llm.ainvoke([
            SystemMessage(content=_LLM_SYSTEM),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
        ])
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        plans = _parse_plans(text)
        if plans:
            return {"plans": plans, "llm": True}
    except Exception as exc:
        logger.warning("AttentionAgent LLM enrichment failed: %s", exc)
    return {"plans": {}, "llm": False}


def _parse_plans(text: str) -> dict[int, dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    # Найдём первый JSON-объект целиком.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Иногда вокруг JSON остаются комментарии; попробуем выкусить.
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace < 0 or last_brace < 0 or last_brace <= first_brace:
            return {}
        try:
            payload = json.loads(raw[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            return {}
    plans_raw = payload.get("plans") if isinstance(payload, dict) else None
    if not isinstance(plans_raw, dict):
        return {}
    plans: dict[int, dict[str, Any]] = {}
    for key, value in plans_raw.items():
        try:
            cid = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        actions = value.get("actions") or []
        actions = [str(a).strip() for a in actions if isinstance(a, (str, int, float)) and str(a).strip()]
        plans[cid] = {
            "analysis": str(value.get("analysis") or "").strip(),
            "actions": actions[:5],
        }
    return plans


# ── Fallbacks ─────────────────────────────────────────────────────────────────

def _fallback_analysis(item: dict[str, Any]) -> str:
    """Краткий технический диагноз из метрик, когда LLM недоступен."""
    bits: list[str] = []
    bits.append(f"severity={item['severity']}, attention {item['attention_score']}/100")
    if item["status"] == "blocked":
        reason = item.get("blocked_reason") or "причина не указана"
        bits.append(f"кампания заблокирована ({reason})")
    if item["delivery_failure_rate_pct"] > 5:
        bits.append(f"failure rate {item['delivery_failure_rate_pct']}%")
    if item["slow_delivery_share_pct"] > 3:
        bits.append(f"{item['slow_delivery_share_pct']}% сообщений с задержкой >300с")
    if (item.get("event_lag_minutes") or 0) > 60:
        bits.append(f"события не приходят {item['event_lag_minutes']} мин")
    if (item.get("response_lag_minutes") or 0) > 60:
        bits.append(f"отклики копятся {item['response_lag_minutes']} мин")
    if (item.get("queue_lag_minutes") or 0) > 15:
        bits.append(f"consumer отстаёт на {item['queue_lag_minutes']} мин")
    if item["status"] == "running" and item["messages_sent_24h"] == 0:
        bits.append("за 24ч не отправлено ни одного сообщения")
    return "; ".join(bits) + "."


def _fallback_actions(item: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for rec in item["recommendations"][:3]:
        text = rec.get("action", "")
        details = rec.get("details", "")
        if details:
            text = f"{text} — {details}" if text else details
        if text:
            actions.append(text)
    if not actions:
        actions = [
            "Проверить состояние канального провайдера и очередь отправки.",
            "Сверить параметры события / отклика и состояние consumer'а в Kafka.",
            "Перезапустить кампанию после устранения причины.",
        ]
    return actions


# ── Format helpers ────────────────────────────────────────────────────────────

def _count(value: int | float) -> str:
    n = int(value or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}М"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _audience(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def _channel(code: str) -> str:
    return _CHANNEL_LABEL.get((code or "").lower(), code or "—")


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"

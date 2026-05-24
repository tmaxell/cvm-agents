"""AttentionAgent — портфельный обзор кампаний и план фикса топ-N.

Структура ответа:
1. Executive summary (счётчики, бюджет, KPI портфеля).
2. Топ-5 кампаний — детальный разбор каждой с LLM-обогащённым планом.
3. Разрез по категориям проблем (low_open_rate, budget_burn, …).
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
_CHANNEL_LABEL = {"sms": "SMS", "push": "Push", "email": "Email", "ussd": "USSD"}


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

    # Executive summary
    by_sev = summary["by_severity"]
    out.append("## Обзор портфеля")
    out.append("")
    out.append(
        f"Всего кампаний: **{summary['total']}** · "
        f"требуют внимания: **{summary['needs_attention_count']}** · "
        f"в норме: **{summary['healthy_count']}**."
    )
    out.append("")
    out.append("**По severity:**")
    for sev_key, label in [("critical", "critical"), ("high", "high"), ("medium", "medium"), ("low", "low")]:
        out.append(f"- {label}: **{by_sev.get(sev_key, 0)}**")
    out.append("")

    # Бюджет + KPI
    burn_pct = round(summary["burn_ratio"] * 100)
    out.append(
        f"**Бюджет:** освоено **{_money(summary['spent_total'])}** из **{_money(summary['budget_total'])}** "
        f"(**{burn_pct}%** burn)."
    )
    kpi_all = summary["kpi_all"]
    kpi_prob = summary["kpi_problem"]
    kpi_ok = summary["kpi_healthy"]
    out.append(
        f"**KPI портфеля:** open **{kpi_all['open_rate']}%** · click **{kpi_all['click_rate']}%** · CR **{kpi_all['conversion_rate']}%**."
    )
    out.append(
        f"**Проблемные кампании в среднем:** open {kpi_prob['open_rate']}% / click {kpi_prob['click_rate']}% / CR {kpi_prob['conversion_rate']}%. "
        f"**Здоровые:** open {kpi_ok['open_rate']}% / click {kpi_ok['click_rate']}% / CR {kpi_ok['conversion_rate']}%."
    )
    out.append("")

    # Топ-N
    out.append(f"## Топ-{len(top)} требуют действий сейчас")
    out.append("")
    for idx, item in enumerate(top, start=1):
        plan = plans.get(item["campaign_id"], {})
        out.append(f"### {idx}. {item['campaign_name']} · id {item['campaign_id']}  ·  {item['severity_label']}")
        out.append("")
        # Снапшот метрик строкой
        burn_item_pct = round(item["burn_ratio"] * 100)
        out.append(
            f"канал {_channel(item['channel'])}, аудитория {_audience(item['audience_size'])}, бюджет {_money(item['spent'])}/{_money(item['budget'])} ({burn_item_pct}%), open {item['open_rate']}% / click {item['click_rate']}% / CR {item['conversion_rate']}%, attention {item['attention_score']}"
        )
        out.append("")
        # Проблемы
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
        # План фикса (LLM или fallback)
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

    # Issue breakdown
    if categories:
        out.append("## Разрез по проблемам")
        out.append("")
        for cat in categories[:6]:
            channels_str = ", ".join(
                f"{_channel(ch)} ×{n}" for ch, n in sorted(cat["channels"].items(), key=lambda x: -x[1])
            )
            line = f"- **{cat['title']}** · затронуто кампаний: **{cat['affected_count']}**"
            if cat.get("target"):
                line += f" · цель: {cat['target']}"
            if channels_str:
                line += f" · по каналам: {channels_str}"
            out.append(line)
            if cat.get("affected_ids"):
                preview_ids = ", ".join(f"#{cid}" for cid in cat["affected_ids"][:6])
                out.append(f"  - id: {preview_ids}")
        out.append("")

    out.append(f"Проанализировано {all_count} кампаний с health-снимком. Доступны действия «Доработать» по топу.")
    return "\n".join(out).strip()


# ── LLM enrichment ────────────────────────────────────────────────────────────

_LLM_SYSTEM = """Ты — CVM-аналитик. По JSON-данным портфеля и топ-кампаний верни JSON ровно такого вида:

{
  "plans": {
    "<campaign_id>": {
      "analysis": "<2-3 предложения: корневая причина и почему сейчас критично, на русском>",
      "actions": ["<глагол + конкретное действие 1>", "<действие 2>", "<действие 3>"]
    },
    ...
  }
}

Требования:
- analysis опирается на цифры конкретной кампании (open, click, CR, burn).
- actions — 3 пункта, конкретные, в повелительном наклонении, без воды.
- НЕ повторяй сухой текст из recommendations, переформулируй и углубляй.
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
                "channel": item["channel"],
                "severity": item["severity"],
                "attention_score": item["attention_score"],
                "burn_ratio": item["burn_ratio"],
                "audience_size": item["audience_size"],
                "open_rate": item["open_rate"],
                "click_rate": item["click_rate"],
                "conversion_rate": item["conversion_rate"],
                "issues": [{"code": i["code"], "title": i.get("title", ""), "message": i.get("message", "")} for i in item["issues"]],
                "stored_recommendations": [r["action"] for r in item["recommendations"] if r.get("action")],
            }
            for item in top
        ]
        prompt = {
            "portfolio_summary": {
                "total": summary["total"],
                "by_severity": summary["by_severity"],
                "burn_ratio": summary["burn_ratio"],
                "kpi_problem": summary["kpi_problem"],
                "kpi_healthy": summary["kpi_healthy"],
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
    burn_pct = round(item["burn_ratio"] * 100)
    return (
        f"Severity={item['severity']}, attention {item['attention_score']}/100. "
        f"При burn {burn_pct}% воронка просела: open {item['open_rate']}%, click {item['click_rate']}%, CR {item['conversion_rate']}%."
    )


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
            "Сузить аудиторию до недавно активного сегмента.",
            "Перепроверить триггеры частоты и контактных политик.",
            "Запустить A/B тест нового оффера.",
        ]
    return actions


# ── Format helpers ────────────────────────────────────────────────────────────

def _money(value: int | float) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


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

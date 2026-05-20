"""AttentionAgent — кампании, требующие внимания."""

from __future__ import annotations

import time
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.campaign_attention import build_campaign_attention_report
from schemas import ChatAction


NAME = "attention"
DESCRIPTION = "Анализирует demo_campaigns + campaign_health и возвращает топ кампаний по приоритету фикса."
SUPPORTED_INTENTS = ("campaign_attention",)


async def execute(ctx: AgentContext) -> AgentResult:
    await ctx.emit("step_started", detail="AttentionAgent: ранжирование кампаний по риску")
    started = time.perf_counter()
    report = await build_campaign_attention_report()
    latency = int((time.perf_counter() - started) * 1000)
    campaigns: list[dict[str, Any]] = report.get("campaigns") or []
    await ctx.emit(
        "step_completed",
        detail=f"AttentionAgent: получено {len(campaigns)} кампаний",
        metadata={"latency_ms": latency, "count": len(campaigns)},
    )

    if not campaigns:
        reason = report.get("reason", "")
        hints = report.get("suggested_next_steps") or []
        msg = "Кампаний, требующих внимания, не найдено."
        if reason:
            msg += f"\n\n_{reason}_"
        if hints:
            msg += "\n\nЧто можно сделать:\n" + "\n".join(f"- {h}" for h in hints)
        return AgentResult(assistant_message=msg, status="ok")

    top = campaigns[:5]
    lines = [f"**Топ кампаний, требующих внимания** ({len(campaigns)}):", ""]
    for item in top:
        lines.append(f"### {item['campaign_name']} (id {item['campaign_id']})")
        lines.append("")
        lines.append(f"- ⚠️ {_clean(item.get('what_is_wrong'))}")
        lines.append(f"- 💰 {_clean(item.get('why_it_matters'))}")
        lines.append(f"- 🔧 {_clean(item.get('suggested_fix'))}")
        lines.append("")
    message = "\n".join(lines).rstrip()

    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="attention_report",
        content_json={"campaigns": campaigns, "formula": report.get("ranking_formula")},
        metadata_json={"top_n": len(top)},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    actions: list[ChatAction] = [
        ChatAction(
            id="refine_campaign",
            label=f"Доработать «{_truncate(item['campaign_name'], 28)}»",
            kind="refine",
            payload={"campaign_id": item["campaign_id"], "campaign_name": item["campaign_name"]},
        )
        for item in top[:3]
    ]

    return AgentResult(
        assistant_message=message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"campaigns_total": len(campaigns)},
    )


def _clean(text: Any) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split())


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"

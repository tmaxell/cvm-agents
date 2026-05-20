"""SegmentsAgent — генерация гипотез сегментов аудитории."""

from __future__ import annotations

import re
import time

from agents.base import AgentContext, AgentResult
from agents.segment_agent import suggest_segments
from schemas import ChatAction, SegmentSuggestRequest


NAME = "segments"
DESCRIPTION = "Предлагает 2-3 гипотезы сегментов под бизнес-цель кампании."
SUPPORTED_INTENTS = ("suggest_segments",)


async def execute(ctx: AgentContext) -> AgentResult:
    message = ctx.message
    product = _extract_product(message) or "general"

    await ctx.emit("step_started", detail=f"SegmentsAgent: product={product}")
    started = time.perf_counter()
    try:
        response = await suggest_segments(SegmentSuggestRequest(product=product, campaign_goal=message))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=f"segment_agent failed: {str(exc)[:200]}")
        return AgentResult(
            assistant_message=f"Не удалось сгенерировать сегменты: {str(exc)[:200]}",
            status="error",
        )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Гипотез: {len(response.hypotheses)}", metadata={"latency_ms": latency})

    if not response.hypotheses:
        return AgentResult(
            assistant_message=response.summary or "Не удалось предложить сегменты для этого запроса.",
            status="ok",
        )

    lines = [response.summary or "Предлагаю несколько гипотез сегментов:", ""]
    for h in response.hypotheses[:3]:
        name = h.name or h.title or "Без названия"
        lines.append(f"### {name}")
        lines.append("")
        desc = h.audience_description or h.description
        reason = h.relevance_reason or h.rationale
        if desc:
            lines.append(f"- 👥 {desc}")
        if reason:
            lines.append(f"- 🎯 {reason}")
        if h.risk_or_limitation:
            lines.append(f"- ⚠️ {h.risk_or_limitation}")
        lines.append("")
    message_text = "\n".join(lines).rstrip()

    primary = response.hypotheses[0]
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="segment_draft",
        content_json=primary.model_dump(),
        metadata_json={"hypotheses_count": len(response.hypotheses)},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    actions = [
        ChatAction(id="save_segment", label="Сохранить сегмент", kind="save",
                   payload={"segment": primary.model_dump()}),
        ChatAction(id="build_campaign_from_segment", label="Создать кампанию из сегмента",
                   kind="build", payload={"segment": primary.model_dump()}),
    ]
    return AgentResult(
        assistant_message=message_text,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"hypotheses": len(response.hypotheses)},
    )


def _extract_product(message: str) -> str | None:
    if not message:
        return None
    m = re.search(r"продукт[:\s]+([^.,;\n]+)", message, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"product[:\s]+([^.,;\n]+)", message, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None

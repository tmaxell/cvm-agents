"""DocsAgent — ответы на вопросы по документации через RAG copilot."""

from __future__ import annotations

import time

from agents.base import AgentContext, AgentResult
from agents.qa_copilot import answer as copilot_answer
from schemas import CopilotRequest


NAME = "docs"
DESCRIPTION = "Отвечает на вопросы по документации платформы CVM через RAG + LLM."
SUPPORTED_INTENTS = ("documentation_qa",)


async def execute(ctx: AgentContext) -> AgentResult:
    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx.history[-6:]
        if m.get("role") in {"user", "assistant"}
    ]

    await ctx.emit("step_started", detail="DocsAgent: RAG + LLM ответ")
    started = time.perf_counter()
    try:
        response = await copilot_answer(CopilotRequest(question=ctx.message, history=history_pairs))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=str(exc)[:200])
        return AgentResult(
            assistant_message=f"Не удалось получить ответ от RAG copilot: {str(exc)[:200]}",
            status="error",
        )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit(
        "step_completed",
        detail=f"Источников: {len(response.citations)}",
        metadata={"latency_ms": latency, "citations": len(response.citations)},
    )

    text = response.answer
    if response.citations:
        text += "\n\n**Источники:**\n"
        for c in response.citations[:5]:
            text += f"- {c.title or c.source}\n"
    return AgentResult(assistant_message=text.strip(), metadata={"citations": len(response.citations)})

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

    await ctx.emit("step_started", detail="DocsAgent: RAG retrieval")
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
        detail=f"DocsAgent: цитат {len(response.citations)}",
        metadata={"latency_ms": latency, "citations": len(response.citations)},
    )

    citations = [
        {
            "id": c.id,
            "title": c.title,
            "source": c.source,
            "heading_path": list(c.heading_path or []),
            "score": float(c.score or 0.0),
        }
        for c in response.citations[:6]
    ]
    return AgentResult(
        assistant_message=response.answer.strip(),
        metadata={"citations": citations},
    )

"""Supervisor — точка входа мультиагентной системы.

Сценарий:
1. Если в context есть explicit action — диспатчим в RuntimeAgent
   (для save/start/pause) или в специализированный агент (refine_campaign,
   build_campaign_from_segment) по id action.
2. Иначе классифицируем intent (rules + LLM), строим план и выполняем шаги.

Эмитим trace-события на каждом этапе через ctx.emit().
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import AgentContext, AgentResult, Plan, PlanStep
from agents.chat_orchestrator import IntentDecision, classify_intent
from agents.registry import agent_for_intent, get_agent
from schemas import ChatAction

logger = logging.getLogger(__name__)


# Какие action.id маршрутизируются к каким агентам / intents.
_ACTION_DISPATCH: dict[str, tuple[str, dict[str, str] | None]] = {
    # Runtime actions — все идут в RuntimeAgent.
    "save_campaign":      ("runtime", None),
    "save_segment":       ("runtime", None),
    "save_target_group":  ("runtime", None),
    "assign_segment_as_target_group": ("runtime", None),
    "start_campaign":     ("runtime", None),
    "pause_campaign":     ("runtime", None),
    # Бизнес actions — идут в специализированные агенты.
    "refine_campaign":         ("refiner",  {"campaign_id": "campaign_id", "draft_flow": "draft_flow"}),
    "build_campaign":          ("builder",  {"message": "goal"}),
    "build_campaign_from_segment": ("builder", {"segment": "segment"}),
    "apply_segment":           ("builder",  {"segment": "segment"}),
    # clarify_reply — frontend особый: фронт превращает payload.message в обычное сообщение.
    # Если всё-таки прилетит сюда — передадим payload.message как goal в builder.
    "clarify_reply":           ("builder",  {"message": "goal"}),
}


async def handle(context: AgentContext) -> AgentResult:
    """Главный entry point для /api/chat."""
    if context.action is not None:
        return await _handle_action(context)
    return await _handle_message(context)


# ── Action dispatch ───────────────────────────────────────────────────────────

async def _handle_action(ctx: AgentContext) -> AgentResult:
    action = ctx.action
    assert action is not None
    await ctx.emit("plan_created", detail=f"Action requested: {action.id}", metadata={"action_id": action.id})

    route = _ACTION_DISPATCH.get(action.id)
    if route is None:
        return AgentResult(
            assistant_message=f"Неизвестное действие: {action.id}",
            status="error",
        )

    agent_name, input_map = route
    agent = get_agent(agent_name)
    if agent is None:
        return AgentResult(assistant_message=f"Агент `{agent_name}` не зарегистрирован.", status="error")

    # Прокидываем нужные поля payload в ctx.inputs.
    inputs: dict[str, Any] = {}
    payload = action.payload or {}
    if input_map:
        for src_key, dst_key in input_map.items():
            if src_key == "message":
                inputs[dst_key] = ctx.message
                continue
            if src_key in payload:
                inputs[dst_key] = payload[src_key]
    inputs.setdefault("action_id", action.id)

    ctx.inputs.update(inputs)
    return await agent.execute(ctx)


# ── Message classification + plan ─────────────────────────────────────────────

async def _handle_message(ctx: AgentContext) -> AgentResult:
    # Sticky-context: если последний ассистент-ответ просил уточнения (stage=collect_brief),
    # продолжаем разговор в Builder, не запуская intent classifier на «короткий ответ».
    sticky_agent = _detect_sticky_agent(ctx.history)
    if sticky_agent:
        await ctx.emit(
            "plan_created",
            detail=f"sticky-context: продолжаем в {sticky_agent}",
            metadata={"sticky": sticky_agent},
        )
        agent = get_agent(sticky_agent)
        if agent is not None:
            ctx.inputs.setdefault("goal", ctx.message)
            return await agent.execute(ctx)

    decision = await classify_intent(ctx.message, history=ctx.history)
    await ctx.emit(
        "plan_created",
        detail=f"intent={decision.intent} confidence={decision.confidence:.2f} ({decision.reason})",
        metadata={"intent": decision.intent, "confidence": decision.confidence},
    )

    plan = _build_plan(decision)
    if not plan.steps:
        return AgentResult(assistant_message="Не удалось построить план выполнения.", status="error")

    # Выполняем план последовательно. В большинстве сценариев — один шаг.
    last_result: AgentResult | None = None
    for step in plan.steps:
        agent = get_agent(step.agent)
        if agent is None:
            await ctx.emit("step_completed", status="error", detail=f"agent `{step.agent}` not registered")
            return AgentResult(assistant_message=f"Агент `{step.agent}` не зарегистрирован.", status="error")
        ctx.inputs.update(step.inputs)
        last_result = await agent.execute(ctx)
        if last_result.status == "error":
            return last_result
        # Если шаг произвёл draft_flow — добавим его в артефакты контекста для следующих шагов.
        for artifact in last_result.artifacts:
            ctx.artifacts.append(artifact)

    return last_result or AgentResult(assistant_message="Шагов не выполнено.", status="error")


def _detect_sticky_agent(history: list[dict[str, Any]]) -> str | None:
    """Если последний ассистент ждёт уточнений — возвращает имя агента для продолжения."""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        meta = msg.get("metadata") or {}
        agent_meta = meta.get("agent_meta") or {}
        if isinstance(agent_meta, dict) and agent_meta.get("stage") == "collect_brief":
            return "builder"
        # Если последний ответ был от builder/refiner и status был needs_input — sticky.
        # Сохраняем как простой эвристический сигнал: первый assistant идёт.
        break
    return None


def _build_plan(decision: IntentDecision) -> Plan:
    """Простой mapper intent → один шаг.

    Будущая точка расширения: для сложных запросов можно разбить на несколько шагов
    (например, suggest_segments → build_campaign_from_first_segment).
    """
    intent = decision.intent
    agent = agent_for_intent(intent)
    if agent is None:
        agent = get_agent("docs")
        intent = "documentation_qa"
    return Plan(
        intent=intent,  # type: ignore[arg-type]
        summary=decision.reason or intent,
        steps=[PlanStep(agent=agent.name, description=agent.description, inputs={})],
    )

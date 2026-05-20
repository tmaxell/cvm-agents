"""BuilderAgent — сборка кампании по запросу пользователя.

Стратегия (мультиагентный подход):
1. Template-first — если запрос явно совпадает с одним из эталонных сценариев
   (data_package / gift / demo), возвращаем готовый шаблон.
2. Brief analyzer — LLM извлекает структурированный бриф из истории.
3. Если критичные поля отсутствуют (product / channels / audience) — отдаём
   needs_input с конкретными уточняющими вопросами и НЕ строим flow.
4. Если бриф готов — LLM-планировщик строит план шагов; детерминистический
   сборщик собирает валидный JSON flow.
5. Фолбэк — простой 3-нодный flow если LLM-план не получился.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.builder.brief import CampaignBriefAnalysis, analyze_brief, is_ready_to_build
from agents.builder.planner import assemble_flow_from_plan, plan_flow_with_llm
from agents.builder.templates import (
    derive_campaign_name,
    find_template,
    load_template_flow,
)
from schemas import ChatAction
from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_push_communication_activity,
    make_target_group_activity,
)

logger = logging.getLogger(__name__)

NAME = "builder"
DESCRIPTION = "Собирает draft_flow кампании: template-first → brief analyzer → LLM-план → детерминистический сборщик."
SUPPORTED_INTENTS = ("build_campaign",)


async def execute(ctx: AgentContext) -> AgentResult:
    goal = ctx.inputs.get("goal") or ctx.message
    seed_segment = ctx.inputs.get("segment")

    await ctx.emit("step_started", detail=f"BuilderAgent: цель — «{_truncate(goal, 80)}»")
    started = time.perf_counter()

    # 1. Template-first.
    template = find_template(goal)
    if template is not None:
        await ctx.emit("step_completed", detail=f"Template: {template.key} ({template.title})", metadata={"template": template.key})
        campaign_name = derive_campaign_name(goal, template.title)
        flow = load_template_flow(template, campaign_name=campaign_name)
        message = (
            f"Подобрал готовый шаблон под этот сценарий: **{template.title}**.\n\n"
            f"{template.description}\n\n"
            f"Кампания: **{campaign_name}**. {len(flow.get('activities') or [])} активностей в потоке. "
            "Откорректируйте параметры или попросите доработать."
        )
        return await _finalize(ctx, flow=flow, message=message, mode="template")

    # 2. Brief analyzer.
    await ctx.emit("step_started", detail="BuilderAgent: анализ брифа")
    brief = await analyze_brief(goal, ctx.history)
    if seed_segment and isinstance(seed_segment, dict):
        # Если бриф пришёл из сегмент-агента — расширяем audience из сегмента.
        if not brief.audience.get("description"):
            audience_desc = (
                seed_segment.get("audience_description")
                or seed_segment.get("description")
                or seed_segment.get("name")
            )
            if audience_desc:
                brief.audience = {"description": str(audience_desc)[:160]}
    brief_dict = _brief_to_dict(brief)
    await ctx.emit(
        "step_completed",
        detail=f"Бриф: product={brief.product or '?'} channels={brief.channels or '?'} scenario={brief.scenario}",
        metadata={"missing": brief.missing_critical, "confidence": brief.confidence},
    )

    # 3. Если критичные поля отсутствуют — уточняем.
    if not is_ready_to_build(brief) and brief.missing_critical:
        return await _ask_clarifying(ctx, goal=goal, brief=brief)

    # 4. LLM-план + детерминистический сборщик.
    await ctx.emit("step_started", detail="BuilderAgent: LLM-планировщик с брифом")
    history_pairs = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx.history[-6:]
        if m.get("role") in {"user", "assistant"}
    ]
    plan = await plan_flow_with_llm(goal, history=history_pairs, brief=brief_dict)
    if plan is not None:
        try:
            flow = assemble_flow_from_plan(plan)
            campaign_name = plan.get("campaign_name") or brief.product or "Новая кампания"
            summary = plan.get("summary") or ""
            steps_summary = ", ".join(
                s["type"].replace("Activity", "") for s in plan["steps"][:8]
            )
            message_lines = [
                f"Собрал кампанию **{campaign_name}** ({len(plan['steps'])} шагов).",
            ]
            if summary:
                message_lines.append(f"_{summary}_")
            message_lines.append("")
            message_lines.append(f"**План:** {steps_summary}")
            if brief.notes:
                message_lines.append("")
                message_lines.append("**Что учтено:**")
                for note in brief.notes[:3]:
                    message_lines.append(f"- {note}")
            await ctx.emit(
                "step_completed",
                detail=f"План: {len(plan['steps'])} шагов",
                metadata={"steps": len(plan["steps"]), "summary": summary[:100]},
            )
            return await _finalize(ctx, flow=flow, message="\n".join(message_lines), mode="llm_plan", brief=brief)
        except Exception as exc:
            logger.warning("assemble_flow_from_plan failed: %s", exc)
            await ctx.emit("step_started", status="warning", detail=f"Сборка плана упала: {str(exc)[:120]}")
    else:
        await ctx.emit("step_completed", status="warning", detail="LLM план не получился")

    # 5. Fallback.
    await ctx.emit("step_started", detail="BuilderAgent: detereminist fallback")
    flow = _build_fallback_flow(goal, brief)
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Fallback готов ({latency} ms)")
    message = (
        "Не получилось вытащить структуру кампании из запроса. Собрал базовый шаблон "
        "**Common → TargetGroup → SMS push**. Уточните аудиторию, оффер и каналы — пересоберу подробнее."
    )
    return await _finalize(ctx, flow=flow, message=message, mode="fallback", brief=brief)


# ── Clarifying questions ──────────────────────────────────────────────────────

async def _ask_clarifying(ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis) -> AgentResult:
    """Возвращает запрос пользователю с уточняющими вопросами. Flow ещё не собираем."""
    await ctx.emit(
        "step_started",
        detail=f"BuilderAgent: запрашиваю уточнения — {', '.join(brief.missing_critical)}",
    )
    questions = brief.clarifying_questions or _default_questions(brief.missing_critical)
    known_lines: list[str] = []
    if brief.product:
        known_lines.append(f"- Продукт: **{brief.product}**.")
    if brief.channels:
        known_lines.append(f"- Каналы: **{', '.join(brief.channels)}**.")
    if brief.audience and brief.audience.get("description"):
        known_lines.append(f"- Аудитория: **{brief.audience['description']}**.")
    if brief.goal:
        known_lines.append(f"- Цель: {brief.goal}.")

    parts = ["Прежде чем собрать кампанию, нужно уточнить несколько деталей."]
    if known_lines:
        parts.append("")
        parts.append("**Уже понятно:**")
        parts.extend(known_lines)
    parts.append("")
    parts.append("**Уточните, пожалуйста:**")
    for q in questions[:4]:
        parts.append(f"- {q}")

    await ctx.emit("step_completed", detail=f"Задано {len(questions)} вопросов", metadata={"missing": brief.missing_critical})

    # Quick-reply кнопки для самых частых ответов.
    quick_actions: list[ChatAction] = []
    if "channels" in brief.missing_critical:
        quick_actions.append(_quick_reply("SMS-канал", "Используем SMS как основной канал коммуникации."))
        quick_actions.append(_quick_reply("Email-канал", "Используем Email как основной канал."))
        quick_actions.append(_quick_reply("Push-канал", "Используем мобильные Push-уведомления."))
    if "audience" in brief.missing_critical:
        quick_actions.append(_quick_reply("Все активные клиенты", "Аудитория — все активные клиенты за последние 30 дней."))

    return AgentResult(
        assistant_message="\n".join(parts),
        actions=quick_actions,
        status="needs_input",
        metadata={"brief": _brief_to_dict(brief), "missing": brief.missing_critical, "stage": "collect_brief"},
    )


def _default_questions(missing: list[str]) -> list[str]:
    catalog = {
        "product": "Какой продукт / тариф / услугу продвигаем?",
        "channels": "Через какой канал отправляем коммуникацию: SMS, Push, Email или USSD?",
        "audience": "На какую аудиторию (целевая группа или критерий)?",
        "goal": "Какая бизнес-цель: продажа, удержание, повторная активация?",
    }
    return [catalog[m] for m in missing if m in catalog]


def _quick_reply(label: str, message: str) -> ChatAction:
    """Action типа quick-reply — клик отправляет message как обычный текст пользователя.

    Frontend трактует ACTION_LABELS[id] как «текст для отправки» при клике на action.
    Чтобы не мутить supervisor.action_dispatch, делаем action id='clarify_reply' и payload.message.
    Supervisor для неизвестных id вернёт ошибку, поэтому frontend сам обрабатывает clarify_reply
    как plain message.
    """
    return ChatAction(id="clarify_reply", label=label, kind="text", payload={"message": message})


# ── Финализация / persistence ─────────────────────────────────────────────────

async def _finalize(
    ctx: AgentContext,
    *,
    flow: dict[str, Any],
    message: str,
    mode: str,
    brief: CampaignBriefAnalysis | None = None,
) -> AgentResult:
    activities_count = len(flow.get("activities") or [])
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="draft_flow",
        content_json=flow,
        metadata_json={
            "mode": mode,
            "activities_count": activities_count,
            "brief": _brief_to_dict(brief) if brief else None,
        },
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    actions = [
        ChatAction(
            id="save_campaign",
            label="Сохранить кампанию в AdTarget",
            kind="save",
            payload={"draft_flow": flow},
        ),
        ChatAction(
            id="refine_campaign",
            label="Доработать флоу",
            kind="refine",
            payload={"draft_flow": flow},
        ),
    ]
    return AgentResult(
        assistant_message=message,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata={"mode": mode, "activities_count": activities_count},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _brief_to_dict(brief: CampaignBriefAnalysis | None) -> dict[str, Any] | None:
    if brief is None:
        return None
    data = asdict(brief)
    return data


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _build_fallback_flow(goal: str, brief: CampaignBriefAnalysis | None) -> dict[str, Any]:
    campaign_name = (brief.product if brief and brief.product else None) or _truncate(goal, 60) or "Новая кампания"
    audience_hint = ""
    if brief and brief.audience:
        audience_hint = str(brief.audience.get("description") or "")
    content_type = "SmsContent"
    if brief and brief.channels:
        mapping = {"sms": "SmsContent", "email": "EmailContent", "push": "PushContent", "ussd": "UssdContent"}
        content_type = mapping.get(brief.channels[0], "SmsContent")
    common = make_common_activity(campaign_name)
    target = make_target_group_activity(target_group_id=1)
    if audience_hint:
        target["name"] = f"Аудитория — {_truncate(audience_hint, 40)}"
    push = make_push_communication_activity(
        channel_id=1,
        content_type=content_type,
        message_text=f"Привет! Специальное предложение по «{campaign_name}». Подробности уточняйте.",
    )
    return assemble_flow([common, target, push])

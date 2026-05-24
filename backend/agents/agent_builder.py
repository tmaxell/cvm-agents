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
import re
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
from tools import adtarget

# Регулярка «пользователь хочет, чтобы аудиторию подобрали за него».
_RECOMMEND_RE = re.compile(
    r"\b(порекоменд|рекоменд|подбер|предлож|подскаж|сам[аи]?\s+(подбер|выбер)|recommend|suggest)",
    re.IGNORECASE,
)


def _wants_recommendation(message: str) -> bool:
    return bool(message and _RECOMMEND_RE.search(message))


# Ведущие филлеры, которые срезаем из описания аудитории
# («нет, давай лучше я сам опишу: молодые семьи» → «молодые семьи»).
_AUDIENCE_FILLER_RE = re.compile(
    r"^(нет[,.]?\s*|да[,.]?\s*|давай[,.]?\s*|лучше\s+|пусть\s+|хочу\s+|я\s+|сам[аи]?\s+"
    r"|опиш[уыие]\w*\s*|укаж[уыие]\w*\s*|скаж[уыие]\w*\s*|отбер[уые]\w*\s*|это\s+|аудитори\w*\s*[:—-]?\s*)+",
    re.IGNORECASE,
)


# Регулярка «пользователь просит сгенерировать оффер».
_OFFER_GEN_RE = re.compile(
    r"\b(сгенерир|придум|подбер|напиш|дай)\w*\s+(\w+\s+){0,3}(оффер|вариант|вариант\w*\s+оффер|текст\w*)",
    re.IGNORECASE,
)
_OFFER_SKIP_RE = re.compile(
    r"\b(не\s+нужно|пропуст\w*|без\s+оффер|типовой|стандартн\w*|по\s+умолчанию)",
    re.IGNORECASE,
)


def _wants_offer_generation(message: str) -> bool:
    return bool(message and _OFFER_GEN_RE.search(message))


def _wants_skip_offer(message: str) -> bool:
    return bool(message and _OFFER_SKIP_RE.search(message))


def _clean_audience_description(message: str) -> str:
    """Достаёт «чистое» описание аудитории из реплики пользователя.

    Срезает разговорные филлеры и берёт текст после двоеточия, если оно есть
    («…я сам опишу: молодые семьи 25-35» → «молодые семьи 25-35»).
    """
    text = (message or "").strip()
    if not text:
        return ""
    if ":" in text:
        tail = text.split(":", 1)[1].strip()
        if len(tail) >= 5:
            text = tail
    cleaned = _AUDIENCE_FILLER_RE.sub("", text).strip()
    return cleaned or text
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

    # 0a. Если пришли с сегментом из SegmentsAgent и в сессии ещё нет таргет-группы —
    #     автоматически закрепляем сегмент как таргет-группу. Так дальше по флоу
    #     BuilderAgent уже работает с сущностью таргет-группа, а не с сегментом.
    if seed_segment and isinstance(seed_segment, dict):
        if _resolve_target_group(ctx) == (None, None):
            await _auto_promote_segment(ctx, seed_segment)

    # 0b. select_offer action: пользователь выбрал вариант оффера из меню OfferAgent —
    #     сохраняем выбор как артефакт offer_choice, дальше сборка подхватит текст.
    if ctx.action is not None and ctx.action.id == "select_offer":
        await _save_offer_choice(ctx)

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
    # Если в сессии уже есть таргет-группа — берём её описание как audience по умолчанию.
    tg_id_pre, tg_name_pre = _resolve_target_group(ctx)
    if tg_name_pre and not brief.audience.get("description"):
        brief.audience = {"description": str(tg_name_pre)[:160]}
    elif seed_segment and isinstance(seed_segment, dict):
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

    # Если таргет-группа уже выбрана/назначена в сессии — больше не считаем
    # «audience» отсутствующим критичным полем.
    if tg_id_pre and "audience" in brief.missing_critical:
        brief.missing_critical = [m for m in brief.missing_critical if m != "audience"]

    # 2.5. Резолв аудитории.
    sticky_stage = ctx.inputs.get("sticky_stage")
    action_id = ctx.action.id if ctx.action is not None else None
    chosen_method = ctx.inputs.get("audience_method")

    # A1. Пользователь выбрал конкретный метод подбора → запускаем сегментацию им.
    if (action_id == "audience_method" or chosen_method) and brief.product and not tg_id_pre:
        return await _recommend_audience(ctx, goal=goal, brief=brief, method=chosen_method)

    # A2. Пользователь попросил рекомендацию (кнопка или «порекомендуй» в режиме
    #     collect_audience) — показываем меню методов подбора.
    wants_rec = action_id == "recommend_audience" or (
        sticky_stage == "collect_audience" and _wants_recommendation(ctx.message)
    )
    if wants_rec and brief.product and not tg_id_pre:
        return await _ask_audience_method(ctx, goal=goal, brief=brief)

    # B. Пользователь в режиме collect_audience описал аудиторию сам —
    #    закрепляем описание как полноценную таргет-группу (сценарий завершается
    #    сохранением ТГ), дальше сборка кампании пойдёт уже с ней.
    if sticky_stage == "collect_audience" and not tg_id_pre:
        described = _clean_audience_description(ctx.message)
        if described and not _wants_recommendation(ctx.message):
            await _auto_promote_segment(ctx, {
                "name": _truncate(described, 80),
                "audience_description": described,
                "selection_criteria": {},
                "is_existing_target_group": False,
            })
            tg_id_pre, tg_name_pre = _resolve_target_group(ctx)
            if tg_name_pre:
                brief.audience = {"description": tg_name_pre}
                brief.missing_critical = [m for m in brief.missing_critical if m != "audience"]

    # C. Аудитории по-прежнему не хватает, продукт известен, ТГ не выбрана —
    #    предлагаем выбор «порекомендовать / описать самому».
    if "audience" in brief.missing_critical and brief.product and not tg_id_pre:
        return await _ask_audience_choice(ctx, goal=goal, brief=brief)

    # 3. Если критичные поля отсутствуют — уточняем.
    if not is_ready_to_build(brief) and brief.missing_critical:
        return await _ask_clarifying(ctx, goal=goal, brief=brief)

    # 3.5. Шаг оффера. Если в сессии нет выбранного оффера и пользователь не
    # просил пропустить — предлагаем сгенерировать варианты. Если попросил
    # сгенерировать — делегируем в OfferAgent.
    offer_text = _resolve_offer_text(ctx)
    skip_offer = ctx.inputs.get("offer_mode") == "skip" or action_id == "skip_offer_generation"
    wants_offers = action_id == "generate_offers" or (
        sticky_stage == "collect_offer" and _wants_offer_generation(ctx.message)
    )
    if not offer_text and not skip_offer:
        if wants_offers:
            return await _generate_offers(ctx, goal=goal, brief=brief)
        return await _ask_offer_decision(ctx, goal=goal, brief=brief)

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
            tg_id, tg_name = _resolve_target_group(ctx)
            flow = assemble_flow_from_plan(
                plan, target_group_id=tg_id, target_group_name=tg_name, offer_text=offer_text,
            )
            campaign_name = plan.get("campaign_name") or brief.product or "Новая кампания"
            summary = plan.get("summary") or ""
            steps_summary = ", ".join(
                s["type"].replace("Activity", "") for s in plan["steps"][:8]
            )
            message_lines = [
                f"Собрал кампанию **{campaign_name}** ({len(plan['steps'])} шагов).",
            ]
            if tg_name:
                message_lines.append(f"Таргет-группа: **{tg_name}** (id {tg_id}).")
            if summary:
                message_lines.append(summary)
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
    tg_id, tg_name = _resolve_target_group(ctx)
    flow = _build_fallback_flow(
        goal, brief, target_group_id=tg_id, target_group_name=tg_name, offer_text=offer_text,
    )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Fallback готов ({latency} ms)")
    message = (
        "Не получилось вытащить структуру кампании из запроса. Собрал базовый шаблон "
        "**Common → TargetGroup → SMS push**. Уточните аудиторию, оффер и каналы — пересоберу подробнее."
    )
    return await _finalize(ctx, flow=flow, message=message, mode="fallback", brief=brief)


# ── Резолв аудитории ──────────────────────────────────────────────────────────

async def _ask_audience_choice(ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis) -> AgentResult:
    """Спрашивает: порекомендовать таргет-группу или пользователь опишет аудиторию сам.

    Возвращает needs_input со stage=collect_audience — следующее сообщение
    останется в BuilderAgent (sticky-context).
    """
    product = brief.product or "продукт"
    await ctx.emit("step_started", detail=f"BuilderAgent: уточняю аудиторию для «{product}»")

    message = (
        f"Собираю кампанию для продвижения **{product}**. Кому будем продвигать?\n\n"
        "Я могу **порекомендовать таргет-группу** — подберу варианты на основе данных "
        "о продукте (для кого он next best offer, кто уже подключал, похожие продукты) "
        "с примерной оценкой охвата. Либо вы можете **сами описать аудиторию** — "
        "просто напишите, кого охватываем, и я закреплю это как таргет-группу."
    )
    # Одна кнопка — рекомендация; описать аудиторию пользователь может, просто
    # написав сообщение (мы в sticky-context collect_audience).
    actions = [
        ChatAction(
            id="recommend_audience",
            label="Порекомендуй таргет-группу",
            kind="runtime",
            payload={"goal": goal, "product": brief.product or ""},
        ),
    ]
    await ctx.emit("step_completed", detail="BuilderAgent: предложен выбор способа подбора аудитории")
    return AgentResult(
        assistant_message=message,
        actions=actions,
        status="needs_input",
        metadata={"brief": _brief_to_dict(brief), "stage": "collect_audience", "product": brief.product},
    )


async def _ask_audience_method(ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis) -> AgentResult:
    """Показывает меню способов подбора таргет-группы.

    Пользователь выбрал «порекомендовать» — теперь он выбирает КАК подбирать:
    NBO / look-alike по подключившим / look-alike по похожим / расспросить.
    Состав меню зависит от того, найден ли продукт в каталоге (look-alike по
    подключившим доступен только для продуктов из каталога).
    """
    from agents.audience_strategy import (
        METHOD_DESCRIPTIONS, method_label, resolve_audience_signals,
    )

    product = brief.product or "продукт"
    await ctx.emit("step_started", detail=f"BuilderAgent: меню подбора аудитории для «{product}»")
    signals = await resolve_audience_signals(product)
    methods = signals.menu_methods
    await ctx.emit(
        "step_completed",
        detail=f"Доступно методов: {len(methods)} (продукт в каталоге: {signals.found_in_catalog})",
        metadata={"methods": methods, "found_in_catalog": signals.found_in_catalog},
    )

    lines = [
        f"Как подобрать таргет-группу для **{product}**? Выберите способ:",
        "",
    ]
    actions: list[ChatAction] = []
    for m in methods:
        lines.append(f"- **{method_label(m)}** — {METHOD_DESCRIPTIONS.get(m, '')}")
        actions.append(ChatAction(
            id="audience_method",
            label=method_label(m),
            kind="runtime",
            payload={"method": m, "goal": goal, "product": brief.product or ""},
        ))
    lines.append("")
    lines.append("Либо просто опишите аудиторию своими словами — закреплю её как таргет-группу.")

    return AgentResult(
        assistant_message="\n".join(lines),
        actions=actions,
        status="needs_input",
        metadata={"brief": _brief_to_dict(brief), "stage": "collect_audience", "product": brief.product},
    )


async def _recommend_audience(
    ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis, method: str | None,
) -> AgentResult:
    """Делегирует подбор аудитории в SegmentsAgent выбранным методом.

    SegmentsAgent сгенерирует варианты сегментов под метод. Дальше пользователь
    закрепит вариант как таргет-группу (action assign_segment_as_target_group)
    и вернётся к сборке кампании.
    """
    from agents.agent_segments import execute as segments_execute

    product = brief.product or ctx.inputs.get("product") or "general"
    await ctx.emit(
        "step_started",
        detail=f"BuilderAgent → SegmentsAgent: подбор аудитории «{product}», метод={method or 'auto'}",
    )
    ctx.inputs["product"] = product
    ctx.inputs["campaign_goal"] = goal
    if method:
        ctx.inputs["audience_method"] = method
    # Маркер «вызвано из сборки кампании» — SegmentsAgent пометит свой ответ
    # stage=collect_audience, чтобы пользователь мог либо выбрать гипотезу
    # кнопкой, либо передумать и описать аудиторию своими словами (sticky → builder).
    ctx.inputs["from_builder"] = True
    return await segments_execute(ctx)


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

    # Quick-reply кнопки строго под актуальные missing-поля.
    quick_actions: list[ChatAction] = []
    if "channels" in brief.missing_critical:
        if "sms" not in brief.channels:
            quick_actions.append(_quick_reply("SMS-канал", "Используем SMS как основной канал коммуникации."))
        if "email" not in brief.channels:
            quick_actions.append(_quick_reply("Email-канал", "Используем Email как основной канал."))
        if "push" not in brief.channels:
            quick_actions.append(_quick_reply("Push-канал", "Используем мобильные Push-уведомления."))
    if "audience" in brief.missing_critical:
        quick_actions.append(_quick_reply("Все активные клиенты", "Аудитория — все активные клиенты за последние 30 дней."))
        quick_actions.append(_quick_reply("Отток за 30 дней", "Аудитория — клиенты, ушедшие в отток за последние 30 дней."))
    if "product" in brief.missing_critical and brief.goal and "удержани" not in (brief.goal or "").lower():
        quick_actions.append(_quick_reply("Тариф не важен", "Тариф не важен, главное — реализовать заявленную цель кампании."))

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


async def _auto_promote_segment(ctx: AgentContext, segment: dict[str, Any]) -> None:
    """Закрепляет переданный сегмент как таргет-группу: создаёт ЦГ в AdTarget mock
    (или подхватывает уже сопоставленную существующую), сохраняет артефакт
    target_group_draft в сессии и помещает его в ctx.artifacts, чтобы _resolve_target_group
    нашёл его на следующем шаге."""
    name = (
        segment.get("name")
        or segment.get("title")
        or segment.get("audience_description")
        or "Таргет-группа из сегмента"
    )
    matched = segment.get("matched_target_group") or {}
    existing_tg_id = matched.get("target_group_id") if isinstance(matched, dict) else None
    clients_count = matched.get("clients_count") if isinstance(matched, dict) else None
    if not clients_count:
        clients_count = segment.get("clients_count")

    try:
        if existing_tg_id and segment.get("is_existing_target_group"):
            tg_id = int(existing_tg_id)
            tg_name = matched.get("name") or name
            source = "matched_existing"
        else:
            await ctx.emit("step_started", detail=f"BuilderAgent: закрепляю сегмент «{str(name)[:60]}» как таргет-группу")
            result = await adtarget.create_target_group(
                name=str(name)[:120],
                criteria=segment.get("selection_criteria") or {},
                clients_count=int(clients_count) if isinstance(clients_count, int) else None,
                source_segment_id=str(segment.get("id") or segment.get("name") or "")[:64] or None,
            )
            tg_id = int(result.get("id"))
            tg_name = result.get("name") or name
            clients_count = result.get("clientsCount") or clients_count
            source = "created_from_segment"
            await ctx.emit(
                "step_completed",
                detail=f"таргет-группа #{tg_id} ({tg_name}) создана",
                metadata={"target_group_id": tg_id, "source": source},
            )
    except Exception as exc:
        logger.warning("auto-promote segment failed: %s", exc)
        return

    artifact_content = {
        "target_group_id": tg_id,
        "name": str(tg_name),
        "clients_count": int(clients_count) if isinstance(clients_count, (int, float)) and clients_count else None,
        "source": source,
        "source_segment": segment,
    }
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="target_group_draft",
        content_json=artifact_content,
        metadata_json={"source": source, "target_group_id": tg_id, "auto_promoted": True},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    if artifact:
        ctx.artifacts.append(artifact)


# ── Шаг выбора оффера ────────────────────────────────────────────────────────

def _resolve_offer_text(ctx: AgentContext) -> str | None:
    """Достаёт выбранный пользователем текст оффера из артефакта offer_choice."""
    # Если только что прилетел select_offer action — payload уже в inputs.
    direct = ctx.inputs.get("offer_text")
    if direct and isinstance(direct, str):
        return direct.strip() or None
    artifact = ctx.latest_artifact("offer_choice")
    if not artifact:
        return None
    content = artifact.get("content") or {}
    text = content.get("text")
    return str(text).strip() if text else None


async def _save_offer_choice(ctx: AgentContext) -> None:
    """select_offer action → сохраняет выбранный вариант как artifact offer_choice.

    Сохраняем в начале execute, до сборки flow, чтобы _resolve_offer_text
    нашёл выбор и подставил в Communication.
    """
    payload = (ctx.action.payload if ctx.action else None) or {}
    text = (
        ctx.inputs.get("offer_text")
        or payload.get("variant_text")
        or payload.get("offer_text")
        or ""
    )
    if not text:
        return
    content = {
        "id": ctx.inputs.get("offer_variant_id") or payload.get("variant_id"),
        "text": str(text),
        "product": ctx.inputs.get("product") or payload.get("product"),
        "channel": ctx.inputs.get("channel") or payload.get("channel"),
    }
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="offer_choice",
        content_json=content,
        metadata_json={"variant_id": content["id"], "length_chars": len(content["text"])},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)
    if artifact:
        ctx.artifacts.append(artifact)
    await ctx.emit(
        "step_completed",
        detail=f"Оффер выбран ({len(content['text'])} символов) — собираю кампанию",
        metadata={"variant_id": content["id"]},
    )


async def _ask_offer_decision(
    ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis,
) -> AgentResult:
    """Спрашивает: сгенерировать варианты оффера или собрать с типовым текстом."""
    product = brief.product or "продукт"
    channel = (brief.channels[0] if brief.channels else "sms").lower()
    tg_id, tg_name = _resolve_target_group(ctx)
    audience = tg_name or (brief.audience or {}).get("description") or "выбранная аудитория"

    await ctx.emit(
        "step_started",
        detail=f"BuilderAgent: запрашиваю решение по офферу (канал {channel})",
    )

    message = (
        f"Бриф готов: **{product}** для **{audience}**, канал **{channel.upper()}**. "
        "Сгенерировать **2-3 варианта оффера** под этот канал и аудиторию или собрать "
        "кампанию с **типовым текстом**?"
    )
    actions = [
        ChatAction(
            id="generate_offers",
            label="Сгенерируй варианты оффера",
            kind="runtime",
            payload={"goal": goal, "product": product, "channel": channel, "audience": audience},
        ),
        ChatAction(
            id="skip_offer_generation",
            label="Собрать с типовым текстом",
            kind="runtime",
            payload={"goal": goal, "product": product},
        ),
    ]
    return AgentResult(
        assistant_message=message,
        actions=actions,
        status="needs_input",
        metadata={
            "brief": _brief_to_dict(brief),
            "stage": "collect_offer",
            "product": product,
            "channel": channel,
        },
    )


async def _generate_offers(
    ctx: AgentContext, *, goal: str, brief: CampaignBriefAnalysis,
) -> AgentResult:
    """Делегирует генерацию оффера в OfferAgent."""
    from agents.agent_offer import execute as offer_execute

    product = brief.product or ctx.inputs.get("product") or "продукт"
    channel = (
        ctx.inputs.get("channel")
        or (brief.channels[0] if brief.channels else "sms")
    ).lower()
    tg_id, tg_name = _resolve_target_group(ctx)
    audience = (
        ctx.inputs.get("audience")
        or tg_name
        or (brief.audience or {}).get("description")
        or "общая аудитория"
    )
    ctx.inputs["product"] = product
    ctx.inputs["channel"] = channel
    ctx.inputs["audience"] = audience
    ctx.inputs["target_group_name"] = tg_name or audience
    ctx.inputs["from_builder"] = True
    await ctx.emit(
        "step_started",
        detail=f"BuilderAgent → OfferAgent: продукт «{product}», канал {channel}",
    )
    return await offer_execute(ctx)


def _resolve_target_group(ctx: AgentContext) -> tuple[int | None, str | None]:
    """Возвращает (target_group_id, target_group_name) из последнего
    `target_group_draft` артефакта сессии. Артефакт создаёт RuntimeAgent
    через action `assign_segment_as_target_group` — после этого все
    последующие сборки кампании в сессии используют эту таргет-группу.
    """
    artifact = ctx.latest_artifact("target_group_draft")
    if not artifact:
        return None, None
    content = artifact.get("content") or {}
    tg_id = content.get("target_group_id") or content.get("id")
    tg_name = content.get("name")
    try:
        tg_id_int = int(tg_id) if tg_id is not None else None
    except (TypeError, ValueError):
        tg_id_int = None
    return tg_id_int, (str(tg_name) if tg_name else None)


def _build_fallback_flow(
    goal: str,
    brief: CampaignBriefAnalysis | None,
    *,
    target_group_id: int | None = None,
    target_group_name: str | None = None,
    offer_text: str | None = None,
) -> dict[str, Any]:
    campaign_name = (brief.product if brief and brief.product else None) or _truncate(goal, 60) or "Новая кампания"
    audience_hint = ""
    if brief and brief.audience:
        audience_hint = str(brief.audience.get("description") or "")
    content_type = "SmsContent"
    if brief and brief.channels:
        mapping = {"sms": "SmsContent", "email": "EmailContent", "push": "PushContent", "ussd": "UssdContent"}
        content_type = mapping.get(brief.channels[0], "SmsContent")
    common = make_common_activity(campaign_name)
    target = make_target_group_activity(target_group_id=int(target_group_id or 1))
    if target_group_name:
        target["name"] = target_group_name
    elif audience_hint:
        target["name"] = f"Аудитория — {_truncate(audience_hint, 40)}"
    message_text = offer_text or f"Привет! Специальное предложение по «{campaign_name}». Подробности уточняйте."
    push = make_push_communication_activity(
        channel_id=1,
        content_type=content_type,
        message_text=message_text,
    )
    return assemble_flow([common, target, push])

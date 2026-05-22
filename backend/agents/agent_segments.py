"""SegmentsAgent — генерация гипотез сегментов аудитории.

Перед обращением к LLM-сегментатору агент собирает сигналы по продукту
(NBO / подключившие / похожие продукты — см. agents/audience_strategy.py)
и кладёт их в запрос. Так гипотезы получаются обоснованными: каждая
ссылается на конкретный метод подбора.

Если по продукту НЕТ ни одного сигнала (нет NBO, нет подключивших, нет
похожих продуктов) — агент не выдумывает аудиторию, а возвращает
needs_input с вопросами о свойствах продукта.

Агент переиспользуется в двух режимах:
  • standalone — intent suggest_segments;
  • внутри сборки кампании — BuilderAgent делегирует сюда (см. agent_builder),
    передавая product/campaign_goal через ctx.inputs.
"""

from __future__ import annotations

import re
import time

from agents.audience_strategy import resolve_audience_signals
from agents.base import AgentContext, AgentResult
from agents.segment_agent import suggest_segments
from schemas import ChatAction, SegmentSuggestRequest


NAME = "segments"
DESCRIPTION = "Предлагает 2-3 гипотезы сегментов под продукт и цель кампании; собирает сигналы NBO/look-alike из каталога."
SUPPORTED_INTENTS = ("suggest_segments",)


async def execute(ctx: AgentContext) -> AgentResult:
    message = ctx.message
    # Продукт может прийти явно из BuilderAgent (ctx.inputs) либо извлекаться из текста.
    product = (
        ctx.inputs.get("product")
        or _extract_product(message)
        or "general"
    )
    campaign_goal = ctx.inputs.get("campaign_goal") or message or "Подбор аудитории"
    from_builder = bool(ctx.inputs.get("from_builder"))
    sticky_stage = ctx.inputs.get("sticky_stage")
    # Stage, который пометим в ответе с гипотезами: если сегментация идёт внутри
    # сборки кампании — collect_audience, чтобы пользователь мог передумать и
    # описать аудиторию своими словами (sticky вернёт его в BuilderAgent).
    hypotheses_stage = "collect_audience" if from_builder else None

    # Ответ на запрос свойств продукта (sticky collect_product_properties):
    # стратегию заново не резолвим (данных в каталоге всё равно нет) —
    # строим гипотезы сразу из описания пользователя.
    if sticky_stage == "collect_product_properties":
        return await _suggest_from_description(
            ctx, product=ctx.inputs.get("product") or product,
            description=message, hypotheses_stage=hypotheses_stage,
        )

    # 1. Собираем сигналы по продукту (NBO / подключившие / похожие продукты).
    await ctx.emit("step_started", detail=f"SegmentsAgent: сбор сигналов по продукту «{product}»")
    signals = await resolve_audience_signals(product)
    await ctx.emit(
        "step_completed",
        detail=f"Методы подбора: {', '.join(signals.methods)}",
        metadata={"methods": signals.methods, "found_in_catalog": signals.found_in_catalog},
    )

    # 2. Нет ни одного сигнала → не выдумываем аудиторию, расспрашиваем о продукте.
    if not signals.has_data:
        return _ask_product_properties(ctx, product=product, signals=signals)

    # 3. Есть сигналы → строим гипотезы сегментатором, передав сигналы в запрос.
    await ctx.emit("step_started", detail="SegmentsAgent: генерирую гипотезы сегментов")
    started = time.perf_counter()
    try:
        response = await suggest_segments(SegmentSuggestRequest(
            product=product,
            campaign_goal=campaign_goal,
            current_campaign_context={"audience_signals": signals.llm_context()},
        ))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=f"segment_agent failed: {str(exc)[:200]}")
        return AgentResult(
            assistant_message=f"Не удалось сгенерировать сегменты: {str(exc)[:200]}",
            status="error",
        )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Гипотез: {len(response.hypotheses)}", metadata={"latency_ms": latency})

    return await _render_hypotheses(
        ctx,
        response=response,
        intro=signals.human_summary(),
        methods=signals.methods,
        hypotheses_stage=hypotheses_stage,
    )


async def _suggest_from_description(
    ctx: AgentContext, *, product: str, description: str, hypotheses_stage: str | None,
) -> AgentResult:
    """Строит гипотезы из свободного описания продукта/аудитории.

    Вызывается, когда пользователь ответил на запрос свойств продукта
    (sticky collect_product_properties): данных в каталоге нет, поэтому
    сегментатор работает в режиме compose_new по тексту пользователя.
    """
    await ctx.emit("step_started", detail="SegmentsAgent: гипотезы по описанию продукта от пользователя")
    started = time.perf_counter()
    try:
        response = await suggest_segments(SegmentSuggestRequest(
            product=product,
            campaign_goal=description or "Подбор аудитории по описанию продукта",
            strategy="compose_new",
        ))
    except Exception as exc:
        await ctx.emit("step_completed", status="error", detail=f"segment_agent failed: {str(exc)[:200]}")
        return AgentResult(
            assistant_message=f"Не удалось сгенерировать сегменты: {str(exc)[:200]}",
            status="error",
        )
    latency = int((time.perf_counter() - started) * 1000)
    await ctx.emit("step_completed", detail=f"Гипотез: {len(response.hypotheses)}", metadata={"latency_ms": latency})

    return await _render_hypotheses(
        ctx,
        response=response,
        intro=f"По вашему описанию продукта «{product}» подобрал варианты аудитории:",
        methods=["compose_new"],
        hypotheses_stage=hypotheses_stage,
    )


async def _render_hypotheses(
    ctx: AgentContext, *, response, intro: str, methods: list[str], hypotheses_stage: str | None,
) -> AgentResult:
    """Рендерит карточки гипотез + кнопки. Общий код для всех путей SegmentsAgent."""
    if not response.hypotheses:
        return AgentResult(
            assistant_message=response.summary or "Не удалось предложить сегменты для этого запроса.",
            status="ok",
        )

    lines = [intro, ""]
    for h in response.hypotheses[:3]:
        name = h.name or h.title or "Без названия"
        lines.append(f"### {name}")
        lines.append("")
        desc = h.audience_description or h.description
        reason = h.relevance_reason or h.rationale
        if desc:
            lines.append(f"- **Аудитория:** {desc}")
        if reason:
            lines.append(f"- **Зачем:** {reason}")
        if h.estimated_reach_label:
            reach = f"**Примерный охват:** {h.estimated_reach_label}"
            mtg = h.matched_target_group
            if mtg and mtg.clients_count:
                reach += f" (≈{mtg.clients_count:,} клиентов)".replace(",", " ")
            lines.append(f"- {reach}")
        if h.risk_or_limitation:
            lines.append(f"- **Ограничения:** {h.risk_or_limitation}")
        lines.append("")
    lines.append("_Выберите вариант и закрепите его как таргет-группу — дальше можно собрать кампанию._")
    message_text = "\n".join(lines).rstrip()

    primary = response.hypotheses[0]
    artifact_id = await ctx.store.save_artifact(
        session_id=ctx.session_id,
        artifact_type="segment_draft",
        content_json=primary.model_dump(),
        metadata_json={"hypotheses_count": len(response.hypotheses), "methods": methods},
        source_run_id=ctx.run_id,
    )
    artifact = await ctx.store.get_artifact(artifact_id)

    primary_payload = primary.model_dump()
    actions = [
        ChatAction(id="save_segment", label="Сохранить сегмент", kind="save",
                   payload={"segment": primary_payload}),
        ChatAction(id="assign_segment_as_target_group", label="Назначить таргет-группой",
                   kind="runtime", payload={"segment": primary_payload}),
        ChatAction(id="build_campaign_from_segment", label="Собрать кампанию для таргет-группы",
                   kind="build", payload={"segment": primary_payload}),
    ]
    metadata: dict = {"hypotheses": len(response.hypotheses), "methods": methods}
    # Если сегментация идёт внутри сборки кампании — помечаем stage, чтобы
    # пользователь мог передумать и описать аудиторию своими словами.
    if hypotheses_stage:
        metadata["stage"] = hypotheses_stage
    return AgentResult(
        assistant_message=message_text,
        artifacts=[artifact] if artifact else [],
        actions=actions,
        metadata=metadata,
    )


def _ask_product_properties(ctx: AgentContext, *, product: str, signals) -> AgentResult:
    """Возвращает needs_input с вопросами о свойствах продукта.

    Срабатывает, когда по продукту нет ни NBO, ни подключивших, ни похожих
    продуктов — выдумывать аудиторию нельзя, нужно расспросить пользователя.
    """
    parts = [
        signals.human_summary(),
        "",
        "Чтобы предложить таргет-группу, опишите продукт:",
        "- Кому он в первую очередь полезен (какая потребность закрывается)?",
        "- Ценовой сегмент: бюджетный, средний, премиальный?",
        "- Есть ли ограничения (тип устройства, тариф, регион, возраст)?",
        "- С каким уже существующим продуктом он ближе всего по аудитории?",
        "",
        "Либо опишите аудиторию напрямую — и я закреплю её как таргет-группу.",
    ]
    return AgentResult(
        assistant_message="\n".join(parts),
        status="needs_input",
        metadata={
            "stage": "collect_product_properties",
            "product": product,
            "methods": signals.methods,
        },
    )


def _extract_product(message: str) -> str | None:
    """Грубое извлечение продукта из текста для standalone-запроса.

    В сценарии сборки кампании продукт приходит явно через ctx.inputs —
    эта эвристика нужна только когда сегментацию зовут напрямую.
    """
    if not message:
        return None
    # 1. Явное «продукт: X» / «product: X».
    for pattern in (r"продукт[:\s]+([^.,;\n]+)", r"product[:\s]+([^.,;\n]+)"):
        m = re.search(pattern, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    # 2. Продуктовые существительные: «тариф Семейный», «пакет данных 5 ГБ», «услуга X».
    m = re.search(
        r"\b(тариф\w*|пакет\w*|услуг\w*|подписк\w*|опци\w*)\s+([^.,;\n]{2,60})",
        message,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(1)} {m.group(2)}".strip()
    return None
